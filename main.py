from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from utils.schemas import (
    BuildScriptResponse,
    FullPipelineRequest,
    FullPipelineResponse,
    JobStatusResponse,
    RenderResponse,
    StepOkResponse,
)
from src.utils.config_loader import cfg_bool, load_yaml_config, cfg_str
from src.microstory.service import (
    _build_micro_story, 
    _save_micro_story, 
    _load_micro_story,
)
from src.media.service import _fetch_media_for_story
from src.utils.helper import _create_job_id, _make_paths_for_api_job, _ensure_dirs, _require_job
from pathlib import Path
from src.media.pexels_unsplash import ImageProvider
from src.tts.service import _synth_audio_for_story, _sync_micro_story_durations_from_audio
load_dotenv()
load_yaml_config()


# def _quote_from_in(q: QuoteIn) -> QuoteItem:
#     return QuoteItem(id=q.id, quote=q.quote, meaning_vi=q.meaning_vi)

app = FastAPI(
    title="AutoVideo API",
    version="1.0.0",
    description="Tách bước pipeline (build script → media → TTS → render → upload) hoặc chạy full end-to-end.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@app.post("/api/v1/jobs/build-script-from-txt", response_model=BuildScriptResponse)
def post_build_script_from_txt(
    file: UploadFile = File(...),
) -> BuildScriptResponse:
    """
    Upload one `.txt` file and build script for a new job.
    Args:
        file: The `.txt` file to upload.
    Returns:
        A `BuildScriptResponse` object.
    """
    job_id = _create_job_id()   
    quote_id = file.filename.split(".")[0]
    paths = _make_paths_for_api_job(Path(cfg_str("paths", "output_dir")), job_id)
    _ensure_dirs(paths)
    text_content = file.file.read().decode("utf-8").strip()
    story = _build_micro_story(text_content)
    _save_micro_story(paths, story, quote_id)
    return BuildScriptResponse(
        job_id=job_id,
        quote_id=quote_id,
        micro_story=story.model_dump(),
    )


@app.post("/api/v1/jobs/{job_id}/fetch-media", response_model=StepOkResponse)
def post_fetch_media(job_id: str) -> StepOkResponse:
    """
    Tải ảnh/video stock theo từng scene.
    Args:
        job_id: The job ID.
    Returns:
        A `StepOkResponse` object.
    """
    paths, meta = _require_job(job_id)
    quote_id = str(meta["quote_id"])
    story = _load_micro_story(paths)
    provider = ImageProvider.from_env(name=cfg_str("image", "provider", default="pexels_unsplash"))
    _fetch_media_for_story(story, paths, provider)
    return StepOkResponse(
        job_id=job_id, 
        quote_id = quote_id,
        step="fetch-media",
    )


@app.post("/api/v1/jobs/{job_id}/tts", response_model=StepOkResponse)
def post_tts(job_id: str, tts_enabled: bool | None = None) -> StepOkResponse:
    """
    Tổng hợp giọng đọc theo scene. Mặc định theo `config.yaml` (`tts.enabled`);
    có thể override bằng query `?tts_enabled=true|false`.
    Args:
        job_id: The job ID.
        tts_enabled: Whether to enable TTS.
    Returns:
        A `StepOkResponse` object.
    """
    paths, meta = _require_job(job_id)
    quote_id = str(meta["quote_id"])
    story = _load_micro_story(paths)
    tts_enabled = cfg_bool("tts", "enabled", default=True)
    _synth_audio_for_story(story, paths, tts_enabled=tts_enabled)
    if tts_enabled:
        _sync_micro_story_durations_from_audio(story, paths, save=True)
    return StepOkResponse(
        job_id=job_id, 
        quote_id=quote_id, 
        step="tts",
    )


# @app.post("/api/v1/jobs/{job_id}/render", response_model=RenderResponse)
# def post_render(job_id: str) -> RenderResponse:
#     """Ghép media + audio + caption thành MP4 (cần fetch-media và tts)."""
#     paths, meta = _require_job(job_id)
#     quote_id = str(meta["quote_id"])
#     story = sync_micro_story_durations_from_audio(paths, save=True)
#     n = len(story.scenes)
#     scene_paths = list_scene_media_paths(paths, n)
#     audio_paths = list_scene_audio_paths(paths, n)
#     out = render_video(
#         story,
#         scene_paths,
#         audio_paths,
#         paths,
#         quote_id=quote_id,
#     )
#     root = default_output_dir().resolve()
#     rel = out.resolve().relative_to(root)
#     return RenderResponse(
#         job_id=job_id,
#         quote_id=quote_id,
#         video_filename=out.name,
#         video_rel_path=str(rel),
#     )


# @app.post("/api/v1/jobs/{job_id}/upload", response_model=StepOkResponse)
# def post_upload(job_id: str) -> StepOkResponse:
#     """Upload MP4 lên YouTube (chỉ khi `youtube.upload: true` trong config / env)."""
#     paths, meta = _require_job(job_id)
#     quote_id = str(meta["quote_id"])
#     allowed = cfg_bool("youtube", "upload", env_legacy="YOUTUBE_UPLOAD", default=False)
#     if not allowed:
#         raise HTTPException(
#             status_code=400,
#             detail="YouTube upload disabled in config (youtube.upload / YOUTUBE_UPLOAD).",
#         )
#     story = load_micro_story(paths)
#     video_path = paths.rendered_dir / f"{slug(quote_id)}.mp4"
#     if not video_path.exists():
#         raise HTTPException(status_code=400, detail="Rendered video missing; run /render first.")
#     upload_to_youtube(video_path=video_path, story=story)
#     return StepOkResponse(job_id=job_id, quote_id=quote_id, step="upload", detail="YouTube upload finished")


# @app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
# def get_job_status(job_id: str) -> JobStatusResponse:
#     paths, meta = _require_job(job_id)
#     quote_id = str(meta["quote_id"])
#     script_path = paths.assets_dir / MICRO_STORY_JSON
#     has_script = script_path.exists()
#     n_scenes = 0
#     if has_script:
#         try:
#             n_scenes = len(load_micro_story(paths).scenes)
#         except Exception:
#             n_scenes = 0
#     has_media = False
#     has_audio = False
#     if has_script and n_scenes > 0:
#         try:
#             list_scene_media_paths(paths, n_scenes)
#             has_media = True
#         except FileNotFoundError:
#             has_media = False
#         try:
#             list_scene_audio_paths(paths, n_scenes)
#             # consider has_audio if at least one real file
#             has_audio = True
#             for i in range(n_scenes):
#                 stem = paths.audio_dir / f"scene_{i:02d}"
#                 ok = any(
#                     p.exists() and p.stat().st_size > 0
#                     for p in (
#                         stem.with_suffix(".mp3"),
#                         stem.with_suffix(".wav"),
#                         paths.audio_dir / f"scene_{i:02d}_silent.mp3",
#                         paths.audio_dir / f"scene_{i:02d}_silent.wav",
#                     )
#                 )
#                 if not ok:
#                     has_audio = False
#                     break
#         except Exception:
#             has_audio = False
#     video_path = paths.rendered_dir / f"{slug(quote_id)}.mp4"
#     has_video = video_path.exists()
#     return JobStatusResponse(
#         job_id=job_id,
#         quote_id=quote_id,
#         has_script=has_script,
#         has_media=has_media,
#         has_audio=has_audio,
#         has_video=has_video,
#         video_filename=video_path.name if has_video else None,
#     )


# @app.get("/api/v1/jobs/{job_id}/video")
# def get_job_video(job_id: str):
#     paths, meta = _require_job(job_id)
#     quote_id = str(meta["quote_id"])
#     video_path = paths.rendered_dir / f"{slug(quote_id)}.mp4"
#     if not video_path.exists():
#         raise HTTPException(status_code=404, detail="Video not rendered yet")
#     return FileResponse(video_path, media_type="video/mp4", filename=video_path.name)


# @app.post("/api/v1/pipeline/full", response_model=FullPipelineResponse)
# def post_pipeline_full(body: FullPipelineRequest) -> FullPipelineResponse:
#     """
#     Chạy toàn bộ pipeline cho một hoặc nhiều quote (giống CLI `run`),
#     ghi ra thư mục output theo `quote_id` (không dùng thư mục `api_jobs`).
#     """
#     out = default_output_dir()
#     items = [q.model_dump() for q in body.quotes]
#     run_pipeline_for_quotes(
#         quote_items=items,
#         out_dir=out,
#         quote_id=body.quote_id,
#         use_llm=body.use_llm,
#         upload=body.upload,
#     )
#     ids = [q.id for q in body.quotes]
#     if body.quote_id:
#         ids = [body.quote_id]
#     return FullPipelineResponse(ok=True, output_dir=str(out.resolve()), processed_quote_ids=ids)
