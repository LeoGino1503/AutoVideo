from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

from audio_probe import probe_audio_duration_seconds
from src.utils.config_loader import cfg_bool, cfg_str
from src.media.pexels_unsplash import ImageProvider
from src.utils.schemas import MicroScene, MicroStory, QuoteItem
from tts.edge_tts_synth import synth_voice_for_text, write_silent_track
from video.render_moviepy import render_micro_story_video
StoryMode = Literal["shorts", "long_segments"]





def slug(s: str) -> str:
    s2 = re.sub(r"[^a-zA-Z0-9_-]+", "_", s).strip("_")
    return s2[:120] if s2 else "item"


def make_paths_for_quote(out_dir: Path, quote_id: str) -> PipelinePaths:
    work_dir = out_dir / slug(quote_id)
    return _paths_under(work_dir)


def ensure_dirs(paths: PipelinePaths) -> None:
    for p in [paths.assets_dir, paths.images_dir, paths.audio_dir, paths.rendered_dir]:
        p.mkdir(parents=True, exist_ok=True)


QUOTE_JSON = "quote.json"
MICRO_STORY_JSON = "micro_story.json"
JOB_META_JSON = "job_meta.json"


def save_quote(paths: PipelinePaths, quote: QuoteItem) -> None:
    ensure_dirs(paths)
    (paths.assets_dir / QUOTE_JSON).write_text(quote.model_dump_json(indent=2), encoding="utf-8")


def load_quote(paths: PipelinePaths) -> QuoteItem:
    raw = (paths.assets_dir / QUOTE_JSON).read_text(encoding="utf-8")
    return QuoteItem.model_validate_json(raw)


def scene_audio_path_for_index(paths: PipelinePaths, idx: int) -> Path | None:
    """First existing non-empty audio file for ``scene_{idx:02d}`` (same order as ``list_scene_audio_paths``)."""
    stem = f"scene_{idx:02d}"
    for p in (
        paths.audio_dir / f"{stem}.mp3",
        paths.audio_dir / f"{stem}.wav",
        paths.audio_dir / f"{stem}_silent.mp3",
        paths.audio_dir / f"{stem}_silent.wav",
    ):
        if p.exists() and p.stat().st_size > 0:
            return p
    return None




def save_job_meta(paths: PipelinePaths, *, job_id: str, quote_id: str) -> None:
    ensure_dirs(paths)
    (paths.work_dir / JOB_META_JSON).write_text(
        json.dumps({"job_id": job_id, "quote_id": quote_id}, indent=2),
        encoding="utf-8",
    )


def load_job_meta(paths: PipelinePaths) -> dict[str, Any]:
    p = paths.work_dir / JOB_META_JSON
    if not p.exists():
        raise FileNotFoundError("job not found")
    return json.loads(p.read_text(encoding="utf-8"))


def new_job_id() -> str:
    return 


def build_micro_story_fallback(quote: QuoteItem) -> MicroStory:
    scenes = [
        {
            "narration": "Bạn vừa làm mọi thứ tưởng như ổn.",
            "onScreenText": "Mọi thứ tưởng ổn…",
            "imageQuery": "person confident checking calendar, cinematic lighting, realistic",
        },
        {
            "narration": "Thì một chuyện xấu xảy ra đúng lúc bạn cần nhất.",
            "onScreenText": "Và rồi xui đến…",
            "imageQuery": "sudden rain outside window, worried person, cinematic realism",
        },
        {
            "narration": "Murphy nói: nếu có thể sai, nó sẽ sai.",
            "onScreenText": "Nếu có thể sai...",
            "imageQuery": "dramatic shadow, tense atmosphere, cinematic, realistic",
        },
        {
            "narration": f"Nhưng ít nhất bạn đã được cảnh báo: {quote.meaning_vi or 'đừng quá bất ngờ.'}",
            "onScreenText": "Bạn đã được cảnh báo.",
            "imageQuery": "person realizing lesson, hopeful mood, cinematic, realistic",
        },
    ]
    return MicroStory(
        title_hook="Murphy: Nếu có thể sai...",
        voice_text_full=None,
        scenes=scenes,
        youtube_title=None,
        youtube_description=None,
        youtube_tags=None,
        youtube_privacy_status="private",
    )


def _split_long_text_to_segments(text: str, max_chars: int = 260) -> list[str]:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return []
    # Split by sentence boundaries, then merge into medium chunks.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    if not sentences:
        return [cleaned[:max_chars]]
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(s) > max_chars:
            if cur:
                chunks.append(cur.strip())
                cur = ""
            for i in range(0, len(s), max_chars):
                chunks.append(s[i : i + max_chars].strip())
            continue
        cand = f"{cur} {s}".strip() if cur else s
        if len(cand) <= max_chars:
            cur = cand
        else:
            chunks.append(cur.strip())
            cur = s
    if cur:
        chunks.append(cur.strip())
    return [c for c in chunks if c]


def _keyword_for_segment(seg: str) -> str:
    if "Charles Horton Cooley" in seg:
        return "Charles Horton Cooley"
    m = re.search(r"['\"]([^'\"]{3,60})['\"]", seg)
    if m:
        return m.group(1)[:60]
    words = seg.split()
    return " ".join(words[:4])[:60] if words else "Key idea"


def _image_query_for_segment(keyword: str, seg: str) -> str:
    q = f"{keyword}, {seg[:120]}, cinematic realistic, documentary style"
    return q[:200]


def build_micro_story_long_segments(quote: QuoteItem) -> MicroStory:
    source = (quote.meaning_vi or quote.quote or "").strip()
    segments = _split_long_text_to_segments(source, max_chars=260)
    if not segments:
        return build_micro_story_fallback(quote)
    scenes: list[dict[str, Any]] = []
    for seg in segments[:24]:
        keyword = _keyword_for_segment(seg)
        scenes.append(
            {
                "narration": seg,
                "onScreenText": keyword,
                "imageQuery": _image_query_for_segment(keyword, seg),
            }
        )
    return MicroStory(
        title_hook=quote.quote[:90],
        voice_text_full=source,
        scenes=scenes,
        youtube_title=quote.quote[:100],
        youtube_description=(quote.meaning_vi or quote.quote)[:400],
        youtube_tags="psychology,self concept,looking glass self,charles horton cooley",
        youtube_privacy_status="private",
    )


def build_micro_story(
    quote: QuoteItem,
    *,
    use_llm: bool = True,
    mode: StoryMode = "shorts",
) -> MicroStory:
    source_text = (quote.meaning_vi or quote.quote or "").strip()
    if not source_text:
        raise ValueError("Quote text is empty; cannot build micro story.")
    if mode == "long_segments":
        return build_micro_story_long_segments(quote)
    if not use_llm:
        return build_micro_story_fallback(quote)
    return build_micro_story_from_txt(source_text)











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
        stem = f"scene_{idx:02d}"
        candidates = [
            paths.audio_dir / f"{stem}.mp3",
            paths.audio_dir / f"{stem}.wav",
            paths.audio_dir / f"{stem}_silent.mp3",
            paths.audio_dir / f"{stem}_silent.wav",
        ]
        chosen: Path | None = None
        for p in candidates:
            if p.exists() and p.stat().st_size > 0:
                chosen = p
                break
        result.append(chosen if chosen is not None else _make_silent_audio(paths.audio_dir, 2.5, idx))
    return result


def render_video(
    story: MicroStory,
    scene_paths: list[Path],
    audio_paths: list[Path],
    paths: PipelinePaths,
    *,
    quote_id: str,
) -> Path:
    if len(scene_paths) != len(story.scenes) or len(audio_paths) != len(story.scenes):
        raise ValueError("scene_paths and audio_paths must match story.scenes length")
    ensure_dirs(paths)
    out_video_path = paths.rendered_dir / f"{slug(quote_id)}.mp4"
    render_micro_story_video(
        scene_paths=scene_paths,
        audio_paths=audio_paths,
        scenes=story.scenes,
        out_path=out_video_path,
        fps=24,
    )
    return out_video_path


def default_output_dir() -> Path:
    return Path(cfg_str("paths", "output_dir", env_legacy="OUTPUT_DIR", default="output"))
