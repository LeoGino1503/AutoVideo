from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
from PIL import Image, ImageDraw, ImageFont

from src.utils.config_loader import cfg_int, cfg_str, env_api_key


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _safe_query(q: str) -> str:
    q = q.strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q[:140]


def _try_load_font(size: int) -> ImageFont.ImageFont:
    # DejaVu fonts are installed in Dockerfile; locally it may still exist.
    for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def create_placeholder_image(out_path: Path, query: str, size=(1080, 1920)) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, (10, 12, 20))
    draw = ImageDraw.Draw(img)

    # Simple gradient-ish overlay
    for y in range(size[1]):
        shade = int(20 + (y / size[1]) * 60)
        draw.line([(0, y), (size[0], y)], fill=(shade // 2, shade // 3, shade))

    font_title = _try_load_font(46)
    font_body = _try_load_font(30)

    title = "Murphy Short"
    draw.text((60, 80), title, font=font_title, fill=(245, 245, 255))

    # Wrap query
    q = query.strip()
    max_chars = 36
    lines = [q[i : i + max_chars] for i in range(0, len(q), max_chars)] or [q]
    y0 = 320
    for i, line in enumerate(lines[:20]):
        draw.text((60, y0 + i * 38), line, font=font_body, fill=(220, 220, 230))

    img.save(out_path, quality=92)
    return out_path


@dataclass(frozen=True)
class ImageSourceResult:
    url: str


class ImageProvider:
    def __init__(self, cache_dir: Optional[Path] = None):
        if cache_dir is not None:
            self.cache_dir = cache_dir
        else:
            raw = cfg_str("paths", "image_cache_dir", env_legacy="IMAGE_CACHE_DIR", default="").strip()
            self.cache_dir = (
                Path(raw).expanduser()
                if raw
                else Path.home() / ".cache" / "murphy_api" / "images"
            )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def from_env(name: str = "pexels_unsplash") -> "ImageProvider":
        # Currently only one provider bundle exists.
        return ImageProvider()

    def _cache_path_for_query(self, query: str) -> Path:
        return self.cache_dir / f"{_hash_text(query)}.jpg"

    def _cache_path_for_video_query(self, query: str) -> Path:
        # Distinct cache key so photo/video never collide for same text.
        return self.cache_dir / f"{_hash_text('pexels_video:' + query)}.mp4"

    def _download_to(self, url: str, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return out_path

    def _search_pexels_photo(self, query: str) -> Optional[ImageSourceResult]:
        """Pexels Photos API: GET https://api.pexels.com/v1/search"""
        api_key = env_api_key("PEXELS_API_KEY")
        if not api_key:
            return None

        q = _safe_query(query)
        url = "https://api.pexels.com/v1/search"
        params = {"query": q, "per_page": 1}
        headers = {"Authorization": api_key}
        r = requests.get(url, headers=headers, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        photos = data.get("photos") or []
        if not photos:
            return None
        photo = photos[0]
        src = photo.get("src") or {}
        best = src.get("original") or src.get("large") or src.get("medium")
        return ImageSourceResult(url=best) if best else None

    @staticmethod
    def _pick_best_pexels_video_mp4_url(
        video_files: list[dict[str, Any]],
        *,
        target_w: int = 1080,
        target_h: int = 1920,
    ) -> Optional[str]:
        """
        From Pexels Video `video_files`, pick one MP4 URL best suited for 9:16 Shorts.
        See: https://www.pexels.com/api/documentation/#videos-search
        """
        candidates = [
            f
            for f in video_files
            if (f.get("file_type") == "video/mp4") and f.get("link")
        ]
        if not candidates:
            return None

        target_ar = target_h / max(target_w, 1)

        def sort_key(f: dict[str, Any]) -> tuple:
            w = int(f.get("width") or 0)
            h = int(f.get("height") or 0)
            if w <= 0 or h <= 0:
                return (9, 9.0, 0, 0)
            ar = h / w
            # Prefer portrait-ish; close to 9:16
            ar_pen = abs(ar - target_ar)
            covers = 1 if (w >= target_w and h >= target_h) else 0
            q = str(f.get("quality") or "").lower()
            q_rank = {"uhd": 0, "hd": 1, "sd": 2}.get(q, 3)
            return (-covers, q_rank, ar_pen, -(w * h))

        best = sorted(candidates, key=sort_key)[0]
        return str(best["link"])

    def _search_pexels_video_download_url(self, query: str) -> Optional[str]:
        """
        Pexels Videos API: GET https://api.pexels.com/v1/videos/search
        """
        api_key = env_api_key("PEXELS_API_KEY")
        if not api_key:
            return None

        q = _safe_query(query)
        url = "https://api.pexels.com/v1/videos/search"
        orientation = cfg_str(
            "pexels", "video_orientation", env_legacy="PEXELS_VIDEO_ORIENTATION", default="portrait"
        ).strip().lower()
        if orientation not in {"landscape", "portrait", "square"}:
            orientation = "portrait"

        size = cfg_str("pexels", "video_size", env_legacy="PEXELS_VIDEO_SIZE", default="").strip().lower()
        per_page = cfg_int("pexels", "video_per_page", env_legacy="PEXELS_VIDEO_PER_PAGE", default=3)
        params: dict[str, Any] = {
            "query": q,
            "per_page": max(1, min(80, per_page)),
            "orientation": orientation,
            "page": 1,
        }
        if size in {"large", "medium", "small"}:
            params["size"] = size

        headers = {"Authorization": api_key}
        r = requests.get(url, headers=headers, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        videos = data.get("videos") or []
        for video in videos:
            files = video.get("video_files") or []
            picked = self._pick_best_pexels_video_mp4_url(files)
            if picked:
                return picked
        return None

    def _search_unsplash(self, query: str) -> Optional[ImageSourceResult]:
        api_key = env_api_key("UNSPLASH_ACCESS_KEY")
        if not api_key:
            return None

        q = _safe_query(query)
        url = "https://api.unsplash.com/search/photos"
        params = {"query": q, "per_page": 1}
        headers = {"Authorization": f"Client-ID {api_key}"}
        r = requests.get(url, headers=headers, params=params, timeout=60)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        photo = results[0]
        urls = photo.get("urls") or {}
        best = urls.get("regular") or urls.get("full") or urls.get("small")
        return ImageSourceResult(url=best) if best else None

    def fetch_image(self, query: str, out_path: Path) -> Path:
        """
        Returns a local image path for the query.
        - If cache hit exists, we copy it to out_path.
        - If API keys missing or request fails, we generate a placeholder.
        """
        out_path = Path(out_path)
        cache_path = self._cache_path_for_query(query)
        if cache_path.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Copy bytes to keep paths independent from cache pruning.
            out_path.write_bytes(cache_path.read_bytes())
            return out_path

        # Try Pexels first, then Unsplash.
        source = self._search_pexels_photo(query) or self._search_unsplash(query)
        if source and source.url:
            try:
                tmp_path = cache_path
                self._download_to(source.url, tmp_path)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(tmp_path.read_bytes())
                return out_path
            except Exception:
                # Fall through to placeholder
                pass

        return create_placeholder_image(out_path, query)

    def _try_fetch_pexels_video(self, query: str, out_path: Path) -> Optional[Path]:
        """
        Download first suitable portrait MP4 from Pexels Videos API.
        Returns out_path on success.
        """
        out_path = Path(out_path)
        if out_path.suffix.lower() != ".mp4":
            out_path = out_path.with_suffix(".mp4")

        cache_path = self._cache_path_for_video_query(query)
        if cache_path.exists() and cache_path.stat().st_size > 0:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(cache_path.read_bytes())
            return out_path

        mp4_url = self._search_pexels_video_download_url(query)
        if not mp4_url:
            return None
        try:
            self._download_to(mp4_url, cache_path)
            if not cache_path.exists() or cache_path.stat().st_size == 0:
                return None
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(cache_path.read_bytes())
            return out_path
        except Exception:
            return None

    def fetch_scene_media(self, query: str, out_path: Path) -> Path:
        """
        Resolve scene visual: photo (jpg) or stock video (mp4) from Pexels when configured.

        Env:
        - PEXELS_MEDIA: ``photo`` (default) | ``video``
        When ``video``, uses https://api.pexels.com/v1/videos/search and falls back to photos
        if no video is available.
        """
        mode = cfg_str("pexels", "media", env_legacy="PEXELS_MEDIA", default="photo").strip().lower()
        if mode == "video":
            video_path = self._try_fetch_pexels_video(query, out_path)
            if video_path is not None:
                return video_path
            img_out = Path(out_path).with_suffix(".jpg")
            return self.fetch_image(query, img_out)

        return self.fetch_image(query, Path(out_path).with_suffix(".jpg"))

