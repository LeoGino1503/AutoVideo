from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from src.utils.config_loader import cfg_bool, cfg_str, env_api_key

log = logging.getLogger(__name__)

# ElevenLabs payload safety margin (official limits vary by tier).
_ELEVEN_CHUNK_CHARS = 3500
_MIN_OUTPUT_BYTES = 400


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def elevenlabs_ready() -> bool:
    if not cfg_bool("tts", "elevenlabs", "enabled", default=False):
        return False
    return bool(env_api_key("ELEVENLABS_API_KEY").strip())


def _chunk_text(text: str) -> list[str]:
    t = _normalize(text)
    if not t:
        return []
    return [t[i : i + _ELEVEN_CHUNK_CHARS] for i in range(0, len(t), _ELEVEN_CHUNK_CHARS)]


def _normalize_elevenlabs_model_id(model_id: str) -> str:
    """
    Docs use underscores (e.g. ``eleven_turbo_v2_5``). YAML typos like ``v2.5`` break the API.
    """
    m = model_id.strip()
    if m.startswith("eleven_") and "." in m:
        m = m.replace(".", "_")
    return m


def _model_priority_list() -> list[str]:
    """Primary first, then secondary, then legacy ``model_id`` (deduped)."""
    seen: set[str] = set()
    out: list[str] = []
    for key, default in (
        ("model_id_primary", "eleven_multilingual_v2"),
        ("model_id_secondary", ""),
        ("model_id", ""),
    ):
        m = cfg_str("tts", "elevenlabs", key, default=default).strip()
        m = _normalize_elevenlabs_model_id(m)
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out if out else ["eleven_multilingual_v2"]


def _log_elevenlabs_error(model_id: str, out_name: str, exc: BaseException) -> None:
    msg = str(exc).lower()
    if (
        "402" in str(exc)
        or "payment_required" in msg
        or "paid_plan_required" in msg
        or "free users cannot use library voices" in msg
    ):
        log.warning(
            "ElevenLabs 402 / paid plan: voice này hoặc API cần gói trả phí. "
            "Đổi sang voice Instant (free API), nâng cấp ElevenLabs, hoặc "
            "đặt tts.elevenlabs.enabled: false để dùng Edge TTS. (%s, model=%s)",
            out_name,
            model_id,
        )
        return
    if "invalid_uid" in msg or ("invalid id" in msg and "model" in msg):
        log.warning(
            "ElevenLabs model id không hợp lệ (%s). Kiểm tra "
            "https://elevenlabs.io/docs/models (vd. eleven_turbo_v2_5). File: %s",
            model_id,
            out_name,
        )
        return
    log.warning("ElevenLabs model %s failed for %s: %s", model_id, out_name, exc)


def _ffmpeg_concat_mp3(part_paths: list[Path], out_path: Path) -> bool:
    if not part_paths or shutil.which("ffmpeg") is None:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / f"{out_path.stem}_el_concat.txt"
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


def _write_iterator_to_file(stream, out_path: Path) -> int:
    total = 0
    with open(out_path, "wb") as f:
        for chunk in stream:
            if chunk:
                b = chunk if isinstance(chunk, (bytes, bytearray)) else bytes(chunk)
                f.write(b)
                total += len(b)
    return total


def _synthesize_with_model(
    client: object,
    chunks: list[str],
    out_path: Path,
    *,
    voice_id: str,
    model_id: str,
    output_format: str,
) -> bool:
    out_path = Path(out_path)
    try:
        if len(chunks) == 1:
            audio = client.text_to_speech.convert(
                voice_id=voice_id,
                text=chunks[0],
                model_id=model_id,
                output_format=output_format,
            )
            out_path.unlink(missing_ok=True)
            _write_iterator_to_file(audio, out_path)
        else:
            part_paths = [
                out_path.parent / f"{out_path.stem}_el{i:03d}.mp3" for i in range(len(chunks))
            ]
            try:
                for i, chunk in enumerate(chunks):
                    audio = client.text_to_speech.convert(
                        voice_id=voice_id,
                        text=chunk,
                        model_id=model_id,
                        output_format=output_format,
                    )
                    part_paths[i].unlink(missing_ok=True)
                    n = _write_iterator_to_file(audio, part_paths[i])
                    if n < _MIN_OUTPUT_BYTES:
                        raise RuntimeError(f"ElevenLabs part {i} too small ({n} bytes)")
                out_path.unlink(missing_ok=True)
                if not _ffmpeg_concat_mp3(part_paths, out_path):
                    log.warning(
                        "ElevenLabs multi-chunk: ffmpeg concat failed; using first part only for %s",
                        out_path.name,
                    )
                    shutil.copyfile(part_paths[0], out_path)
            finally:
                for p in part_paths:
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass

        ok = out_path.exists() and out_path.stat().st_size >= _MIN_OUTPUT_BYTES
        if not ok:
            log.warning(
                "ElevenLabs model %s output missing or too small: %s", model_id, out_path.name
            )
        return ok
    except Exception as exc:
        _log_elevenlabs_error(model_id, out_path.name, exc)
        return False


def synth_elevenlabs_to_path(text: str, out_path: Path) -> bool:
    """
    Synthesize speech via ElevenLabs SDK. Tries ``model_id_primary``, then ``model_id_secondary``.
    Returns True if any model produced valid MP3; False to fall back to Edge TTS / silence.
    """
    if not elevenlabs_ready():
        return False

    from elevenlabs.client import ElevenLabs

    api_key = env_api_key("ELEVENLABS_API_KEY").strip()
    voice_id = cfg_str("tts", "elevenlabs", "voice_id", default="JBFqnCBsd6RMkjVDRZzb")
    output_format = cfg_str("tts", "elevenlabs", "output_format", default="mp3_44100_128")

    chunks = _chunk_text(text)
    if not chunks:
        return False

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    client = ElevenLabs(api_key=api_key)

    for model_id in _model_priority_list():
        out_path.unlink(missing_ok=True)
        if _synthesize_with_model(
            client,
            chunks,
            out_path,
            voice_id=voice_id,
            model_id=model_id,
            output_format=output_format,
        ):
            log.debug("ElevenLabs used model %s for %s", model_id, out_path.name)
            return True

    log.warning("All ElevenLabs models failed for %s", out_path.name)
    return False
