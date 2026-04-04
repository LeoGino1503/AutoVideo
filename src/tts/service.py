from src.utils.schemas import MicroStory, PipelinePaths, MicroScene
from src.utils.helper import _ensure_dirs
from src.tts.edge_tts_synth import synth_voice_for_text, _make_silence_mp3, _make_silence_wav
from pathlib import Path
import time
import shutil

def _synth_audio_for_story(
    story: MicroStory,
    paths: PipelinePaths,
    *,
    tts_enabled: bool,
) -> list[Path]:
    _ensure_dirs(paths)
    audio_paths: list[Path] = []
    for idx, scene in enumerate(story.scenes):
        if not tts_enabled:
            audio_paths.append(_make_silent_audio(paths.audio_dir, fb, idx))
            continue
        out = paths.audio_dir / f"scene_{idx:02d}.mp3"
        audio_paths.append(
            synth_voice_for_text(
                text=scene.narration,
                out_path=out,
                tts_enabled=tts_enabled,
            )
        )
        time.sleep(0.6)
        if (idx + 1) % 10 == 0:
            time.sleep(5.0)
    return audio_paths


def _make_silent_audio(audio_dir: Path, duration_seconds: float, idx: int) -> Path:
    target = audio_dir / f"scene_{idx:02d}_silent.mp3"
    if target.exists() and target.stat().st_size > 0:
        return target
    wav_alt = target.with_suffix(".wav")
    if wav_alt.exists() and wav_alt.stat().st_size > 0:
        return wav_alt
    return _write_silent_track(target, duration_seconds)

def _write_silent_track(out_path: Path, duration_seconds: float) -> Path:
    """
    Write a playable silence track. Prefers mp3 via ffmpeg; otherwise wav (stdlib).
    Never returns a 0-byte file.
    """
    out_path = Path(out_path)
    if shutil.which("ffmpeg"):
        mp3 = out_path if out_path.suffix.lower() == ".mp3" else out_path.with_suffix(".mp3")
        p = _make_silence_mp3(mp3, duration_seconds)
        if p.exists() and p.stat().st_size > 0:
            return p
    wav = out_path if out_path.suffix.lower() == ".wav" else out_path.with_suffix(".wav")
    return _make_silence_wav(wav, duration_seconds)

def _sync_micro_story_durations_from_audio(
    paths: PipelinePaths,
    *,
    save: bool = True,
) -> MicroStory:
    """
    Measure each scene's audio under ``paths.audio_dir`` and set in-memory ``duration_seconds``
    for render/TTS alignment. ``micro_story.json`` is still saved **without** ``duration_seconds``
    (see ``save_micro_story``).
    """
    story = _load_micro_story(paths)
    new_scenes: list[MicroScene] = []
    for idx, scene in enumerate(story.scenes):
        ap = _scene_audio_path_for_index(paths, idx)
        if ap is None:
            new_scenes.append(scene)
            continue
        try:
            dur = _probe_audio_duration_seconds(ap)
            dur = max(0.5, min(dur, 600.0))
            new_scenes.append(scene.model_copy(update={"duration_seconds": round(dur, 2)}))
        except Exception:
            new_scenes.append(scene)

    updated = story.model_copy(update={"scenes": new_scenes})
    if save:
        _save_micro_story(paths, updated)
    return updated