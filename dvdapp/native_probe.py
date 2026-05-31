from __future__ import annotations

import json
from pathlib import Path

from .common import run_cmd

ANALYZER = (Path(__file__).resolve().parents[1] / "native" / "build" / "dvd_entropy").resolve()


def analyze_sample(device: str, sample_bytes: int = 4 * 1024 * 1024) -> dict | None:
    if not ANALYZER.exists():
        return None

    result = run_cmd([str(ANALYZER), device, str(sample_bytes)], timeout=12)
    if result.return_code != 0:
        return None

    try:
        return json.loads(result.stdout.strip())
    except Exception:
        return None
