from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from moviepy.audio.AudioClip import AudioClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy import concatenate_audioclips, concatenate_videoclips
from moviepy.video.VideoClip import ColorClip, ImageClip
from moviepy.video.VideoClip import VideoClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
from moviepy.video.fx.FadeIn import FadeIn
from moviepy.video.fx.FadeOut import FadeOut
from moviepy.video.fx.Loop import Loop
from moviepy.video.io.VideoFileClip import VideoFileClip

from audio_probe import probe_audio_duration_seconds
from src.utils.schemas import MicroScene


def _scene_render_duration(scene: MicroScene, audio_path: Path) -> float:
    if scene.duration_seconds is not None:
        return float(scene.duration_seconds)
    ap = Path(audio_path)
    if ap.exists() and ap.stat().st_size > 0:
        try:
            d = probe_audio_duration_seconds(ap)
            return max(0.5, min(float(d), 600.0))
        except Exception:
            pass
    words = max(1, len(scene.narration.split()))
    return max(2.0, min(10.0, round(words / 2.4, 2)))


def _ffmpeg_mux_video_and_audio(
    video_in: Path,
    audio_in: Path,
    out: Path,
    *,
    audio_bitrate: str = "192k",
) -> bool:
    """Mux pre-encoded video + WAV (or other audio) into final MP4. Video stream copied."""
    if shutil.which("ffmpeg") is None:
        return False
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_in),
        "-i",
        str(audio_in),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        str(out),
    ]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def _try_load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def _render_caption_image(text: str, *, width: int, height: int) -> Image.Image:
    """
    Create a caption overlay as a PIL RGBA image.
    """
    # Transparent background
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 40
    font = _try_load_font(58)

    # Wrap text into multiple lines
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for w in words:
        test = " ".join(current + [w])
        if draw.textlength(test, font=font) <= width - pad * 2 and len(" ".join(current + [w])) <= 24:
            current.append(w)
        else:
            if current:
                lines.append(" ".join(current))
            current = [w]
    if current:
        lines.append(" ".join(current))
    lines = lines[:3]  # keep short for readability

    # Rectangle background for readability
    # Use bottom third
    rect_top = int(height * 0.62)
    rect_bottom = int(height * 0.93)
    draw.rounded_rectangle(
        [(20, rect_top), (width - 20, rect_bottom)],
        radius=30,
        fill=(0, 0, 0, 140),
    )

    y = rect_top + 10
    for line in lines:
        # center horizontally
        line_w = draw.textlength(line, font=font)
        x = int((width - line_w) / 2)
        draw.text((x, y), line, font=font, fill=(245, 245, 255, 255))
        y += 70

    return img


def _make_silence_audio(duration: float, fps: int = 44100) -> AudioClip:
    return AudioClip(lambda t: 0.0, duration=duration, fps=fps)


def _base_visual_for_scene(
    media_path: Path,
    duration: float,
    *,
    target_w: int,
    target_h: int,
) -> tuple[VideoClip, Optional[VideoFileClip]]:
    """
    Build a time-scaled visual (image or stock video), cropped to cover 9:16, with light Ken Burns zoom.
    Returns (composite_clip, optional VideoFileClip to close after export).
    """
    media_path = Path(media_path)
    zoom_start = 1.00
    zoom_end = 1.08

    vfile: Optional[VideoFileClip] = None
    if media_path.suffix.lower() == ".mp4":
        # Strip source audio so final mix is only narration (edge-tts) per scene.
        vfile = VideoFileClip(str(media_path)).without_audio()
        d_src = float(vfile.duration or 0.0)
        if d_src <= 0:
            vfile.close()
            vfile = None
            base = ColorClip(size=(target_w, target_h), color=(0, 0, 0)).with_duration(duration)
        elif d_src + 1e-6 < duration:
            base = vfile.with_effects([Loop(duration=duration)])
        elif d_src > duration + 1e-6:
            base = vfile.subclipped(0, duration)
        else:
            base = vfile.with_duration(duration)
        iw, ih = base.size
    else:
        base = ImageClip(str(media_path)).with_duration(duration)
        iw, ih = base.size

    cover_scale = max(target_w / iw, target_h / ih)
    cover_w, cover_h = int(iw * cover_scale), int(ih * cover_scale)
    cover_scaled = base.resized(new_size=(cover_w, cover_h))
    canvas = ColorClip(size=(target_w, target_h), color=(0, 0, 0)).with_duration(duration)

    def zoomed_new_size(t: float) -> tuple[int, int]:
        s = zoom_start + (zoom_end - zoom_start) * (t / max(duration, 1e-6))
        return (max(1, int(cover_w * s)), max(1, int(cover_h * s)))

    zoomed = cover_scaled.resized(zoomed_new_size)
    comp = CompositeVideoClip(
        [
            canvas,
            zoomed.with_position(("center", "center")),
        ],
        size=(target_w, target_h),
    )
    return comp, vfile


