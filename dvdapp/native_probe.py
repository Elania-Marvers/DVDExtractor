from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import run_cmd

_NATIVE_ROOT = (Path(__file__).resolve().parents[1] / "native" / "build").resolve()
SIGNAL_PROBE = _NATIVE_ROOT / "dvd_signal_probe"
ANALYZER = _NATIVE_ROOT / "dvd_entropy"


def _resolve_analyzer() -> Path | None:
    if SIGNAL_PROBE.exists():
        return SIGNAL_PROBE
    if ANALYZER.exists():
        return ANALYZER
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def analyze_sample(device: str, sample_bytes: int = 4 * 1024 * 1024) -> dict | None:
    analyzer = _resolve_analyzer()
    if analyzer is None:
        return None

    result = run_cmd([str(analyzer), device, str(sample_bytes)], timeout=12)
    stdout_payload = _safe_parse_json(result.stdout)
    if result.return_code != 0:
        if isinstance(stdout_payload, dict):
            return {
                "ok": False,
                "analyzer": analyzer.name,
                "return_code": result.return_code,
                "error_code": stdout_payload.get("error_code"),
                "stage": stdout_payload.get("stage"),
                "component": stdout_payload.get("component"),
                "message": stdout_payload.get("message"),
                "detail": stdout_payload.get("detail"),
                "raw_stdout": result.stdout.strip(),
                "raw_stderr": result.stderr.strip(),
            }
        return {
            "ok": False,
            "analyzer": analyzer.name,
            "return_code": result.return_code,
            "message": result.stderr.strip() or "native analyzer failed",
            "raw_stdout": result.stdout.strip(),
        }

    # Sortie succès attendue:
    # - JSON plat ({"bytes":..., "entropy":...})
    # - ou JSON enrichi ({"ok":true, ...})
    try:
        payload = stdout_payload
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    return {
        "ok": payload.get("ok", True),
        "analyzer": analyzer.name,
        "path": device,
        "bytes": _safe_int(payload.get("bytes")) or 0,
        "entropy": _safe_float(payload.get("entropy")),
        "byte_sum": _safe_int(payload.get("byte_sum")),
        "pack_sync_count": _safe_int(payload.get("pack_sync_count")) or 0,
        "ts_sync_count": _safe_int(payload.get("ts_sync_count")) or 0,
        "max_zero_run": _safe_int(payload.get("max_zero_run")) or 0,
        "error_code": _safe_int(payload.get("error_code")),
    }


def _safe_parse_json(payload: str):
    try:
        return json.loads(payload.strip())
    except Exception:
        return None
