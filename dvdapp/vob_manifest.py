from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

from .common import run_cmd

LOGGER = logging.getLogger(__name__)

MANIFEST_TOOL = (Path(__file__).resolve().parents[1] / "native" / "build" / "dvd_vob_manifest").resolve()
VOB_RE = re.compile(r"^VTS_(\d{1,2})_(\d{1,2})\.VOB$", re.IGNORECASE)


def _safe_float_or_none(value: str | None) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _run_native_manifest(video_ts: Path) -> list[tuple[int, list[Path]]]:
    if not MANIFEST_TOOL.exists():
        return []

    result = run_cmd([str(MANIFEST_TOOL), str(video_ts)], timeout=6)
    if result.return_code != 0:
        return []
    stdout = (result.stdout or "").strip()
    if not stdout:
        return []

    try:
        payload = json.loads(stdout)
    except Exception:
        LOGGER.debug("native manifest invalid JSON for %s", video_ts)
        return []

    titles = payload.get("titles")
    if not isinstance(titles, list):
        return []

    mapped: list[tuple[int, list[Path]]] = []
    for item in titles:
        if not isinstance(item, dict):
            continue
        title_id = _safe_float_or_none(str(item.get("id")))
        if title_id is None:
            continue

        parts_raw = item.get("parts")
        if not isinstance(parts_raw, list):
            continue

        parts = [Path(str(path)) for path in parts_raw if isinstance(path, str)]
        if not parts:
            continue
        mapped.append((title_id, parts))

    return mapped


def _fallback_scan(video_ts: Path) -> list[tuple[int, list[Path]]]:
    vob_files = sorted(video_ts.glob("VTS_*_*.VOB"))
    if not vob_files:
        return []

    title_parts: dict[int, list[tuple[int, Path]]] = {}
    for file in vob_files:
        match = VOB_RE.match(file.name)
        if not match:
            continue
        title_id = int(match.group(1))
        part_no = int(match.group(2))
        if part_no == 0:
            continue
        title_parts.setdefault(title_id, []).append((part_no, file))

    candidates: list[tuple[int, int, list[Path]]] = []
    for title_id, parts in title_parts.items():
        if not parts:
            continue
        sorted_parts = [path for _, path in sorted(parts, key=lambda item: item[0])]
        candidates.append((title_id, len(sorted_parts), sorted_parts))

    candidates.sort(key=lambda item: (-item[1], item[0]))
    return [(title_id, parts) for title_id, _, parts in candidates]


def scan_vob_titles_from_video_ts(video_ts: Path) -> list[tuple[int, list[Path]]]:
    try:
        video_ts = Path(video_ts)
    except Exception as exc:
        raise RuntimeError(f"invalid VIDEO_TS path: {exc}")

    if not video_ts.exists() or not video_ts.is_dir():
        return []

    titles = _run_native_manifest(video_ts)
    if not titles:
        titles = _fallback_scan(video_ts)

    return titles
