import json
from argparse import Namespace
from pathlib import Path

from caelestia.utils.wallpaper import (
    get_colours_for_wall,
    get_wallpaper,
    is_valid_video,
    restore_video_wallpaper,
    set_random,
    set_wallpaper,
    set_video_wallpaper,
)


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        if self.args.restore:
            # Restore video wallpaper if one was set (for boot)
            restore_video_wallpaper()
        elif self.args.print:
            print(json.dumps(get_colours_for_wall(self.args.print, self.args.no_smart)))
        elif self.args.file:
            # Auto-detect if file is video or image
            file_path = Path(self.args.file)
            if is_valid_video(file_path):
                set_video_wallpaper(file_path, self.args.no_smart)
            else:
                set_wallpaper(file_path, self.args.no_smart)
        elif self.args.random:
            set_random(self.args)
        else:
            print(get_wallpaper() or "No wallpaper set")
