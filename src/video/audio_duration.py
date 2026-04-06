"""Đo độ dài file âm thanh (ffprobe ưu tiên, fallback MoviePy)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from src.video.ffmpeg import resolve_ffprobe_executable


def probe_audio_duration_seconds(audio_path: Path) -> float:
    """
    Trả về thời lượng giây (MP3/WAV, …).
    Ưu tiên ``ffprobe``; không có thì dùng MoviePy ``AudioFileClip``.
    """
    audio_path = Path(audio_path)
    if not audio_path.is_file() or audio_path.stat().st_size == 0:
        raise FileNotFoundError(str(audio_path))

    probe = resolve_ffprobe_executable()
    if probe:
        cmd = [
            probe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and r.stdout.strip():
            return max(0.05, float(r.stdout.strip()))

    from moviepy.audio.io.AudioFileClip import AudioFileClip

    clip = AudioFileClip(str(audio_path))
    try:
        d = float(clip.duration or 0.0)
        if d <= 0:
            raise RuntimeError("MoviePy reported zero duration")
        return max(0.05, d)
    finally:
        clip.close()
