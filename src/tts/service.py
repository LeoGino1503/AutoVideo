"""TTS: im lặng, Edge, ElevenLabs — một module duy nhất."""
from __future__ import annotations

import asyncio
import logging
import random
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path
import edge_tts
from src.utils.config_loader import cfg_bool, cfg_int, cfg_raw, cfg_str
from src.video.ffmpeg import resolve_ffmpeg_executable
from src.utils.helper import ensure_dirs
from src.utils.schemas import MicroStory, PipelinePaths
from src.tts.elevenlabs_synth import elevenlabs_ready, synth_elevenlabs_to_path
from src.tts.google_chirp3_synth import google_chirp3_ready, synth_google_chirp3_to_path

log = logging.getLogger(__name__)


def _tts_provider() -> str:
    return cfg_str("tts", "provider", default="auto").strip().lower()


def _tts_default_float(key: str, default: float) -> float:
    v = cfg_raw("tts", "default", key, default=None)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _synth_audio_for_story(
    story: MicroStory,
    paths: PipelinePaths,
    *,
    tts_enabled: bool,
) -> list[Path]:
    ensure_dirs(paths)
    silent = _tts_default_float("silent_duration", 3.0)
    audio_paths: list[Path] = []
    for idx, scene in enumerate(story.scenes):
        out = paths.audio_dir / f"scene_{idx:02d}.mp3"
        audio_paths.append(
            synth_voice_for_text(
                text=scene.narration,
                out_path=out,
                tts_enabled=tts_enabled,
                fallback_duration_seconds=silent,
            )
        )
        time.sleep(0.6)
        if (idx + 1) % 10 == 0:
            time.sleep(5.0)
    return audio_paths


def _make_silence_wav(out_path: Path, duration_seconds: float, *, sample_rate: int = 48_000) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration_seconds = max(0.05, float(duration_seconds))
    nframes = int(duration_seconds * sample_rate)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * nframes)
    return out_path


def _make_silence_mp3(out_path: Path, duration_seconds: float) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ff = resolve_ffmpeg_executable()
    if not ff:
        return _make_silence_wav(out_path.with_suffix(".wav"), duration_seconds)
    cmd = [
        ff,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=mono",
        "-t",
        str(float(duration_seconds)),
        "-q:a",
        "9",
        "-acodec",
        "libmp3lame",
        str(out_path),
    ]
    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    log.warning("ffmpeg silence mp3 failed; using wav fallback for %s", out_path)
    return _make_silence_wav(out_path.with_suffix(".wav"), duration_seconds)


