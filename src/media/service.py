from src.utils.config_loader import cfg_str
from src.media.pexels_unsplash import ImageProvider
from src.utils.schemas import MicroStory, PipelinePaths
from pathlib import Path
from src.utils.helper import ensure_dirs

def _fetch_media_for_story(
    story: MicroStory,
    paths: PipelinePaths,
    provider: ImageProvider,
) -> list[Path]:
    ensure_dirs(paths)
    scene_paths: list[Path] = []
    for idx, scene in enumerate(story.scenes):
        media_path = provider.fetch_scene_media(
            query=scene.imageQuery,
            out_path=paths.images_dir / f"scene_{idx:02d}",
        )
        scene_paths.append(media_path)
    return scene_paths