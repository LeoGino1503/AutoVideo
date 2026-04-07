"""
Load `config.yaml` for non-secret settings. API keys read only from environment (.env).
Optional legacy env vars still override YAML when set (backward compatible).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml

_loaded: Optional[dict[str, Any]] = None


def _config_file_path() -> Path:
    raw = os.environ.get("AUTOVIDEO_CONFIG", "config.yaml").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def load_yaml_config(force: bool = False) -> dict[str, Any]:
    global _loaded
    if _loaded is not None and not force:
        return _loaded
    path = _config_file_path()
    if path.exists():
        text = path.read_text(encoding="utf-8")
        _loaded = yaml.safe_load(text) or {}
    else:
        _loaded = {}
    if not isinstance(_loaded, dict):
        _loaded = {}
    return _loaded


def _deep_get(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    d: Any = data
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _legacy_env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    return None if v is None else v


def cfg_raw(*keys: str, env_legacy: Optional[str] = None, default: Any = None) -> Any:
    """
    Read nested config: cfg_raw('pexels', 'media', env_legacy='PEXELS_MEDIA', default='photo').
    If env_legacy is set and non-empty in environment, that wins.
    """
    if env_legacy:
        ev = _legacy_env(env_legacy)
        if ev is not None and str(ev).strip() != "":
            return ev
    d = load_yaml_config()
    v = _deep_get(d, keys)
    return default if v is None else v


def cfg_str(*keys: str, env_legacy: Optional[str] = None, default: str = "") -> str:
    v = cfg_raw(*keys, env_legacy=env_legacy, default=default)
    if v is None:
        return default
    return str(v)


def cfg_bool(*keys: str, env_legacy: Optional[str] = None, default: bool = False) -> bool:
    if env_legacy:
        ev = _legacy_env(env_legacy)
        if ev is not None and str(ev).strip() != "":
            return str(ev).strip().lower() in {"1", "true", "yes", "y", "on"}
    v = cfg_raw(*keys, env_legacy=None, default=None)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def cfg_float(*keys: str, env_legacy: Optional[str] = None, default: float = 0.0) -> float:
    if env_legacy:
        ev = _legacy_env(env_legacy)
        if ev is not None and str(ev).strip() != "":
            try:
                return float(ev)
            except ValueError:
                pass
    v = cfg_raw(*keys, env_legacy=None, default=None)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def cfg_int(*keys: str, env_legacy: Optional[str] = None, default: int = 0) -> int:
    if env_legacy:
        ev = _legacy_env(env_legacy)
        if ev is not None and str(ev).strip() != "":
            try:
                return int(ev)
            except ValueError:
                pass
    v = cfg_raw(*keys, env_legacy=None, default=None)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def env_api_key(name: str) -> str:
    """Secrets: only from process environment / .env."""
    v = os.environ.get(name)
    return v if v is not None else ""