def render_micro_story_video(
    *,
    scene_paths: list[Path],
    audio_paths: list[Path],
    scenes: list[MicroScene],
    out_path: Path,
    fps: int = 24,
    target_w: int = 1080,
    target_h: int = 1920,
) -> Path:
    """
    Render a 9:16 MP4 from image/video + audio micro-scenes.
    Each scene is 2–3 seconds; includes light Ken Burns zoom and caption overlay.
    Stock video scenes use ``.mp4`` (e.g. from Pexels Videos API).
    """
    if not (len(scene_paths) == len(scenes) == len(audio_paths)):
        raise ValueError("scene_paths, audio_paths, scenes must have same length.")

    scene_clips: list[VideoClip] = []
    audio_clips = []
    video_sources: list[VideoFileClip] = []

    for idx, (media_path, audio_path, scene) in enumerate(zip(scene_paths, audio_paths, scenes)):
        duration = _scene_render_duration(scene, audio_path)

        if not Path(media_path).exists():
            raise FileNotFoundError(media_path)

        video, vsrc = _base_visual_for_scene(
            media_path,
            duration,
            target_w=target_w,
            target_h=target_h,
        )
        if vsrc is not None:
            video_sources.append(vsrc)

        # Caption overlay
        caption_img = _render_caption_image(scene.onScreenText, width=target_w, height=target_h)
        caption_path = Path(out_path).parent / f"_caption_{idx:02d}.png"
        caption_path.parent.mkdir(parents=True, exist_ok=True)
        caption_img.save(caption_path)
        caption_clip = ImageClip(str(caption_path)).with_duration(duration)
        video = CompositeVideoClip([video, caption_clip], size=(target_w, target_h))

        # Light fade for attention
        video = video.with_effects([FadeIn(0.12), FadeOut(0.12)])

        scene_clips.append(video)

        # Audio
        try:
            if Path(audio_path).exists() and Path(audio_path).stat().st_size > 0:
                a = AudioFileClip(str(audio_path))
                if a.duration is None or a.duration <= 0:
                    a = _make_silence_audio(duration)
                elif a.duration > duration:
                    a = a.with_duration(duration)
                else:
                    # pad by adding silence
                    pad = duration - a.duration
                    if pad > 0.02:
                        a = concatenate_audioclips([a, _make_silence_audio(pad)])
            else:
                a = _make_silence_audio(duration)
        except Exception:
            a = _make_silence_audio(duration)
        audio_clips.append(a)

    final_video: VideoClip = concatenate_videoclips(scene_clips, method="chain")
    final_audio = concatenate_audioclips(audio_clips)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_video = out_path.parent / f"{out_path.stem}_video_only.mp4"
    tmp_audio = out_path.parent / f"{out_path.stem}_full_narration.wav"

    def _close_sources() -> None:
        for v in video_sources:
            try:
                v.close()
            except Exception:
                pass

    if shutil.which("ffmpeg"):
        try:
            final_video.write_videofile(
                str(tmp_video),
                fps=fps,
                codec="libx264",
                audio=False,
                preset="medium",
                threads=2,
                logger=None,
            )
            final_audio.write_audiofile(
                str(tmp_audio),
                fps=44100,
                codec="pcm_s16le",
                logger=None,
            )
            if not _ffmpeg_mux_video_and_audio(tmp_video, tmp_audio, out_path):
                raise RuntimeError(
                    f"ffmpeg mux failed: {tmp_video} + {tmp_audio} -> {out_path}. "
                    "Try running ffmpeg manually or check codecs."
                )
        finally:
            _close_sources()
            try:
                final_video.close()
                final_audio.close()
            except Exception:
                pass
            try:
                tmp_video.unlink(missing_ok=True)
                tmp_audio.unlink(missing_ok=True)
            except OSError:
                pass
        return out_path

    combined = final_video.with_audio(final_audio)
    try:
        combined.write_videofile(
            str(out_path),
            fps=fps,
            codec="libx264",
            audio_codec="aac",
            preset="medium",
            threads=2,
            logger=None,
        )
    finally:
        _close_sources()
        try:
            combined.close()
        except Exception:
            pass

    return out_path

