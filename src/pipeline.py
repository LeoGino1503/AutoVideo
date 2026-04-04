from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from config_loader import cfg_bool
from pipeline_steps import (
    build_micro_story,
    default_output_dir,
    ensure_dirs,
    fetch_media_for_story,
    image_provider_from_config,
    make_paths_for_quote,
    render_video,
    save_micro_story,
    synth_audio_for_story,
    sync_micro_story_durations_from_audio,
)
from script_schema import QuoteItem
from youtube.upload import upload_to_youtube


def run_pipeline_for_quotes(
    quote_items: list[dict[str, Any]] | list[QuoteItem],
    out_dir: Path,
    quote_id: Optional[str] = None,
    use_llm: bool = True,
    mode: str = "shorts",
    upload: bool = False,
):
    out_dir.mkdir(parents=True, exist_ok=True)

    normalized: list[QuoteItem] = []
    for item in quote_items:
        if isinstance(item, QuoteItem):
            normalized.append(item)
        else:
            normalized.append(QuoteItem.model_validate(item))

    if quote_id:
        normalized = [q for q in normalized if q.id == quote_id]
        if not normalized:
            raise ValueError(f"quote_id not found: {quote_id}")

    tts_enabled = cfg_bool("tts", "enabled", env_legacy="TTS_ENABLED", default=True)
    youtube_upload_allowed = cfg_bool(
        "youtube", "upload", env_legacy="YOUTUBE_UPLOAD", default=False
    )
    provider = image_provider_from_config()

    for quote in normalized:
        paths = make_paths_for_quote(out_dir, quote.id)
        ensure_dirs(paths)

        print(f"Processing {quote.id}")

        story = build_micro_story(quote, use_llm=use_llm, mode=mode)
        save_micro_story(paths, story)

        scene_paths = fetch_media_for_story(story, paths, provider)
        audio_paths = synth_audio_for_story(story, paths, tts_enabled=tts_enabled)
        story = sync_micro_story_durations_from_audio(paths, save=True)

        out_video_path = render_video(
            story,
            scene_paths,
            audio_paths,
            paths,
            quote_id=quote.id,
        )

        if upload and youtube_upload_allowed:
            upload_to_youtube(video_path=out_video_path, story=story)


__all__ = ["run_pipeline_for_quotes", "default_output_dir"]
