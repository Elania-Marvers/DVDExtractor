from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    storage_root: Path
    storage_dirname: str
    storage_link: Path
    storage_path: Path
    poll_interval: float


def build_settings(host: str, port: int, poll_interval: float) -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    storage_root = Path(os.environ.get("DVD_EXTRACT_STORAGE_ROOT", "/Volumes/mac_s1")).expanduser()
    storage_dirname = os.environ.get("DVD_EXTRACT_STORAGE_DIRNAME", "dvd_mp4")
    storage_link = Path(os.environ.get("DVD_EXTRACT_STORAGE_LINK", str(project_root / "storage"))).expanduser()
    if not storage_link.is_absolute():
        storage_link = project_root / storage_link
    storage_path = _ensure_storage_link(storage_root, storage_dirname, storage_link)

    return Settings(
        host=host,
        port=port,
        storage_root=storage_root,
        storage_dirname=storage_dirname,
        storage_link=storage_link,
        storage_path=storage_path,
        poll_interval=poll_interval,
    )


def _ensure_storage_link(storage_root: Path, storage_dirname: str, storage_link: Path) -> Path:
    target = (storage_root / storage_dirname).resolve()
    if not storage_root.exists():
        if os.environ.get("DVD_EXTRACT_ALLOW_LOCAL_FALLBACK", "1") == "1":
            return _local_storage_fallback(storage_root, storage_link, "not mounted")
        raise RuntimeError(f"Storage root not found: {storage_root}")

    try:
        target.mkdir(parents=True, exist_ok=True)
        _assert_writable_directory(target)
    except OSError as exc:
        if os.environ.get("DVD_EXTRACT_ALLOW_LOCAL_FALLBACK", "1") == "1":
            return _local_storage_fallback(storage_root, storage_link, str(exc))
        raise RuntimeError(f"Storage path is not writable: {target}") from exc

    if storage_link.exists():
        if storage_link.is_symlink():
            try:
                current = storage_link.resolve()
            except OSError:
                current = None
            if current == target:
                return storage_link
            if os.environ.get("DVD_EXTRACT_FORCE_LINK", "0") == "1":
                storage_link.unlink(missing_ok=True)
            else:
                logging.warning(
                    "storage path %s is already a symlink to %s; leaving unchanged. Set DVD_EXTRACT_FORCE_LINK=1 to repoint.",
                    storage_link,
                    current,
                )
                _assert_writable_directory(storage_link)
                return storage_link
        elif storage_link.is_dir():
            logging.info(
                "Storage path %s already exists as a real directory (not symlink). Using it directly.",
                storage_link,
            )
            _assert_writable_directory(storage_link)
            return storage_link
        else:
            raise RuntimeError(f"Cannot prepare storage location at {storage_link}")

    storage_link.symlink_to(target)
    logging.info("Storage symlink created: %s -> %s", storage_link, target)
    return storage_link


def _local_storage_fallback(storage_root: Path, storage_link: Path, reason: str) -> Path:
    fallback = Path(os.environ.get("DVD_EXTRACT_LOCAL_FALLBACK", str(storage_link.parent / "storage_local"))).resolve()
    logging.warning(
        "External storage root %s unavailable (%s). Using local fallback %s.",
        storage_root,
        reason,
        fallback,
    )
    fallback.mkdir(parents=True, exist_ok=True)
    _assert_writable_directory(fallback)
    return fallback


def _assert_writable_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / f".dvdextractor-write-test-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
