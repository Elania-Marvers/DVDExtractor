from __future__ import annotations

import re

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseRetryPlanner(ABC):
    """Template de stratégie de reprise en mode ingénieur."""

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    @abstractmethod
    def derive_retry_attempts(
        self,
        command: dict,
        attempt_error: str,
        output: str,
        base_device: str,
    ) -> list[dict]:
        raise NotImplementedError


class DvdRetryPlanner(BaseRetryPlanner):
    """Planificateur d'essais alternatifs ciblés pour les erreurs DVD/FFmpeg."""

    def derive_retry_attempts(
        self,
        command: dict,
        attempt_error: str,
        output: str,
        base_device: str,
    ) -> list[dict]:
        lower = (attempt_error or "").lower()
        argv = command.get("argv") or []
        if not isinstance(argv, list) or len(argv) < 2:
            return []

        joined = " ".join(str(item) for item in argv).lower()
        if "/dev/" not in joined and ".vob" not in joined and "video_ts" not in joined and "dvd://" not in joined:
            return []

        source = command.get("input_source") or base_device
        source_path = str(source)
        base_name = Path(str(source_path)).name if source_path else "source"
        retries: list[dict] = []

        input_format = command.get("input_format") or "mpeg"
        if isinstance(input_format, str) and input_format.lower() not in {"mpeg", "dvd", "concat", "handbrake"}:
            input_format = "mpeg"

        def make_candidate(argv_candidate: list[str], *, label_suffix: str) -> dict | None:
            if not argv_candidate:
                return None
            candidate = list(argv_candidate)
            if candidate and candidate[-1] != output and output not in candidate:
                candidate.append(output)
            return {
                "label": label_suffix,
                "argv": candidate,
                "input_format": input_format,
                "input_source": source_path,
            }

        def add_attempt(label: str, argv_candidate: list[str]) -> None:
            attempt = make_candidate(argv_candidate, label_suffix=label)
            if attempt:
                retries.append(attempt)

        def drop_long_option(values: list[str], option: str) -> list[str]:
            target = option.lower().lstrip("-")
            out: list[str] = []
            skip_next = False

            for index, token in enumerate(values):
                if skip_next:
                    skip_next = False
                    continue

                normalized = str(token).lower()
                if normalized.startswith("--"):
                    key, has_value = (normalized[2:].split("=", 1) + [""])[:2]
                    if key == target or key == target.replace("-", ""):
                        continue
                    if has_value and key == target:
                        continue

                stripped = normalized.lstrip("-")
                if stripped == target:
                    skip_next = target in {"f", "i", "title", "dvd_device", "codec", "c", "c:v", "c:a", "map"}
                    continue

                out.append(str(token))

            return out

        if "unrecognized option" in lower or "option not found" in lower or "option 'title'" in lower:
            add_attempt(
                f"Retry ingénieur: nettoyage options strictes ({base_name})",
                [arg for arg in argv if arg not in {"-ignore_unknown", "-sn", "-dn", "ignore_err", "-copyts"}],
            )

        unknowns = re.findall(r"unrecognized option '([^']+)'", lower)
        for unknown in unknowns:
            if not unknown:
                continue
            cleaned = drop_long_option(argv, unknown)
            if cleaned != argv:
                add_attempt(f"Retry ingénieur: suppression '{unknown}'", cleaned)

            if unknown.lower() == "dvdvideo":
                cleaned_dvd: list[str] = []
                skip_next = False
                for index, value in enumerate(argv):
                    if skip_next:
                        skip_next = False
                        continue
                    if value == "-f" and index + 1 < len(argv) and str(argv[index + 1]).lower() == "dvdvideo":
                        skip_next = True
                        continue
                    if str(value).lower() == "dvdvideo":
                        continue
                    cleaned_dvd.append(str(value))
                add_attempt("Retry ingénieur: sans format dvdvideo", cleaned_dvd)

            if unknown.lower() == "title":
                add_attempt(
                    "Retry ingénieur: suppression -title",
                    [value for index, value in enumerate(argv) if index > 0 and str(argv[index - 1]).lower() != "-title"],
                )

        if "-dvd_device" in argv:
            for index, token in enumerate(argv):
                if token == "-dvd_device" and index + 1 < len(argv):
                    add_attempt(f"Retry ingénieur: sans -dvd_device ({base_name})", [*argv[:index], *argv[index + 2 :]])
                    break

        if "option 'b:a'" in lower and ("cannot be applied" in lower or "invalid argument" in lower):
            repaired: list[str] = []
            skip = False
            index = 0
            while index < len(argv):
                if skip:
                    skip = False
                    index += 1
                    continue

                token = argv[index]
                if token == "-b:a" and index + 1 < len(argv):
                    skip = True
                    index += 1
                    continue

                if token == "-c:a" and index + 1 < len(argv):
                    repaired.extend(["-c:a", "aac"])
                    skip = True
                    index += 1
                    continue

                repaired.append(token)
                index += 1

            add_attempt(
                f"Retry ingénieur: correction bitrate/codec audio ({base_name})",
                [
                    *repaired,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-ac",
                    "2",
                    "-movflags",
                    "+faststart",
                ],
            )

        if "parser not found for codec none" in lower or "codec none" in lower:
            video_only_base = self.manager._strip_audio_from_args(list(argv))
            if video_only_base != list(argv):
                add_attempt(
                    f"Retry ingénieur: suppression complète de l'audio ({base_name})",
                    self.manager._ensure_video_only_args(video_only_base, output),
                )

        if "invalid data found when processing input" in lower:
            add_attempt(
                f"Retry ingénieur: stratégie vidéo only ({base_name})",
                self.manager._ensure_video_only_args(list(argv), output),
            )

        if "verify-output-failed" in lower and self.manager.ffmpeg_supports_mpeg and source:
            mpeg_input_args = [] if "/dev/" in str(source_path).lower() else ["-f", "mpeg"]
            add_attempt(
                f"Retry ingénieur: relecture permissive ({base_name})",
                [
                    self.manager.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-nostdin",
                    "-fflags",
                    "+genpts",
                    "-err_detect",
                    "ignore_err",
                    "-analyzeduration",
                    "60M",
                    "-probesize",
                    "60M",
                    *mpeg_input_args,
                    "-i",
                    source_path,
                    "-map",
                    "0:v:0?",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-ac",
                    "2",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    "-sn",
                    "-dn",
                ],
            )

        if (
            "invalid data" in lower
            or "protocol not found" in lower
            or "unknown input format" in lower
            or "permission denied" in lower
        ) and source:
            if self.manager.ffmpeg_supports_mpeg:
                add_attempt(
                    f"Retry ingénieur: entrée mpeg permissive ({base_name})",
                    [
                        self.manager.ffmpeg,
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "warning",
                        "-nostdin",
                        "-fflags",
                        "+genpts",
                        "-err_detect",
                        "ignore_err",
                        "-analyzeduration",
                        "60M",
                        "-probesize",
                        "60M",
                        "-f",
                        "mpeg",
                        "-i",
                        source_path,
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "24",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-ac",
                        "2",
                        "-b:a",
                        "192k",
                        "-movflags",
                        "+faststart",
                        "-map",
                        "0:v:0?",
                        "-map",
                        "0:a:0?",
                    ],
                )

        if ("bad data" in lower or "corrupt" in lower or "permission denied" in lower) and source:
            mount = self.manager._mounted_volume(base_device)
            if mount:
                for title_id, parts in self.manager.plan_builder.scan_vob_titles(mount / "VIDEO_TS"):
                    retries.extend(
                        self.manager.plan_builder.build_vob_concat_commands(title_id, parts, output, mount)
                    )

        deduped: list[dict] = []
        normalized = set[tuple[str, ...]]()
        for retry in retries:
            key = tuple(str(item) for item in (retry.get("argv") or []))
            if key in normalized:
                continue
            normalized.add(key)
            deduped.append(retry)

        return deduped
