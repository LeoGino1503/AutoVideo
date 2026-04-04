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

from src.utils.config_loader import cfg_bool

from tts.elevenlabs_synth import elevenlabs_ready, synth_elevenlabs_to_path

log = logging.getLogger(__name__)

# Edge TTS web API effectively caps payload size; chunk to stay under limits.
_TTS_CHUNK_CHARS = 2400

MAX_RETRIES = 3
# Reject suspiciously small Edge output (likely error HTML / truncated).
MIN_VALID_TTS_OUTPUT_BYTES = 1000
# Per-chunk floor (short phrase can still be a small but valid MP3).
_MIN_PART_BYTES = 200

_VI_VOICES_ROTATION = (
    "vi-VN-HoaiMyNeural",
    "vi-VN-NamMinhNeural",
)


def _make_silence_wav(out_path: Path, duration_seconds: float, *, sample_rate: int = 48_000) -> Path:
    """16-bit mono PCM WAV — always playable without ffmpeg."""
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
    if shutil.which("ffmpeg") is None:
        return _make_silence_wav(out_path.with_suffix(".wav"), duration_seconds)
    cmd = [
        "ffmpeg",
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


def _normalize_tts_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _pick_voice(text: str, voice: str | None) -> str:
    if voice is not None and voice.strip():
        return voice.strip()
    h = hash(_normalize_tts_text(text))
    return _VI_VOICES_ROTATION[h % len(_VI_VOICES_ROTATION)]


def _chunk_for_tts(text: str, max_chars: int = _TTS_CHUNK_CHARS) -> list[str]:
    t = _normalize_tts_text(text)
    if not t:
        return []
    return [t[i : i + max_chars] for i in range(0, len(t), max_chars)]


def _ffmpeg_concat_mp3(part_paths: list[Path], out_path: Path) -> bool:
    if not part_paths or shutil.which("ffmpeg") is None:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / f"{out_path.stem}_concat.txt"
    try:
        with list_path.open("w", encoding="utf-8") as f:
            for p in part_paths:
                ap = str(p.resolve()).replace("'", "'\\''")
                f.write(f"file '{ap}'\n")
        cmd = [
            "ffmpeg",
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


def _cleanup_part_files(part_paths: list[Path]) -> None:
    for p in part_paths:
        try:
            p.unlink(missing_ok=True)
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
) -> None:
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
                if not p.exists() or p.stat().st_size < _MIN_PART_BYTES:
                    raise RuntimeError(f"TTS part empty or too small: {p}")
            if not _ffmpeg_concat_mp3(part_paths, out_path):
                log.warning(
                    "ffmpeg missing or concat failed; using first TTS chunk only for %s",
                    out_path.name,
                )
                shutil.copyfile(part_paths[0], out_path)
        finally:
            _cleanup_part_files(part_paths)

    if not out_path.exists() or out_path.stat().st_size < MIN_VALID_TTS_OUTPUT_BYTES:
        sz = out_path.stat().st_size if out_path.exists() else 0
        raise RuntimeError(f"TTS output missing or too small ({sz} bytes, min {MIN_VALID_TTS_OUTPUT_BYTES})")


def synth_voice_for_text(
    text: str,
    out_path: Path,
    *,
    voice: str | None = None,
    fallback_duration_seconds: float = 2.5,
    tts_enabled: bool | None = None,
) -> Path:
    """
    Synth narration audio for one scene.
    Retries with backoff on rate limits; falls back to silence if all attempts fail.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    voice_name = _pick_voice(text, voice)
    if tts_enabled is None:
        enabled = cfg_bool("tts", "enabled", env_legacy="TTS_ENABLED", default=True)
    else:
        enabled = tts_enabled
    if not enabled:
        return write_silent_track(out_path, fallback_duration_seconds)

    chunks = _chunk_for_tts(text)
    if not chunks:
        return write_silent_track(out_path, fallback_duration_seconds)

    if elevenlabs_ready():
        out_path.unlink(missing_ok=True)
        if synth_elevenlabs_to_path(text, out_path):
            return out_path
        log.info("ElevenLabs unavailable or failed; falling back to Edge TTS for %s", out_path.name)

    for attempt in range(MAX_RETRIES):
        try:
            out_path.unlink(missing_ok=True)
            _run_one_tts_attempt(chunks, out_path, voice_name)
            if out_path.exists() and out_path.stat().st_size >= MIN_VALID_TTS_OUTPUT_BYTES:
                return out_path
        except Exception as exc:
            log.warning(
                "TTS attempt %s/%s failed for %s: %s",
                attempt + 1,
                MAX_RETRIES,
                out_path.name,
                exc,
            )
            if attempt < MAX_RETRIES - 1:
                if _is_likely_403(exc):
                    delay = (2**attempt) + random.uniform(0.5, 1.5)
                    time.sleep(delay)
                else:
                    time.sleep(1.0)

    return write_silent_track(out_path, fallback_duration_seconds)
