from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Tuple

from .common import run_cmd

LOGGER = logging.getLogger(__name__)

HOMEBREW_TOOL = (Path(__file__).resolve().parents[1] / "native" / "build" / "dvd_homebrew").resolve()


def is_homebrew_available() -> bool:
    """Retourne True si le binaire homebrew est disponible."""

    return HOMEBREW_TOOL.exists()


def build_concat_command(output: str, parts: list[str] | list[Path]) -> list[str] | None:
    if not output or not parts:
        return None

    parts_clean: list[str] = []
    for item in parts:
        p = Path(item)
        if p.exists() and p.is_file():
            parts_clean.append(str(p))

    if len(parts_clean) != len(parts):
        return None

    return [str(HOMEBREW_TOOL), "concat", "--output", str(output), *parts_clean]


def build_copy_command(output: str, source: str | Path) -> list[str] | None:
    source_path = Path(source)
    if not output or not source_path.is_file():
        return None

    return [str(HOMEBREW_TOOL), "copy", "--source", str(source_path), "--output", str(output)]


def build_demux_command(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    extract_payloads: bool = True,
    max_bytes: int | None = None,
) -> list[str] | None:
    source_path = Path(input_path)
    if not source_path.is_file() or not is_homebrew_available():
        return None

    args = [str(HOMEBREW_TOOL), "demux", "--input", str(source_path)]
    if extract_payloads:
        if not output_dir:
            return None
        args.extend(["--output-dir", str(output_dir)])
    else:
        args.append("--no-payload")
    if max_bytes and max_bytes > 0:
        args.extend(["--max-bytes", str(int(max_bytes))])
    return args


def build_extract_command(
    video_ts: str | Path,
    output: str,
    title: int | None = None,
    ffmpeg: str | None = None,
    work_dir: str | Path | None = None,
) -> list[str] | None:
    if not output or not is_homebrew_available():
        return None

    video_ts_path = Path(video_ts)
    if not video_ts_path.is_dir():
        return None

    args = [
        str(HOMEBREW_TOOL),
        "extract",
        "--video-ts",
        str(video_ts_path),
        "--output",
        str(output),
    ]
    if title:
        args.extend(["--title", str(int(title))])
    if ffmpeg:
        args.extend(["--ffmpeg", str(ffmpeg)])
    if work_dir:
        args.extend(["--work-dir", str(work_dir)])
    return args


def scan_video_ts(video_ts: str | Path) -> list[tuple[int, list[Path]]]:
    if not is_homebrew_available():
        return []

    try:
        result = run_cmd([str(HOMEBREW_TOOL), "scan", str(video_ts)], timeout=12)
    except Exception as exc:
        LOGGER.debug("homebrew scan command failed for %s: %s", video_ts, exc)
        return []

    if result.return_code != 0:
        LOGGER.debug("homebrew scan non-zero for %s: %s", video_ts, result.stderr)
        return []

    payload_text = (result.stdout or "").strip()
    if not payload_text:
        return []

    try:
        payload = json.loads(payload_text)
    except Exception as exc:
        LOGGER.debug("invalid homebrew scan JSON for %s: %s", video_ts, exc)
        return []

    titles_raw = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(titles_raw, list):
        return []

    mapped: list[tuple[int, list[Path]]] = []
    for item in titles_raw:
        if not isinstance(item, dict):
            continue
        try:
            title_id = int(item.get("id", 0))
        except Exception:
            continue
        parts_raw = item.get("parts")
        if not isinstance(parts_raw, list):
            continue

        parts = [Path(str(p)) for p in parts_raw if isinstance(p, str)]
        if not parts:
            continue
        mapped.append((title_id, parts))

    return mapped
