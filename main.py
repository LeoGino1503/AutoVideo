from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from src.utils.schemas import (
    BuildScriptResponse,
    EndToEndPipelineResponse,
    PipelinePaths,
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
from src.utils.helper import (
    _create_job_id,
    _make_paths_for_api_job,
    _require_job,
    ensure_dirs,
    slug,
)
from pathlib import Path
from src.media.pexels_unsplash import ImageProvider
from src.tts.service import _synth_audio_for_story
from src.utils.scene_paths import (
    list_bgm_song_paths,
    list_scene_audio_paths,
    list_scene_media_paths,
)
from googleapiclient.errors import HttpError
from src.video.render_moviepy import render_final_concat_mux
from src.youtube.upload import upload_to_youtube

load_dotenv()
load_yaml_config()


def _render_job(paths: PipelinePaths, job_id: str, quote_id: str) -> RenderResponse:
    story = _load_micro_story(paths)
    ensure_dirs(paths)
    n = len(story.scenes)
    try:
        media_paths = list_scene_media_paths(paths, n)
        audio_paths = list_scene_audio_paths(paths, n)
        bgm = list_bgm_song_paths()
        out = render_final_concat_mux(
            rendered_dir=paths.rendered_dir,
            quote_id=slug(quote_id),
            media_paths=media_paths,
            audio_paths=audio_paths,
            micro_story=story,
            pipeline_paths=paths,
            micro_story_quote_id=quote_id,
            bgm_song_paths=bgm if bgm else None,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    root = Path(cfg_str("paths", "output_dir")).resolve()
    rel = out.resolve().relative_to(root)
    return RenderResponse(
        job_id=job_id,
        quote_id=quote_id,
        video_filename=out.name,
        video_rel_path=str(rel),
    )


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
    quote_id = (file.filename or "script").rsplit(".", 1)[0]
    paths = _make_paths_for_api_job(Path(cfg_str("paths", "output_dir")), job_id)
    ensure_dirs(paths)
    text_content = file.file.read().decode("utf-8").strip()
    if not text_content:
        raise HTTPException(status_code=400, detail="File is empty or whitespace only.")
    try:
        story = _build_micro_story(text_content, quote_id=quote_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_micro_story(paths, story, quote_id)
    return BuildScriptResponse(
        job_id=job_id,
        quote_id=quote_id,
        micro_story=story.model_dump(),
    )


@app.post("/api/v1/jobs/full-from-txt", response_model=EndToEndPipelineResponse)
def post_full_pipeline_from_txt(
    file: UploadFile = File(...),
    tts_enabled: bool | None = None,
) -> EndToEndPipelineResponse:
    """
    Một request: upload ``.txt`` → tạo job, build script, tải media, TTS, render MP4 cuối.
    Query ``tts_enabled`` (optional) giống bước ``/tts``; mặc định theo config.
    """
    job_id = _create_job_id()
    quote_id = (file.filename or "script").rsplit(".", 1)[0]
    paths = _make_paths_for_api_job(Path(cfg_str("paths", "output_dir")), job_id)
    ensure_dirs(paths)
    text_content = file.file.read().decode("utf-8").strip()
    if not text_content:
        raise HTTPException(status_code=400, detail="File is empty or whitespace only.")
    try:
        story = _build_micro_story(text_content, quote_id=quote_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _save_micro_story(paths, story, quote_id)

    provider = ImageProvider.from_env(name=cfg_str("image", "provider", default="pexels_unsplash"))
    _fetch_media_for_story(story, paths, provider)

    use_tts = (
        tts_enabled
        if tts_enabled is not None
        else cfg_bool("tts", "enabled", default=True)
    )
    _synth_audio_for_story(story, paths, tts_enabled=use_tts)

    rendered = _render_job(paths, job_id, quote_id)
    return EndToEndPipelineResponse(
        job_id=rendered.job_id,
        quote_id=rendered.quote_id,
        micro_story=story.model_dump(),
        video_filename=rendered.video_filename,
        video_rel_path=rendered.video_rel_path,
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
    use_tts = (
        tts_enabled
        if tts_enabled is not None
        else cfg_bool("tts", "enabled", default=True)
    )
    _synth_audio_for_story(story, paths, tts_enabled=use_tts)
    return StepOkResponse(
        job_id=job_id, 
        quote_id=quote_id, 
        step="tts",
    )


@app.post("/api/v1/jobs/{job_id}/render", response_model=RenderResponse)
def post_render(job_id: str) -> RenderResponse:
    """
    Cần đã chạy fetch-media và tts.
    Nối video từng scene → ``rendered/video_final.mp4``, nối audio → ``rendered/audio_final.mp3``,
    rồi mux thành ``rendered/{slug(quote_id)}.mp4``.
    """
    paths, meta = _require_job(job_id)
    quote_id = str(meta["quote_id"])
    return _render_job(paths, job_id, quote_id)


@app.post("/api/v1/jobs/{job_id}/upload-youtube", response_model=StepOkResponse)
def post_upload_youtube(job_id: str) -> StepOkResponse:
    """
    Upload ``rendered/{slug(quote_id)}.mp4`` lên YouTube (metadata lấy từ micro story + ``config.yaml``).
    Bật bằng ``youtube.upload: true``; cần OAuth (``youtube.google_client_secret_path``).
    """
    if not cfg_bool("youtube", "upload", default=False):
        raise HTTPException(
            status_code=400,
            detail="YouTube upload disabled in config (youtube.upload).",
        )
    paths, meta = _require_job(job_id)
    quote_id = str(meta["quote_id"])
    video_path = paths.rendered_dir / f"{slug(quote_id)}.mp4"
    if not video_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Rendered video missing: {video_path.name}. Run POST .../render first.",
        )
    story = _load_micro_story(paths)
    try:
        video_id = upload_to_youtube(
            video_path=video_path, story=story, quote_id=quote_id
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HttpError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"YouTube API error: {exc}",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return StepOkResponse(
        job_id=job_id,
        quote_id=quote_id,
        step="upload-youtube",
        youtube_video_id=video_id,
        detail=f"https://www.youtube.com/watch?v={video_id}",
    )