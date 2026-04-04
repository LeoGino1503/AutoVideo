from __future__ import annotations

from typing import Any, Optional, Literal

from pydantic import BaseModel, Field
from dataclasses import dataclass
from pathlib import Path


class InputFiletxt(BaseModel):
    filename: str
    content: str

class QuoteIn(BaseModel):
    id: str
    quote: str
    meaning_vi: Optional[str] = None


class BuildScriptResponse(BaseModel):
    job_id: str
    quote_id: str
    micro_story: dict[str, Any]


class StepOkResponse(BaseModel):
    job_id: str
    quote_id: str
    step: str
    detail: Optional[str] = None


class RenderResponse(BaseModel):
    job_id: str
    quote_id: str
    video_filename: str
    video_rel_path: str


class JobStatusResponse(BaseModel):
    job_id: str
    quote_id: str
    has_script: bool
    has_media: bool
    has_audio: bool
    has_video: bool
    video_filename: Optional[str] = None


class FullPipelineRequest(BaseModel):
    quotes: list[QuoteIn] = Field(..., min_length=1)
    quote_id: Optional[str] = None
    use_llm: bool = True
    upload: bool = False


class FullPipelineResponse(BaseModel):
    ok: bool
    output_dir: str
    processed_quote_ids: list[str]

class QuoteItem(BaseModel):
    id: str
    quote: str
    meaning_vi: Optional[str] = None


class MicroScene(BaseModel):
    # Omitted from ``micro_story.json`` on save; render/TTS infer from audio when None.
    narration: str = Field(..., min_length=1)
    onScreenText: str = Field(..., min_length=1, max_length=120)
    imageQuery: str = Field(..., min_length=1, max_length=200)

class MicroStory(BaseModel):
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