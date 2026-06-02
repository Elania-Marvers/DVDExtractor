from __future__ import annotations

import glob
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import List

from .common import run_cmd


OPTICAL_KEYWORDS = ("dvd", "bluray", "cd", "cd-rom", "dvd-r", "dvd+rw", "optical", "disc", "laser")


@dataclass
class DriveInfo:
    device: str
    name: str
    drive_type: str
    inserted: bool
    state: str
    source: str


class DriveScanner:
    DRUTIL_HEADER = re.compile(r"^\s*(\d+)\:\s*(.+)$")
    KEY_VALUE = re.compile(r"^\s*([^:]+)\:\s*(.*)$")
    DISK_DEVICE_RE = re.compile(r"^/dev/rdisk(\d+)(?:s\d+)?$")
    FAST_PROBE_TIMEOUT_SECONDS = 3

    def list_drives(self) -> List[dict]:
        drives = self._from_drutil()
        if drives:
            normalized = self._normalize_and_filter(drives)
            if normalized:
                self._apply_disc_labels(normalized)
                return [drive.__dict__ for drive in normalized]

            logging.warning("drutil returned results but no optical candidate was detected.")

        logging.warning("drutil unavailable or no drives found; using fallback probe.")
        mounted = self._from_mounted_video_ts()
        if mounted:
            normalized = self._normalize_and_filter(mounted)
            self._apply_disc_labels(normalized)
            return [drive.__dict__ for drive in normalized]

        drives = self._from_diskutil_fallback()
        normalized = self._normalize_and_filter(drives)
        self._apply_disc_labels(normalized)
        return [drive.__dict__ for drive in normalized]

    def _apply_disc_labels(self, drives: List[DriveInfo]) -> None:
        for drive in drives:
            if drive.source == "mount" and drive.name:
                continue

            label = self._guess_disc_label(drive.device)
            if not label:
                continue
            if not drive.inserted:
                continue
            if label.lower() in {"unknown", "no media"}:
                continue
            if drive.name and drive.name.lower() not in {"unknown", "dvd", "cd"}:
                drive.name = f"{drive.name} ({label})"
            else:
                drive.name = label

    def _normalize_and_filter(self, drives: List[DriveInfo]) -> List[DriveInfo]:
        normalized: List[DriveInfo] = []
        seen = set()
        for drive in drives:
            normalized_device = self._normalize_device(drive.device)
            if not normalized_device:
                continue

            if not self._is_optical_candidate(drive):
                continue

            drive.device = normalized_device
            drive.name = self._pick_display_name(drive.name)
            key = (drive.device, drive.name, drive.drive_type, drive.state, drive.inserted)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(drive)

        return normalized

    @staticmethod
    def _pick_display_name(name: str) -> str:
        label = (name or "").strip()
        return label if label else "DVD"

    @classmethod
    def _normalize_device(cls, device: str) -> str:
        match = cls.DISK_DEVICE_RE.match((device or "").strip())
        if not match:
            return ""
        return f"/dev/rdisk{match.group(1)}"

    @staticmethod
    def _contains_optical_keyword(text: str) -> bool:
        lowered = (text or "").lower()
        if not lowered:
            return False
        return any(keyword in lowered for keyword in OPTICAL_KEYWORDS)

    @classmethod
    def _is_optical_candidate(cls, drive: DriveInfo) -> bool:
        if drive.inserted:
            return cls._contains_optical_keyword(drive.name) or cls._contains_optical_keyword(drive.drive_type) or cls._looks_like_title(drive.name)

        # Les lecteurs sans média ne sont gardés que si le type est explicitement optique.
        if not drive.state:
            return False
        if drive.state.lower() in {"empty", "no media"}:
            return cls._contains_optical_keyword(drive.drive_type)
        if cls._contains_optical_keyword(drive.name) or cls._contains_optical_keyword(drive.drive_type):
            return True
        return False

    @staticmethod
    def _looks_like_title(name: str) -> bool:
        value = (name or "").strip()
        if len(value) < 3:
            return False

        # Un titre de disque réel contient souvent un motif texte long, parfois avec underscores ou tirets.
        # Les lecteurs SSD/HDD affichent souvent des noms de volume plus courts et techniques.
        if value.lower().startswith("disk"):
            return False

        tokens = re.split(r"\s+", value)
        if len(tokens) >= 2:
            return True

        # Cas de titres composés d'un seul token mais volumineux (ex: `HUNGER_GAMES`).
        return len(value) >= 10

    def _from_drutil(self) -> List[DriveInfo]:
        if not which("drutil"):
            return []

        result = run_cmd(["drutil", "list"], timeout=8)
        if result.return_code != 0:
            return []

        entries: List[DriveInfo] = []
        current = None
        for line in result.stdout.splitlines():
            header_match = self.DRUTIL_HEADER.match(line)
            if header_match:
                if current:
                    entries.append(current)
                current = DriveInfo(
                    device="",
                    name=header_match.group(2).strip(),
                    drive_type="",
                    inserted=False,
                    state="unknown",
                    source="drutil",
                )
                continue

            if not current:
                continue

            key_value = self.KEY_VALUE.match(line)
            if not key_value:
                continue

            key = key_value.group(1).strip().lower()
            value = key_value.group(2).strip()
            if not value:
                continue

            if key == "device":
                current.device = value
            elif key == "media name":
                current.name = value
                current.inserted = True
                current.state = "inserted"
            elif key == "media":
                current.inserted = self._value_looks_present(value)
                current.state = "inserted" if current.inserted else "empty"
            elif key == "type":
                current.drive_type = value
                if "no media" in value.lower() or value.lower() == "no media":
                    current.inserted = False
                    current.state = "empty"
                else:
                    current.inserted = self._contains_optical_keyword(value)
                    if current.state in {"unknown", ""}:
                        current.state = "inserted" if current.inserted else "empty"
            elif key == "status":
                current.state = value
                current.inserted = self._state_from_text(value)
            elif key == "media type":
                if current.state == "unknown":
                    current.state = "inserted" if self._state_from_text(value) else "unknown"
                current.drive_type = value
                current.inserted = self._contains_optical_keyword(value) or self._contains_optical_keyword(current.drive_type)

            if current.state.lower() in {"empty", "no media"}:
                current.inserted = False

        if current:
            entries.append(current)

        return [d for d in entries if d.device]

    def _from_diskutil_fallback(self) -> List[DriveInfo]:
        drives: List[DriveInfo] = []
        listing = run_cmd(["diskutil", "list"], timeout=self.FAST_PROBE_TIMEOUT_SECONDS)
        if listing.return_code == 0:
            listing_text = (listing.stdout or "").lower()
            if not (
                "cd_partition_scheme" in listing_text
                or "optical" in listing_text
                or "dvd" in listing_text
                or "cd-rom" in listing_text
            ):
                return []

        for node in sorted(glob.glob("/dev/rdisk[0-9]*")):
            if not self._is_disk_root(node):
                continue

            path = Path(node)
            if not path.is_char_device():
                continue

            info = run_cmd(["diskutil", "info", node], timeout=self.FAST_PROBE_TIMEOUT_SECONDS)
            if info.return_code != 0:
                continue

            text = info.stdout
            text_lower = text.lower()
            media_type = self._extract_value(text, "Media Type")
            removable_media = self._extract_value(text, "Removable Media").lower() == "yes"
            read_only_media = self._extract_value(text, "Read-Only Media").lower() == "yes"

            if not (
                self._contains_optical_keyword(media_type)
                or self._contains_optical_keyword(text_lower)
                or (removable_media and read_only_media)
            ):
                continue

            media_name = self._extract_value(text, "Media Name") or self._extract_value(text, "Volume Name")
            if not media_name and "not present" in text_lower:
                media_name = ""

            state = self._state_from_diskutil(text_lower)
            drives.append(
                DriveInfo(
                    device=node,
                    name=media_name or path.name,
                    drive_type=media_type or "",
                    inserted=(state == "inserted"),
                    state=state,
                    source="diskutil",
                )
            )
        return drives

    def _from_mounted_video_ts(self) -> List[DriveInfo]:
        volumes = Path("/Volumes")
        if not volumes.exists():
            return []

        mount_devices = self._mounted_volume_devices()
        drives: List[DriveInfo] = []
        for volume in volumes.iterdir():
            if not volume.is_dir() or not (volume / "VIDEO_TS").is_dir():
                continue

            device = mount_devices.get(str(volume))
            if not device:
                info = run_cmd(["diskutil", "info", str(volume)], timeout=self.FAST_PROBE_TIMEOUT_SECONDS)
                if info.return_code == 0:
                    device = self._extract_value(info.stdout, "Device Node") or device
                    if device.startswith("/dev/disk"):
                        device = device.replace("/dev/disk", "/dev/rdisk", 1)

            if not device:
                logging.debug("mounted VIDEO_TS volume has no device node: %s", volume)
                continue

            drives.append(
                DriveInfo(
                    device=device,
                    name=volume.name,
                    drive_type="DVD volume",
                    inserted=True,
                    state="inserted",
                    source="mount",
                )
            )
        return drives

    @classmethod
    def _mounted_volume_devices(cls) -> dict[str, str]:
        result = run_cmd(["mount"], timeout=cls.FAST_PROBE_TIMEOUT_SECONDS)
        if result.return_code != 0:
            return {}

        devices: dict[str, str] = {}
        for line in result.stdout.splitlines():
            source, _, right = line.partition(" on ")
            if not right:
                continue
            mountpoint = right.split(" (", 1)[0].strip()
            if not mountpoint.startswith("/Volumes/"):
                continue
            source = source.strip()
            if source.startswith("/dev/disk"):
                source = source.replace("/dev/disk", "/dev/rdisk", 1)
            devices[mountpoint] = source
        return devices

    @staticmethod
    def _extract_value(text: str, key: str) -> str:
        marker = key.lower() + ":"
        for line in text.splitlines():
            lowered = line.lower().strip()
            if lowered.startswith(marker):
                return line.split(":", 1)[1].strip()
        return ""

    @staticmethod
    def _is_disk_root(node: str) -> bool:
        return bool(re.fullmatch(r"/dev/rdisk\d+$", node))

    @staticmethod
    def _state_from_text(value: str) -> bool:
        lower = (value or "").lower()
        if not lower:
            return False
        if "no media" in lower or lower in {"empty", "not present", "absent", "unavailable"}:
            return False
        if "inserted" in lower or "present" in lower or "ready" in lower:
            return True
        return True

    @staticmethod
    def _state_from_diskutil(text: str) -> str:
        if "no media" in text:
            return "empty"
        if "media name" in text:
            return "inserted"
        if "volume name" in text:
            return "inserted"
        return "unknown"

    @staticmethod
    def _guess_disc_label(device: str) -> str:
        result = run_cmd(["diskutil", "info", device], timeout=DriveScanner.FAST_PROBE_TIMEOUT_SECONDS)
        if result.return_code != 0:
            return ""
        return (
            DriveScanner._extract_value(result.stdout, "Media Name")
            or DriveScanner._extract_value(result.stdout, "Volume Name")
        ).strip()

    @staticmethod
    def _value_looks_present(value: str) -> bool:
        normalized = (value or "").lower()
        return "none" not in normalized and "no media" not in normalized and bool(normalized.strip())
