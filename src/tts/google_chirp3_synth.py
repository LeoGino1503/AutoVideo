from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from src.utils.config_loader import cfg_bool, cfg_int, cfg_str

log = logging.getLogger(__name__)

_MIN_OUTPUT_BYTES = 400


def _normalize(text: str) -> str:
    return " ".join((text or "").split())


def _chunk_text(text: str) -> list[str]:
    t = _normalize(text)
    if not t:
        return []
    max_chars = cfg_int("tts", "google_chirp3", "max_chunk_chars", default=2400)
    max_chars = max(200, int(max_chars))
    return [t[i : i + max_chars] for i in range(0, len(t), max_chars)]


def _ffmpeg_concat_mp3(part_paths: list[Path], out_path: Path) -> bool:
    if not part_paths or shutil.which("ffmpeg") is None:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / f"{out_path.stem}_gctts_concat.txt"
    try:
        with list_path.open("w", encoding="utf-8") as f:
            for p in part_paths:
                ap = str(p.resolve()).replace("'", "'\\''")
                f.write(f"file '{ap}'\n")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(out_path),
        ]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    finally:
        try:
            list_path.unlink(missing_ok=True)
        except OSError:
            pass


def _endpoint_for_region(region: str) -> str | None:
    r = (region or "").strip().lower()
    if not r or r == "global":
        return None
    if r in {"us", "eu"}:
        return f"{r}-texttospeech.googleapis.com"
    return f"{r}-texttospeech.googleapis.com"


def google_chirp3_ready() -> bool:
    if not cfg_bool("tts", "google_chirp3", "enabled", default=False):
        return False
    try:
        from google.cloud import texttospeech  # noqa: F401
    except Exception:
        return False
    return True


def _build_client():
    from google.cloud import texttospeech

    region = cfg_str("tts", "google_chirp3", "region", default="global").strip().lower()
    endpoint = _endpoint_for_region(region)
    if endpoint:
        return texttospeech.TextToSpeechClient(
            client_options={"api_endpoint": endpoint}
        )
    return texttospeech.TextToSpeechClient()


def _synthesize_chunk(client, chunk: str, voice_name: str, language_code: str) -> bytes:
    from google.cloud import texttospeech

    req = texttospeech.SynthesizeSpeechRequest(
        input=texttospeech.SynthesisInput(text=chunk),
        voice=texttospeech.VoiceSelectionParams(
            language_code=language_code,
            name=voice_name,
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        ),
    )
    resp = client.synthesize_speech(request=req)
    return bytes(resp.audio_content or b"")


def synth_google_chirp3_to_path(text: str, out_path: Path) -> bool:
    """
    Synthesize speech via Google Cloud TTS Chirp 3 voice.
    Returns True if valid MP3 was generated; False to allow fallback provider.
    """
    if not google_chirp3_ready():
        return False

    voice_name = cfg_str(
        "tts", "google_chirp3", "voice_name", default="vi-VN-Chirp3-HD-Aoede"
    ).strip()
    language_code = cfg_str("tts", "google_chirp3", "language_code", default="vi-VN").strip()

    chunks = _chunk_text(text)
    if not chunks:
        return False

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        client = _build_client()
        if len(chunks) == 1:
            audio = _synthesize_chunk(client, chunks[0], voice_name, language_code)
            out_path.unlink(missing_ok=True)
            out_path.write_bytes(audio)
        else:
            part_paths = [
                out_path.parent / f"{out_path.stem}_gc{i:03d}.mp3" for i in range(len(chunks))
            ]
            try:
                for i, chunk in enumerate(chunks):
                    audio = _synthesize_chunk(client, chunk, voice_name, language_code)
                    part_paths[i].unlink(missing_ok=True)
                    part_paths[i].write_bytes(audio)
                    if part_paths[i].stat().st_size < _MIN_OUTPUT_BYTES:
                        raise RuntimeError(
                            f"Google Chirp3 part {i} too small ({part_paths[i].stat().st_size} bytes)"
                        )

                out_path.unlink(missing_ok=True)
                if not _ffmpeg_concat_mp3(part_paths, out_path):
                    log.warning(
                        "Google Chirp3 multi-chunk: ffmpeg concat failed; using first part only for %s",
                        out_path.name,
                    )
                    out_path.write_bytes(part_paths[0].read_bytes())
            finally:
                for p in part_paths:
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass

        ok = out_path.exists() and out_path.stat().st_size >= _MIN_OUTPUT_BYTES
        if not ok:
            log.warning("Google Chirp3 output missing or too small: %s", out_path.name)
        return ok
    except Exception as exc:
        log.warning("Google Chirp3 failed for %s: %s", out_path.name, exc)
        return False
