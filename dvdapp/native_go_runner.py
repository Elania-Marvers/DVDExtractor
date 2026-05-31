from __future__ import annotations

import logging
import shutil
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

TOOLS_DIR = (Path(__file__).resolve().parents[1] / "bin")
GO_TOOL = TOOLS_DIR / "dvd_homebrew_runner"


def is_go_runner_available() -> bool:
    """Retourne True si le binaire go_homebrew_runner est disponible."""
    return GO_TOOL.exists() and os.access(str(GO_TOOL), os.X_OK)


def build_extract_command(video_ts: str | Path, output: str, title: int | None = None, ffmpeg: str | None = None) -> list[str] | None:
    if not output:
        return None

    if not Path(video_ts).is_dir():
        return None

    if not is_go_runner_available():
        return None

    args: list[str] = [
        str(GO_TOOL),
        "extract",
        "--video-ts",
        str(video_ts),
        "--output",
        str(output),
        "--timeout",
        "1200",
    ]

    if title:
        args.extend(["--title", str(int(title))])
    if ffmpeg:
        args.extend(["--ffmpeg", str(ffmpeg)])

    return args
