from src.media.pexels_unsplash import ImageProvider
from src.utils.schemas import MicroStory, PipelinePaths
from pathlib import Path
from src.utils.helper import ensure_dirs

def _existing_scene_media_path(base_path: Path) -> Path | None:
    for suffix in (".mp4",):
        candidate = base_path.with_suffix(suffix)
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None

def _fetch_media_for_story(
    story: MicroStory,
    paths: PipelinePaths,
    provider: ImageProvider,
) -> list[Path]:
    ensure_dirs(paths)
    scene_paths: list[Path] = []
    for idx, scene in enumerate(story.scenes):
        scene_base_path = paths.images_dir / f"scene_{idx:02d}"
        existing_path = _existing_scene_media_path(scene_base_path)
        if existing_path is not None:
            scene_paths.append(existing_path)
            continue

        media_path = provider.fetch_scene_media(query=scene.imageQuery, out_path=scene_base_path)
        scene_paths.append(media_path)
        print(f"Fetched media for scene {idx:02d}: {media_path}")
    return scene_paths