def write_silent_track(out_path: Path, duration_seconds: float) -> Path:
    """Ghi track im lặng (ưu tiên mp3 qua ffmpeg, không thì wav)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if resolve_ffmpeg_executable():
        mp3 = out_path if out_path.suffix.lower() == ".mp3" else out_path.with_suffix(".mp3")
        p = _make_silence_mp3(mp3, duration_seconds)
        if p.exists() and p.stat().st_size > 0:
            return p
    wav = out_path if out_path.suffix.lower() == ".wav" else out_path.with_suffix(".wav")
    return _make_silence_wav(wav, duration_seconds)


def _chunk_for_tts(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return []
    max_chars = cfg_int("tts", "default", "max_chunk_chars", default=2400)
    return [t[i : i + max_chars] for i in range(0, len(t), max_chars)]


def _ffmpeg_concat_mp3(part_paths: list[Path], out_path: Path) -> bool:
    ff = resolve_ffmpeg_executable()
    if not part_paths or not ff:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / f"{out_path.stem}_concat.txt"
    try:
        with list_path.open("w", encoding="utf-8") as f:
            for p in part_paths:
                ap = str(p.resolve()).replace("'", "'\\''")
                f.write(f"file '{ap}'\n")
        cmd = [
            ff,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(out_path),
        ]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    finally:
        try:
            list_path.unlink(missing_ok=True)
        except OSError:
            pass


async def _save_tts_chunks(chunks: list[str], part_paths: list[Path], voice_name: str) -> None:
    for i, (chunk, path) in enumerate(zip(chunks, part_paths)):
        com = edge_tts.Communicate(chunk, voice=voice_name)
        await com.save(str(path))
        if i < len(chunks) - 1:
            await asyncio.sleep(0.3)


def _is_likely_403(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "403" in s or "forbidden" in s or "no audio was received" in s


def _run_one_tts_attempt(
    chunks: list[str],
    out_path: Path,
    voice_name: str,
    *,
    min_part_bytes: int,
    min_valid_bytes: int,
) -> None:
    part_paths: list[Path] = []
    if len(chunks) == 1:

        async def _one() -> None:
            await edge_tts.Communicate(chunks[0], voice=voice_name).save(str(out_path))

        asyncio.run(_one())
    else:
        part_paths = [
            out_path.parent / f"{out_path.stem}_part{i:03d}.mp3" for i in range(len(chunks))
        ]
        try:
            asyncio.run(_save_tts_chunks(chunks, part_paths, voice_name))
            for p in part_paths:
                if not p.exists() or p.stat().st_size < min_part_bytes:
                    raise RuntimeError(f"TTS part empty or too small: {p}")
            if not _ffmpeg_concat_mp3(part_paths, out_path):
                log.warning(
                    "ffmpeg missing or concat failed; using first TTS chunk only for %s",
                    out_path.name,
                )
                shutil.copyfile(part_paths[0], out_path)
        finally:
            for p in part_paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass

    if not out_path.exists() or out_path.stat().st_size < min_valid_bytes:
        sz = out_path.stat().st_size if out_path.exists() else 0
        raise RuntimeError(f"TTS output missing or too small ({sz} bytes, min {min_valid_bytes})")


def synth_voice_for_text(
    text: str,
    out_path: Path,
    *,
    voice: str | None = None,
    fallback_duration_seconds: float | None = None,
    tts_enabled: bool | None = None,
) -> Path:
    """
    Một scene: ElevenLabs (nếu bật + key), sau đó Edge; tắt TTS / lỗi → silence.
    ``tts_enabled is None`` → ``tts.enabled`` và env ``TTS_ENABLED``.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    silent = (
        fallback_duration_seconds
        if fallback_duration_seconds is not None
        else _tts_default_float("silent_duration", 3.0)
    )
    enabled = (
        cfg_bool("tts", "enabled", env_legacy="TTS_ENABLED", default=True)
        if tts_enabled is None
        else tts_enabled
    )
    if not enabled:
        return write_silent_track(out_path, silent)

    voice_name = (
        str(voice).strip()
        if voice and str(voice).strip()
        else cfg_str("tts", "default", "voice", default="vi-VN-HoaiMyNeural")
    )
    min_valid = cfg_int("tts", "default", "min_valid_tts_output_bytes", default=1000)
    min_part = cfg_int("tts", "default", "min_part_bytes", default=200)
    max_retries = cfg_int("tts", "default", "max_retries", default=3)

    chunks = _chunk_for_tts(text)
    if not chunks:
        return write_silent_track(out_path, silent)

    provider = _tts_provider()
    if provider == "elevenlabs":
        if elevenlabs_ready():
            out_path.unlink(missing_ok=True)
            if synth_elevenlabs_to_path(text, out_path):
                return out_path
            log.info("ElevenLabs failed; falling back to Edge TTS for %s", out_path.name)
        else:
            log.info("ElevenLabs not ready; falling back to Edge TTS for %s", out_path.name)
    elif provider in {"google", "google_chirp3", "chirp3"}:
        if google_chirp3_ready():
            out_path.unlink(missing_ok=True)
            if synth_google_chirp3_to_path(text, out_path):
                return out_path
            log.info("Google Chirp3 failed; falling back to Edge TTS for %s", out_path.name)
        else:
            log.info("Google Chirp3 not ready; falling back to Edge TTS for %s", out_path.name)
    else:
        # auto mode: ElevenLabs first, then Google Chirp3, then Edge.
        if elevenlabs_ready():
            out_path.unlink(missing_ok=True)
            if synth_elevenlabs_to_path(text, out_path):
                return out_path
            log.info(
                "ElevenLabs unavailable or failed; trying Google Chirp3 for %s",
                out_path.name,
            )
        if google_chirp3_ready():
            out_path.unlink(missing_ok=True)
            if synth_google_chirp3_to_path(text, out_path):
                return out_path
            log.info("Google Chirp3 unavailable or failed; using Edge TTS for %s", out_path.name)

    for attempt in range(max_retries):
        try:
            out_path.unlink(missing_ok=True)
            _run_one_tts_attempt(
                chunks,
                out_path,
                voice_name,
                min_part_bytes=min_part,
                min_valid_bytes=min_valid,
            )
            if out_path.exists() and out_path.stat().st_size >= min_valid:
                return out_path
        except Exception as exc:
            log.warning(
                "TTS attempt %s/%s failed for %s: %s",
                attempt + 1,
                max_retries,
                out_path.name,
                exc,
            )
            if attempt < max_retries - 1:
                if _is_likely_403(exc):
                    delay = (2**attempt) + random.uniform(0.5, 1.5)
                    time.sleep(delay)
                else:
                    time.sleep(1.0)

    return write_silent_track(out_path, silent)
