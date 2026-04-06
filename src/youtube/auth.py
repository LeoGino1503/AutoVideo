from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from src.utils.config_loader import cfg_int, cfg_str


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def get_youtube_credentials(
    *,
    client_secret_path: Optional[Path] = None,
    token_dir: Optional[Path] = None,
) -> Credentials:
    client_secret_path = client_secret_path or Path(
        cfg_str(
            "youtube",
            "google_client_secret_path",
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
    # port=0 → redirect random (localhost:51377/…) causes Google Error 400 redirect_uri_mismatch
    # because that URI is not registered. Use a fixed port and add it in Google Cloud Console.
    port = cfg_int("youtube", "oauth_local_server_port", env_legacy="YOUTUBE_OAUTH_PORT", default=8080)
    port = max(1024, min(port, 65535))
    creds = flow.run_local_server(port=port, bind_addr="127.0.0.1")
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds

