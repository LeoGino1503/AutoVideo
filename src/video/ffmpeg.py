"""Tìm binary ffmpeg/ffprobe (PATH, env, imageio_ffmpeg)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def resolve_ffmpeg_executable() -> str | None:
    """
    Thứ tự: ``FFMPEG_BINARY``, ``PATH``, ``imageio_ffmpeg`` (MoviePy),
    ``/usr/bin/ffmpeg``, ``/usr/local/bin/ffmpeg``.
    """
    env = os.environ.get("FFMPEG_BINARY", "").strip()
    if env and Path(env).is_file():
        return env
    w = shutil.which("ffmpeg")
    if w:
        return w
    try:
        import imageio_ffmpeg

        p = imageio_ffmpeg.get_ffmpeg_exe()
        if p and Path(p).is_file():
            return p
    except Exception:
        pass
    for p in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(p).is_file():
            return p
    return None


def resolve_ffprobe_executable() -> str | None:
    """``FFPROBE_BINARY``, ``PATH``, hoặc ``ffprobe`` cạnh binary ffmpeg."""
    env = os.environ.get("FFPROBE_BINARY", "").strip()
    if env and Path(env).is_file():
        return env
    w = shutil.which("ffprobe")
    if w:
        return w
    ff = resolve_ffmpeg_executable()
    if ff:
        parent = Path(ff).parent
        for name in ("ffprobe", "ffprobe.exe"):
            cand = parent / name
            if cand.is_file():
                return str(cand)
    for p in ("/usr/bin/ffprobe", "/usr/local/bin/ffprobe"):
        if Path(p).is_file():
            return p
    return None
