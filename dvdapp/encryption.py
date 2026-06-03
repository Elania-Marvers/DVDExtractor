from __future__ import annotations

import re

from .common import run_cmd
from .native_probe import analyze_sample


def detect_encryption(device: str) -> dict:
    # Fast native probe first: optical tools such as lsdvd can block for several seconds
    # while the web UI is polling /api/drives.
    sample = analyze_sample(device)
    if sample and isinstance(sample, dict):
        entropy = sample.get("entropy")
        if isinstance(entropy, (int, float)):
            # Heuristic: encrypted payloads often have high entropy.
            encrypted = entropy >= 7.35
            return {
                "encrypted": encrypted,
                "method": "entropy",
                "entropy": entropy,
                "byte_sum": sample.get("byte_sum"),
            }

    # Fallback probe: lsdvd outputs explicit protection info when available.
    lsdvd_result = run_cmd(["lsdvd", device], timeout=3)
    if lsdvd_result.return_code == 0 and lsdvd_result.stdout.strip():
        txt = (lsdvd_result.stdout + "\n" + lsdvd_result.stderr).lower()
        encrypted = _extract_flag(txt)
        if encrypted is not None:
            return {
                "encrypted": encrypted,
                "method": "lsdvd",
                "raw": _read_line_excerpt(lsdvd_result.stdout),
            }

    return {"encrypted": None, "method": "unknown"}


def _extract_flag(text: str):
    patterns = [
        r"encrypted\s*:\s*(yes|no|true|false|1|0)",
        r"protection\s*:\s*(yes|no|none|present)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            val = m.group(1)
            if val in {"yes", "true", "1", "present"}:
                return True
            if val in {"no", "false", "0", "none"}:
                return False
    if "css" in text or "copy protection" in text:
        return True
    return None


def _read_line_excerpt(text: str, max_chars: int = 250) -> str:
    cleaned = text.replace("\r", " ").replace("\n", " ").strip()
    if not cleaned:
        return ""
    return cleaned[:max_chars]
