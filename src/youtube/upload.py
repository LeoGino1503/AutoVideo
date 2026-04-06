from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from src.utils.config_loader import cfg_str
from src.utils.schemas import MicroStory
from src.youtube.auth import get_youtube_credentials

# YouTube Data API: snippet.title must be 1–100 characters.
_YOUTUBE_TITLE_MAX = 100


def _youtube_snippet_title(
    *,
    story: MicroStory,
    title_prefix: str,
    quote_id: str | None,
) -> str:
    prefix = (title_prefix or "").strip()
    qid = ((story.quote_id or quote_id) or "").strip()
    explicit = (story.youtube_title or "").strip()
    if explicit:
        title = explicit
    elif prefix and qid:
        title = f"{prefix} - {qid}"
    elif prefix:
        title = prefix
    elif qid:
        title = qid
    else:
        hook = (story.title_hook or "").strip()
        if hook:
            title = hook
        else:
            body = (story.voice_text_full or "").strip()
            title = body[:_YOUTUBE_TITLE_MAX] if body else "Video"
    title = title.strip() or "Video"
    if len(title) > _YOUTUBE_TITLE_MAX:
        title = title[:_YOUTUBE_TITLE_MAX].rstrip()
    return title or "Video"


def upload_to_youtube(
    *, video_path: Path, story: MicroStory, quote_id: str | None = None
) -> str:
    """
    Upload MP4 lên YouTube (OAuth). Cần ``client_secret.json`` / token đã cấu hình.
    """
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(str(video_path))

    _ = cfg_str("youtube", "channel_id", default="")

    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    title_prefix = cfg_str("youtube", "title_prefix", default="Murphy's Law | Định Luật Murphy")
    title = _youtube_snippet_title(
        story=story, title_prefix=title_prefix, quote_id=quote_id
    )

    description = story.youtube_description + cfg_str("youtube", "description", default="") + story.youtube_tags + cfg_str("youtube", "tags", default="#podcast #shorts #viral")
    privacy_status = story.youtube_privacy_status or cfg_str(
        "youtube", "privacy_status", default="private"
    )

    request_body = {
        "snippet": {
            "title": title,
            "description": description,
        },
        "status": {"privacyStatus": privacy_status},
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    insert_request = youtube.videos().insert(
        part="snippet,status",
        body=request_body,
        media_body=media,
    )

    insert_response = None
    while insert_response is None:
        _, insert_response = insert_request.next_chunk()

    if not isinstance(insert_response, dict):
        raise RuntimeError("YouTube API returned an unexpected response.")

    video_id = insert_response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube upload failed: no video id in response ({insert_response!r}).")

    return str(video_id)


__all__ = ["upload_to_youtube"]
