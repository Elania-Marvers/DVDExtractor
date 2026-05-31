from __future__ import annotations

"""Abstraction Python autour de l'exécutable natif de dump DVD.

L'objectif de ce module est double:
- détecter si l'exécutable de dump C++ est disponible,
- fournir une API simple pour lister les titres détectés et préparer une commande
  de dump native "propre".
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .common import run_cmd

LOGGER = logging.getLogger(__name__)


NATIVE_DVD_DUMP = (Path(__file__).resolve().parents[1] / "native" / "build" / "dvd_reader_dump").resolve()


@dataclass(frozen=True)
class NativeTitleProbe:
    """Informations de base sur un titre DVD lues depuis libdvdread."""

    title: int
    blocks: int
    size_bytes: int


def is_native_dvd_dump_available() -> bool:
    """Retourne True si le binaire natif de dump est compilé."""

    return NATIVE_DVD_DUMP.exists()


def dump_command_for_title(source: str, title: int, output: str) -> list[str]:
    """Construit la commande de dump natif d'un titre.

    Args:
        source: Device ou dossier monté (ex: /dev/rdisk3).
        title: Numéro de titre (VTS_XX).
        output: Fichier brut en sortie.
    """

    return [
        str(NATIVE_DVD_DUMP),
        "--title",
        str(title),
        "--output",
        str(output),
        source,
    ]


def list_title_candidates(source: str) -> list[NativeTitleProbe]:
    """Liste les titres détectés par libdvdread.

    Le binaire natif retourne un JSON: {"titles": [{"id": 1, "size": ...}, ...]}
    """

    if not is_native_dvd_dump_available():
        return []

    result = run_cmd([str(NATIVE_DVD_DUMP), "--list-titles", source], timeout=20)
    if result.return_code != 0:
        LOGGER.debug("native dvd list-title failed for %s", source)
        return []

    payload_text = (result.stdout or "").strip()
    if not payload_text:
        return []

    try:
        payload = json.loads(payload_text)
    except Exception as exc:
        LOGGER.debug("invalid json from native list-title %s: %s", source, exc)
        return []

    titles_raw = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(titles_raw, list):
        return []

    candidates: list[NativeTitleProbe] = []
    for item in titles_raw:
        if not isinstance(item, dict):
            continue

        try:
            title = int(item.get("id"))
            blocks = int(item.get("blocks", 0) or 0)
            size_bytes = int(item.get("size", 0) or 0)
        except Exception:
            continue

        if title <= 0 or blocks <= 0 or size_bytes <= 0:
            continue

        candidates.append(NativeTitleProbe(title=title, blocks=blocks, size_bytes=size_bytes))

    candidates.sort(key=lambda x: (x.size_bytes, x.blocks), reverse=True)
    return candidates
