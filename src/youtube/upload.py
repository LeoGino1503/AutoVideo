from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config_loader import cfg_str
from script_schema import MicroStory
from youtube.auth import get_youtube_credentials


def upload_to_youtube(*, video_path: Path, story: MicroStory) -> None:
    """
    Uploads a finished MP4 to YouTube using OAuth credentials.
    This function assumes you've already created `client_secret.json`.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(video_path)

    _ = cfg_str("youtube", "channel_id", env_legacy="YOUTUBE_CHANNEL_ID", default="")
    # channel_id isn't required for upload; config key kept for future use.

    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    title_prefix = cfg_str("youtube", "title_prefix", env_legacy="YOUTUBE_TITLE_PREFIX", default="Murphy")
    title_hook = story.title_hook or ""
    title = story.youtube_title or f"{title_prefix}: {title_hook}".strip(": ").strip()

    description = story.youtube_description or cfg_str(
        "youtube", "description", env_legacy="YOUTUBE_DESCRIPTION", default="Daily Murphy's Law micro-story."
    )
    tags = story.youtube_tags or cfg_str(
        "youtube", "tags", env_legacy="YOUTUBE_TAGS", default="murphys law,shorts,viral"
    )
    privacy_status = story.youtube_privacy_status or cfg_str(
        "youtube", "privacy_status", env_legacy="YOUTUBE_PRIVACY_STATUS", default="private"
    )

    request_body = {
        "snippet": {
            "title": title[:100],
            "description": (description or "")[:5000],
            "tags": [t.strip() for t in tags.split(",") if t.strip()],
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
        status, insert_response = insert_request.next_chunk()
        if status:
            # status.progress gives 0..1
            pass

    video_id = insert_response.get("id")
    if video_id:
        print(f"Uploaded to YouTube. Video ID: {video_id}")

