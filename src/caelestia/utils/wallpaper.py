import json
import os
import random
import subprocess

from argparse import Namespace
from pathlib import Path
from typing import cast

from materialyoucolor.hct import Hct
from materialyoucolor.utils.color_utils import argb_from_rgb
from PIL import Image

from caelestia.utils.hypr import message
from caelestia.utils.material import get_colours_for_image
from caelestia.utils.colourfulness import get_variant
from caelestia.utils.paths import (
    compute_hash,
    user_config_path,
    wallpaper_link_path,
    wallpaper_path_path,
    wallpaper_thumbnail_path,
    wallpaper_type_path,
    wallpapers_cache_dir,
)
from caelestia.utils.scheme import Scheme, get_scheme
from caelestia.utils.theme import apply_colours


def is_valid_image(path: Path) -> bool:
    return path.is_file() and path.suffix in [".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".gif"]


def is_valid_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in [".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv"]


def check_wall(wall: Path, filter_size: tuple[int, int], threshold: float) -> bool:
    with Image.open(wall) as img:
        width, height = img.size
        return width >= filter_size[0] * threshold and height >= filter_size[1] * threshold


def get_wallpaper() -> str | None:
    try:
        return wallpaper_path_path.read_text()
    except IOError:
        return None


def get_wallpaper_type() -> str:
    """Get current wallpaper type (image or video)"""
    try:
        return wallpaper_type_path.read_text().strip()
    except IOError:
        return "image"


def restore_video_wallpaper() -> bool:
    """Check if video wallpaper was set. Returns True if video type.

    Note: Shell handles video playback natively via QtMultimedia.
    This function is kept for compatibility but no longer spawns mpvpaper.
    """
    if get_wallpaper_type() != "video":
        return False

    wall_path = get_wallpaper()
    if not wall_path:
        return False

    video = Path(wall_path)
    if not is_valid_video(video):
        return False

    # Shell handles video playback automatically when it reads path.txt
    # No external process needed
    return True


def get_wallpapers(args: Namespace) -> list[Path]:
    directory = Path(args.random)
    if not directory.is_dir():
        return []

    walls = [f for f in directory.rglob("*") if is_valid_image(f)]

    if args.no_filter:
        return walls

    monitors = cast(list[dict[str, int]], message("monitors"))
    filter_size = min(m["width"] for m in monitors), min(m["height"] for m in monitors)

    return [f for f in walls if check_wall(f, filter_size, args.threshold)]


def get_thumb(wall: Path, cache: Path) -> Path:
    thumb = cache / "thumbnail.jpg"

    if not thumb.exists():
        with Image.open(wall) as img:
            img = img.convert("RGB")
            img.thumbnail((128, 128), Image.Resampling.NEAREST)
            thumb.parent.mkdir(parents=True, exist_ok=True)
            img.save(thumb, "JPEG")

    return thumb


def get_smart_opts(wall: Path, cache: Path) -> dict:
    opts_cache = cache / "smart.json"

    try:
        return json.loads(opts_cache.read_text())
    except (IOError, json.JSONDecodeError):
        pass

    opts = {}

    with Image.open(get_thumb(wall, cache)) as img:
        opts["variant"] = get_variant(img)
        img.thumbnail((1, 1), Image.Resampling.LANCZOS)

        # Cast the pixel to a tuple of 3 integers to safely unpack it
        pixel = cast(tuple[int, int, int], img.getpixel((0, 0)))
        hct = Hct.from_int(argb_from_rgb(*pixel))

        opts["mode"] = "light" if hct.tone > 60 else "dark"

    opts_cache.parent.mkdir(parents=True, exist_ok=True)
    with opts_cache.open("w") as f:
        json.dump(opts, f)

    return opts


def get_colours_for_wall(wall: Path | str, no_smart: bool) -> None:
    wall = Path(wall)
    scheme = get_scheme()
    cache = wallpapers_cache_dir / compute_hash(wall)

    if wall.suffix.lower() == ".gif":
        wall = convert_gif(wall)

    name = "dynamic"

    if not no_smart:
        smart_opts = get_smart_opts(wall, cache)
        scheme = Scheme(
            {
                "name": name,
                "flavour": scheme.flavour,
                "mode": smart_opts["mode"],
                "variant": smart_opts["variant"],
                "colours": scheme.colours,
            }
        )

    return {
        "name": name,
        "flavour": scheme.flavour,
        "mode": scheme.mode,
        "variant": scheme.variant,
        "colours": get_colours_for_image(get_thumb(wall, cache), scheme),
    }


def convert_gif(wall: Path) -> Path:
    cache = wallpapers_cache_dir / compute_hash(wall)
    output_path = cache / "first_frame.png"

    if not output_path.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(wall) as img:
            try:
                img.seek(0)
            except EOFError:
                pass

            img = img.convert("RGB")
            img.save(output_path, "PNG")

    return output_path


def extract_video_frame(video: Path) -> Path:
    """Extract a single frame from video for thumbnail and color extraction"""
    cache = wallpapers_cache_dir / compute_hash(video)
    output_path = cache / "video_frame.png"

    if not output_path.exists():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Extract frame at 1 second using ffmpeg
            subprocess.run(
                [
                    "ffmpeg",
                    "-ss",
                    "1",  # Seek to 1 second
                    "-i",
                    str(video),
                    "-vframes",
                    "1",  # Extract 1 frame
                    "-q:v",
                    "2",  # High quality
                    str(output_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # If ffmpeg fails, create a fallback black image
            img = Image.new("RGB", (128, 128), color=(0, 0, 0))
            img.save(output_path, "PNG")

    return output_path


def set_wallpaper(wall: Path, no_smart: bool) -> None:
    # Make path absolute
    wall = Path(wall).resolve()

    if not is_valid_image(wall):
        raise ValueError(f'"{wall}" is not a valid image')

    # Use gif's 1st frame for thumb only
    wall_cache = convert_gif(wall) if wall.suffix.lower() == ".gif" else wall

    cache = wallpapers_cache_dir / compute_hash(wall_cache)

    # Generate thumbnail or get from cache
    thumb = get_thumb(wall_cache, cache)
    wallpaper_thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_thumbnail_path.unlink(missing_ok=True)
    wallpaper_thumbnail_path.symlink_to(thumb)

    # Update state files
    # IMPORTANT: Write type.txt BEFORE path.txt
    # Shell's FileView on path.txt triggers displayPath recalc
    # mediaType must already be "image" when that fires
    wallpaper_type_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_type_path.write_text("image")
    wallpaper_path_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_path_path.write_text(str(wall))
    wallpaper_link_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_link_path.unlink(missing_ok=True)
    wallpaper_link_path.symlink_to(wall)

    scheme = get_scheme()

    # Change mode and variant based on wallpaper colour
    if scheme.name == "dynamic" and not no_smart:
        smart_opts = get_smart_opts(wall_cache, cache)
        scheme.mode = smart_opts["mode"]
        scheme.variant = smart_opts["variant"]

    # Update colours
    scheme.update_colours()
    apply_colours(scheme.colours, scheme.mode)

    # Run custom post-hook if configured
    try:
        cfg = json.loads(user_config_path.read_text()).get("wallpaper", {})
        if post_hook := cfg.get("postHook"):
            subprocess.run(
                post_hook,
                shell=True,
                env={**os.environ, "WALLPAPER_PATH": str(wall)},
                stderr=subprocess.DEVNULL,
            )
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def set_video_wallpaper(video: Path, no_smart: bool) -> None:
    """Set a video as wallpaper (shell handles playback via QtMultimedia)"""
    # Make path absolute
    video = Path(video).resolve()

    if not is_valid_video(video):
        raise ValueError(f'"{video}" is not a valid video')

    # Extract a frame for theme colors
    video_frame = extract_video_frame(video)
    cache = wallpapers_cache_dir / compute_hash(video)

    # Generate thumbnail from extracted frame (for UI previews)
    thumb = get_thumb(video_frame, cache)
    wallpaper_thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_thumbnail_path.unlink(missing_ok=True)
    wallpaper_thumbnail_path.symlink_to(thumb)

    # Update state files
    # IMPORTANT: Write type.txt BEFORE path.txt
    # Shell's FileView on path.txt triggers display update
    # mediaType must already be "video" when that fires
    wallpaper_type_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_type_path.write_text("video")
    wallpaper_path_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_path_path.write_text(str(video))
    wallpaper_link_path.parent.mkdir(parents=True, exist_ok=True)
    wallpaper_link_path.unlink(missing_ok=True)
    wallpaper_link_path.symlink_to(video)

    scheme = get_scheme()

    # Change mode and variant based on video frame colour
    if scheme.name == "dynamic" and not no_smart:
        smart_opts = get_smart_opts(video_frame, cache)
        scheme.mode = smart_opts["mode"]
        scheme.variant = smart_opts["variant"]

    # Update colours
    scheme.update_colours()
    apply_colours(scheme.colours, scheme.mode)

    # Run custom post-hook if configured
    try:
        cfg = json.loads(user_config_path.read_text()).get("wallpaper", {})
        if post_hook := cfg.get("postHook"):
            subprocess.run(
                post_hook,
                shell=True,
                env={**os.environ, "WALLPAPER_PATH": str(video)},
                stderr=subprocess.DEVNULL,
            )
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def set_random(args: Namespace) -> None:
    wallpapers = get_wallpapers(args)

    if not wallpapers:
        raise ValueError("No valid wallpapers found")

    try:
        last_wall = wallpaper_path_path.read_text()
        wallpapers.remove(Path(last_wall))

        if not wallpapers:
            raise ValueError("Only valid wallpaper is current")
    except (FileNotFoundError, ValueError):
        pass

    set_wallpaper(random.choice(wallpapers), args.no_smart)
