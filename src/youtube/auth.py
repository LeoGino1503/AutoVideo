from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config_loader import cfg_str


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def get_youtube_credentials(
    *,
    client_secret_path: Optional[Path] = None,
    token_dir: Optional[Path] = None,
) -> Credentials:
    client_secret_path = client_secret_path or Path(
        cfg_str(
            "youtube",
            "client_secret_path",
            env_legacy="GOOGLE_CLIENT_SECRET_PATH",
            default="client_secret.json",
        )
    )
    if not client_secret_path.exists():
        raise FileNotFoundError(f"Google client secret not found: {client_secret_path}")

    token_dir = token_dir or Path.home() / ".cache" / "murphy_api" / "youtube"
    token_dir.mkdir(parents=True, exist_ok=True)
    token_path = token_dir / "token.json"

    creds: Optional[Credentials] = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes=[YOUTUBE_UPLOAD_SCOPE])

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path), scopes=[YOUTUBE_UPLOAD_SCOPE]
    )
    creds = flow.run_local_server(port=0)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds

