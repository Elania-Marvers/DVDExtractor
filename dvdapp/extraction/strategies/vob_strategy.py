from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from dvdapp.native_go_runner import build_extract_command as build_go_extract_command
from dvdapp.native_homebrew import build_concat_command, build_copy_command, build_extract_command as build_native_extract_command
from dvdapp.vob_manifest import scan_vob_titles_from_video_ts
from dvdapp.common import run_cmd

from .base import ExtractionPlanStrategy

LOGGER = logging.getLogger(__name__)
_VOB_FILE_RE = re.compile(r"^VTS_(\d{1,2})_(\d{1,2})\.VOB$", re.IGNORECASE)
_FALLBACK_MOVIE_TIMEOUT_SECONDS = 60 * 120
_FALLBACK_MOVIE_MIN_DURATION_SECONDS = 60 * 20


class VobPlanStrategy(ExtractionPlanStrategy):
    """Planification d'extraction à partir de titres montés dans VIDEO_TS."""

    name = "dvd-vob"
    priority = 30

    def __init__(self, profile) -> None:
        super().__init__(profile)

    def supports(self) -> bool:
        return bool(self.profile.ffmpeg)

    def build(self, mount_point: Path, output: str, engineer_mode: bool = False) -> list[dict]:
        if not mount_point:
            return []

        video_ts = mount_point / "VIDEO_TS"
        if not video_ts.is_dir():
            return []

        titles = self.scan_vob_titles(video_ts)
        commands: list[dict] = []

        for title_id, parts in titles:
            commands.extend(self._build_title_commands(title_id, parts, output, mount_point, engineer_mode=engineer_mode))

        for command in commands:
            command.setdefault("timeout", self._movie_timeout())
            command.setdefault("min_duration_seconds", self._movie_min_duration())

        return commands

    def scan_vob_titles(self, video_ts: Path) -> list[tuple[int, list[Path]]]:
        titles = scan_vob_titles_from_video_ts(video_ts)
        if titles:
            return self._filter_readable_titles(titles)

        candidates: dict[int, list[Path]] = {}
        for file in sorted(video_ts.glob("VTS_*_*.VOB")):
            match = _VOB_FILE_RE.match(file.name)
            if not match:
                continue
            title_id = int(match.group(1))
            part_id = int(match.group(2))
            if part_id == 0:
                continue
            candidates.setdefault(title_id, []).append(file)

        fallback = [(title_id, sorted(items, key=lambda p: p.name)) for title_id, items in candidates.items()]
        return self._filter_readable_titles(fallback)

    def build_vob_concat_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path | None = None,
    ) -> list[dict]:
        if len(parts) <= 1:
            return []
        if not self.profile.ffmpeg:
            return []

        part_list = sorted(parts, key=lambda item: item.name)
        if not part_list:
            return []

        suffix = f" ({mount_point.name})" if mount_point else ""
        commands: list[dict] = []

        transcode_list = self._build_list_file(part_list, prefix="dvdvob_concat_")
        if transcode_list:
            input_opts = ["-f", "concat", "-safe", "0", "-i", transcode_list]
            commands.extend(
                [
                    {
                        "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (transcode)",
                        "argv": self._ffmpeg_concat_profile(
                            input_opts,
                            output,
                            transcode=True,
                            include_audio=True,
                        ),
                        "artifacts": [transcode_list],
                        "input_format": "concat",
                        "input_source": transcode_list,
                    },
                    {
                        "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (transcode sans audio)",
                        "argv": self._ffmpeg_concat_profile(
                            input_opts,
                            output,
                            transcode=True,
                            include_audio=False,
                        ),
                        "artifacts": [transcode_list],
                        "input_format": "concat",
                        "input_source": transcode_list,
                    },
                    {
                        "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (transcode tolerant)",
                        "argv": self._ffmpeg_concat_profile(
                            input_opts,
                            output,
                            transcode=True,
                            include_audio=True,
                            tolerant=True,
                        ),
                        "artifacts": [transcode_list],
                        "input_format": "concat",
                        "input_source": transcode_list,
                    },
                ]
            )

        copy_list = self._build_list_file(part_list, prefix="dvdvob_copy_")
        if copy_list:
            input_opts = ["-f", "concat", "-safe", "0", "-i", copy_list]
            commands.extend(
                [
                    {
                        "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (copy)",
                        "argv": self._ffmpeg_concat_profile(
                            input_opts,
                            output,
                            transcode=False,
                            include_audio=True,
                        ),
                        "artifacts": [copy_list],
                        "input_format": "concat",
                        "input_source": copy_list,
                    },
                    {
                        "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (copy sans audio)",
                        "argv": self._ffmpeg_concat_profile(
                            input_opts,
                            output,
                            transcode=False,
                            include_audio=False,
                        ),
                        "artifacts": [copy_list],
                        "input_format": "concat",
                        "input_source": copy_list,
                    },
                    {
                        "label": f"Concat VOB titre VTS_{title_id:02d}{suffix} (copy tolerant)",
                        "argv": self._ffmpeg_concat_profile(
                            input_opts,
                            output,
                            transcode=False,
                            include_audio=True,
                            tolerant=True,
                        ),
                        "artifacts": [copy_list],
                        "input_format": "concat",
                        "input_source": copy_list,
                    },
                ]
            )

        return commands

    def build_go_runner_vob_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path | None,
    ) -> list[dict]:
        if not self.profile.go_runner_available or not parts:
            return []

        video_ts = mount_point / "VIDEO_TS" if mount_point else None
        if not video_ts or not video_ts.is_dir():
            return []

        cmd = build_go_extract_command(video_ts, output, title=title_id, ffmpeg=self.profile.ffmpeg)
        if not cmd:
            return []

        return [
            {
                "label": f"Go runner VOB titre VTS_{title_id:02d} ({mount_point.name})",
                "tool": "dvd_homebrew_runner",
                "argv": cmd,
                "input_format": "go-homebrew",
                "input_source": str(video_ts),
                "timeout": self._movie_timeout(),
                "min_duration_seconds": self._movie_min_duration(),
            }
        ]

    def build_native_extract_vob_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path | None,
    ) -> list[dict]:
        if not self.profile.homebrew_available or not self.profile.ffmpeg or not parts:
            return []

        video_ts = mount_point / "VIDEO_TS" if mount_point else None
        if not video_ts or not video_ts.is_dir():
            return []

        work_dir = Path(output).parent
        cmd = build_native_extract_command(
            video_ts,
            output,
            title=title_id,
            ffmpeg=self.profile.ffmpeg,
            work_dir=work_dir,
        )
        if not cmd:
            return []

        return [
            {
                "label": f"C++/ASM extracteur natif titre VTS_{title_id:02d} ({mount_point.name})",
                "tool": "dvd_homebrew",
                "argv": cmd,
                "input_format": "native-homebrew-mp4",
                "input_source": str(video_ts),
                "output_path": output,
                "timeout": self._movie_timeout(),
                "min_duration_seconds": self._movie_min_duration(),
            }
        ]

    def build_homebrew_vob_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path,
    ) -> list[dict]:
        if not self.profile.homebrew_available or not parts:
            return []

        if not self.profile.ffmpeg:
            return []

        if len(parts) == 1:
            source = parts[0]
            action = "copy"
        else:
            source = parts[0]
            action = "concat"

        tmp_name: str
        tmp_fd: int | None = None
        try:
            prefix = f"homebrew_{action}_{title_id:02d}_"
            tmp_fd, tmp_name = tempfile.mkstemp(prefix=prefix, suffix=".vob")
            Path(tmp_name).unlink(missing_ok=True)
        except Exception as exc:
            LOGGER.debug("failed to allocate temporary path for homebrew title %s: %s", title_id, exc)
            return []
        finally:
            if tmp_fd is not None:
                try:
                    os.close(tmp_fd)
                except Exception:
                    pass

        if len(parts) == 1:
            prepare_cmd = build_copy_command(tmp_name, source)
        else:
            prepare_cmd = build_concat_command(tmp_name, parts)

        if not prepare_cmd:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except Exception:
                pass
            return []

        source_label = f" ({mount_point.name})"
        common_input = [
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-analyzeduration",
            "60M",
            "-probesize",
            "60M",
            "-f",
            "mpeg",
            "-i",
            tmp_name,
        ]

        transcode = [
            self.profile.ffmpeg,
            *common_input,
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
            "-b:a",
            "192k",
            "-ac",
            "2",
            "-map",
            "0:v:0?",
            "-map",
            "0:a?",
            "-movflags",
            "+faststart",
            "-sn",
            "-dn",
            output,
        ]

        transcode_no_audio = [
            self.profile.ffmpeg,
            *common_input,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-map",
            "0:v:0?",
            "-sn",
            "-dn",
            output,
        ]

        commands = [
            {
                "label": f"Homebrew VOB titre VTS_{title_id:02d}{source_label} (pré-concat copy + transcode)",
                "tool": "pipeline",
                "pipeline": [
                    {
                        "tool": "dvd_homebrew",
                        "argv": prepare_cmd,
                        "artifacts": [tmp_name],
                        "label": f"homebrew {action} titre {title_id:02d}",
                    },
                    {
                        "tool": "ffmpeg",
                        "argv": transcode,
                        "artifacts": [],
                        "label": f"ffmpeg transcode titre {title_id:02d}",
                    },
                ],
                "artifacts": [tmp_name],
                "input_format": "homebrew",
                "input_source": str(source),
            },
            {
                "label": f"Homebrew VOB titre VTS_{title_id:02d}{source_label} (pré-concat copy + transcode sans audio)",
                "tool": "pipeline",
                "pipeline": [
                    {
                        "tool": "dvd_homebrew",
                        "argv": prepare_cmd,
                        "artifacts": [tmp_name],
                        "label": f"homebrew {action} titre {title_id:02d}",
                    },
                    {
                        "tool": "ffmpeg",
                        "argv": transcode_no_audio,
                        "artifacts": [],
                        "label": f"ffmpeg transcode sans audio titre {title_id:02d}",
                    },
                ],
                "artifacts": [tmp_name],
                "input_format": "homebrew",
                "input_source": str(source),
            },
        ]

        for command in commands:
            command.setdefault("timeout", self._movie_timeout())
            command.setdefault("min_duration_seconds", self._movie_min_duration())

        return commands

    def _build_title_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path,
        *,
        engineer_mode: bool = False,
    ) -> list[dict]:
        commands: list[dict] = []

        if self.profile.homebrew_available:
            commands.extend(self.build_native_extract_vob_commands(title_id, parts, output, mount_point))

        if self.profile.go_runner_available:
            commands.extend(self.build_go_runner_vob_commands(title_id, parts, output, mount_point))

        if not engineer_mode:
            return commands

        if self.profile.homebrew_available:
            commands.extend(self.build_homebrew_vob_commands(title_id, parts, output, mount_point))

        if len(parts) == 1:
            commands.extend(self._build_direct_vob_commands(title_id, parts[0], output, mount_point))
        else:
            commands.extend(self.build_vob_concat_commands(title_id, parts, output, mount_point))

        for command in commands:
            command.setdefault("timeout", self._movie_timeout())
            command.setdefault("min_duration_seconds", self._movie_min_duration())

        return commands

    def _movie_timeout(self) -> int:
        try:
            configured = int(self.profile.command_timeout)
        except Exception:
            configured = 0
        return max(configured, _FALLBACK_MOVIE_TIMEOUT_SECONDS)

    def _movie_min_duration(self) -> int:
        try:
            return int(getattr(self.profile.manager, "MIN_MOVIE_DURATION_SECONDS"))
        except Exception:
            return _FALLBACK_MOVIE_MIN_DURATION_SECONDS

    def _build_direct_vob_commands(
        self,
        title_id: int,
        source: Path,
        output: str,
        mount_point: Path,
    ) -> list[dict]:
        if not self.profile.ffmpeg:
            return []

        source_path = str(source)
        source_node = [
            "-f",
            "mpeg",
            "-i",
            source_path,
        ]

        base_input = [
            self.profile.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            *source_node,
        ]
        suffix = f" ({mount_point.name})"

        variants: list[tuple[str, list[str]]] = [
            (
                "direct transcode",
                [
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
                    "-b:a",
                    "192k",
                    "-map",
                    "0:v:0?",
                    "-map",
                    "0:a?",
                    "-sn",
                    "-dn",
                ],
            ),
            (
                "direct transcode sans audio",
                [
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-map",
                    "0:v:0?",
                    "-sn",
                    "-dn",
                ],
            ),
            (
                "direct copy",
                [
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    "-map",
                    "0:v:0?",
                    "-map",
                    "0:a?",
                    "-sn",
                    "-dn",
                ],
            ),
            (
                "direct copy sans audio",
                [
                    "-an",
                    "-c:v",
                    "copy",
                    "-map",
                    "0:v:0?",
                    "-sn",
                    "-dn",
                ],
            ),
        ]

        commands = []
        for suffix_name, args in variants:
            commands.append(
                {
                    "label": f"VOB titre VTS_{title_id:02d}{suffix} {suffix_name}",
                    "argv": [*base_input, *args, output],
                    "artifacts": [],
                    "input_format": "mpeg",
                    "input_source": source_path,
                }
            )

        commands.append(
            {
                "label": f"VOB titre VTS_{title_id:02d}{suffix} direct transcode tolerant",
                "argv": [
                    self.profile.ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "warning",
                    "-analyzeduration",
                    "60M",
                    "-probesize",
                    "60M",
                    *source_node,
                    "-fflags",
                    "+genpts",
                    "-err_detect",
                    "ignore_err",
                    "-ignore_unknown",
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
                    "-map",
                    "0:v:0?",
                    "-map",
                    "0:a?",
                    "-sn",
                    "-dn",
                    output,
                ],
                "artifacts": [],
                "input_format": "mpeg",
                "input_source": source_path,
            }
        )

        return commands

    def _build_list_file(self, parts: list[Path], prefix: str) -> str | None:
        if not parts:
            return None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=prefix,
                suffix=".txt",
                delete=False,
            ) as handle:
                for part in parts:
                    handle.write(f"file '{part.as_posix()}'\n")
            return handle.name
        except Exception as exc:
            LOGGER.warning("unable to build concat list: %s", exc)
            return None

    def _ffmpeg_concat_profile(
        self,
        input_opts: list[str],
        output: str,
        *,
        transcode: bool,
        include_audio: bool,
        tolerant: bool = False,
    ) -> list[str]:
        if not self.profile.ffmpeg:
            return []

        argv = [
            self.profile.ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "warning",
            *input_opts,
            "-sn",
            "-dn",
        ]

        if tolerant:
            argv.extend(["-analyzeduration", "60M", "-probesize", "60M", "-fflags", "+genpts", "-err_detect", "ignore_err", "-ignore_unknown"])

        if transcode:
            argv.extend([
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
            ])
            if include_audio:
                argv.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])
            else:
                argv.extend(["-an"])
        else:
            argv.extend(["-c:v", "copy"])
            if include_audio:
                argv.extend(["-c:a", "copy"])
            else:
                argv.extend(["-an"])

        if include_audio:
            argv.extend(["-map", "0:v:0?", "-map", "0:a?"])
        else:
            argv.extend(["-map", "0:v:0?"])

        if transcode:
            argv.extend(["-movflags", "+faststart"])

        argv.append(output)
        return argv

    def _filter_readable_titles(self, titles: list[tuple[int, list[Path]]]) -> list[tuple[int, list[Path]]]:
        if not titles:
            return []

        candidates: list[tuple[int, int, int, list[Path]]] = []
        for title_id, parts in titles:
            if not parts:
                continue

            sorted_parts = sorted(parts, key=lambda item: item.name)
            readable = [part for part in sorted_parts if self._probe_source(part)]
            if not readable:
                LOGGER.debug("ignore title %s: no readable vob part", title_id)
                continue

            with_video = [part for part in readable if self.vob_has_video(part)]
            if not with_video:
                LOGGER.debug("ignore title %s: no video stream detected", title_id)
                continue

            total_size = self.safe_file_size(sum(part.stat().st_size for part in sorted_parts) if sorted_parts else 0)
            candidates.append((title_id, total_size, len(with_video), with_video))

        candidates.sort(key=lambda item: (-item[1], -item[2], item[0]))
        return [(item[0], item[3]) for item in candidates[:6]]

    def _probe_source(self, source: Path) -> bool:
        try:
            return bool(self.profile.manager._probe_source_access(str(source))[0])
        except Exception:
            return False

    def vob_has_video(self, file: Path) -> bool:
        if not file.exists() or not file.is_file():
            return False
        if not self.profile.manager.ffprobe:
            return False

        result = run_cmd([
            self.profile.manager.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=index",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file),
        ], timeout=8)

        if result.return_code != 0:
            return False
        return bool((result.stdout or "").strip())

    def safe_file_size(self, value: int | Path) -> int:
        if isinstance(value, Path):
            try:
                return value.stat().st_size
            except OSError:
                return 0
        try:
            return int(value)
        except Exception:
            return 0

    # Backward-compatible entry points expected by plan_builder wrappers
    def build_go_runner(self, title_id: int, parts: list[Path], output: str, mount_point: Path | None) -> list[dict]:
        return self.build_go_runner_vob_commands(title_id, parts, output, mount_point)

    def build_homebrew(self, title_id: int, parts: list[Path], output: str, mount_point: Path) -> list[dict]:
        return self.build_homebrew_vob_commands(title_id, parts, output, mount_point)


VobTitlePlanStrategy = VobPlanStrategy
