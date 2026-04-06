"""Đường dẫn media/audio theo từng scene (scene_00, scene_01, …)."""

from __future__ import annotations

from pathlib import Path

from src.tts.service import write_silent_track
from src.utils.schemas import PipelinePaths


def _first_nonempty_scene_audio(paths: PipelinePaths, idx: int) -> Path | None:
    stem = f"scene_{idx:02d}"
    for name in (f"{stem}.mp3", f"{stem}.wav", f"{stem}_silent.mp3", f"{stem}_silent.wav"):
        p = paths.audio_dir / name
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def list_scene_media_paths(paths: PipelinePaths, n_scenes: int) -> list[Path]:
    """Ordered scene_00.*, scene_01.*, ..."""
    result: list[Path] = []
    for idx in range(n_scenes):
        stem = f"scene_{idx:02d}"
        matches = sorted(paths.images_dir.glob(f"{stem}.*"))
        if not matches:
            raise FileNotFoundError(f"Missing media for {stem} under {paths.images_dir}")
        result.append(matches[0])
    return result


def list_scene_audio_paths(paths: PipelinePaths, n_scenes: int) -> list[Path]:
    result: list[Path] = []
    for idx in range(n_scenes):
        chosen = _first_nonempty_scene_audio(paths, idx)
        result.append(
            chosen
            if chosen is not None
            else write_silent_track(paths.audio_dir / f"scene_{idx:02d}_silent.mp3", 2.5)
        )
    return result
