from __future__ import annotations

from typing import Any, Optional, Literal

from pydantic import BaseModel, Field
from dataclasses import dataclass
from pathlib import Path


class BuildScriptResponse(BaseModel):
    job_id: str
    quote_id: str
    micro_story: dict[str, Any]


class StepOkResponse(BaseModel):
    job_id: str
    quote_id: str
    step: str
    detail: Optional[str] = None
    youtube_video_id: Optional[str] = None


class RenderResponse(BaseModel):
    job_id: str
    quote_id: str
    video_filename: str
    video_rel_path: str


class EndToEndPipelineResponse(BaseModel):
    """Kết quả sau khi chạy build script → fetch-media → TTS → render trong một request."""

    job_id: str
    quote_id: str
    micro_story: dict[str, Any]
    video_filename: str
    video_rel_path: str


class MicroScene(BaseModel):
    # When None, render probes audio or estimates from narration length.
    narration: str = Field(..., min_length=1)
    onScreenText: str = Field(..., min_length=1, max_length=120)
    imageQuery: str = Field(..., min_length=1, max_length=200)
    duration_seconds: Optional[float] = None

class MicroStory(BaseModel):
    quote_id: Optional[str] = None
    title_hook: Optional[str] = None
    voice_text_full: Optional[str] = None
    scenes: list[MicroScene]
    # Optional: save a short caption/description for upload
    youtube_title: Optional[str] = None
    youtube_description: Optional[str] = None
    youtube_tags: Optional[str] = None
    youtube_privacy_status: Optional[Literal["public", "unlisted", "private"]] = None

@dataclass(frozen=True)
class PipelinePaths:
    work_dir: Path
    assets_dir: Path
    images_dir: Path
    audio_dir: Path
    rendered_dir: Path