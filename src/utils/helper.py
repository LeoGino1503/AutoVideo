import uuid
from pathlib import Path
from src.utils.schemas import PipelinePaths
from fastapi import HTTPException
from src.utils.config_loader import cfg_str
import json
from typing import Any

def _create_job_id() -> str:
    return str(uuid.uuid4())

def _make_paths_for_api_job(out_dir: Path, job_id: str) -> PipelinePaths:
    work_dir = out_dir / "job_id" / job_id
    return _paths_under(work_dir)

def _ensure_dirs(paths: PipelinePaths) -> None:
    for p in [paths.assets_dir, paths.images_dir, paths.audio_dir, paths.rendered_dir]:
        p.mkdir(parents=True, exist_ok=True)

def _load_job_meta(paths: PipelinePaths) -> dict[str, Any]:
    p = paths.assets_dir / cfg_str("micro_story", "json_file_name")
    if not p.exists():
        raise FileNotFoundError("job meta not found")
    return json.loads(p.read_text(encoding="utf-8"))

def _require_job(job_id: str) -> tuple[PipelinePaths, dict[str, Any]]:
    paths = _make_paths_for_api_job(Path(cfg_str("paths", "output_dir")), job_id)
    try:
        meta = _load_job_meta(paths)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found")
    return paths, meta

def _paths_under(work_dir: Path) -> PipelinePaths:
    return PipelinePaths(
        work_dir=work_dir,
        assets_dir=work_dir / "assets",
        images_dir=work_dir / "images",
        audio_dir=work_dir / "audio",
        rendered_dir=work_dir / "rendered",
    )