from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .strategies import (
    FfmpegSourcePlanStrategy,
    NativeDumpPlanStrategy,
    VobPlanStrategy,
    HandBrakePlanStrategy,
)

logger = logging.getLogger(__name__)


class BuildProfile:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    @property
    def ffmpeg(self) -> str | None:
        return self.manager.ffmpeg

    @property
    def command_timeout(self) -> int:
        return self.manager.DEFAULT_CMD_TIMEOUT_SECONDS

    @property
    def ffmpeg_supports_mpeg(self) -> bool:
        return self.manager.ffmpeg_supports_mpeg

    @property
    def native_dump_available(self) -> bool:
        return self.manager.native_dump_available

    @property
    def go_runner_available(self) -> bool:
        return self.manager.go_runner_available

    @property
    def homebrew_available(self) -> bool:
        return self.manager.homebrew_available

    @property
    def handbrake(self) -> str | None:
        return self.manager.handbrake


class BaseExtractionPlanBuilder(ABC):
    """Template de base pour un générateur de plan d'extraction."""

    name = "base"
    priority = 100

    def __init__(self, profile: BuildProfile) -> None:
        self.profile = profile

    @abstractmethod
    def build(self, device: str, output: Path, mode: str = "normal") -> list[dict]:
        raise NotImplementedError


class DvdExtractionPlanBuilder(BaseExtractionPlanBuilder):
    """Génère les plans d'extraction DVD (ffmpeg + backends maison)."""

    name = "dvd"
    priority = 10

    def __init__(self, profile: BuildProfile) -> None:
        super().__init__(profile)
        self.ffmpeg_source = FfmpegSourcePlanStrategy(profile)
        self.vob_strategy = VobPlanStrategy(profile)
        self.native_dump = NativeDumpPlanStrategy(profile)
        self.handbrake = HandBrakePlanStrategy(profile)

    def build(self, device: str, output: Path, mode: str = "normal") -> list[dict]:
        return self._assemble_plan(device, str(output), mode)

    def _assemble_plan(self, device: str, output: str, mode: str = "normal") -> list[dict]:
        base_device = device if device.startswith("/dev/") else f"/dev/{device}"
        alt_device = self.profile.manager._alt_device(base_device)
        mount_points = self.profile.manager._mounted_volume_candidates(base_device)

        source_candidates = [base_device]
        if alt_device:
            source_candidates.append(alt_device)
        source_candidates = list(dict.fromkeys(source_candidates))

        engineer_mode = mode in {"engineer", "advanced"}
        commands: list[dict] = []

        for mount_point in mount_points:
            try:
                commands.extend(self.vob_strategy.build(mount_point, output, engineer_mode=engineer_mode))
            except Exception as exc:
                logger.exception("vob strategy failed for mount point %s: %s", mount_point, exc)

            root_vob = mount_point / "VIDEO_TS" / "VIDEO_TS.VOB"
            if root_vob.is_file() and self._probe_source(root_vob):
                try:
                    self.ffmpeg_source.build_ffmpeg_source_commands(
                        commands,
                        str(root_vob),
                        f"VIDEO_TS.VOB ({mount_point.name})",
                        output,
                        ["-f", "mpeg"],
                        engineer_mode,
                    )
                except Exception as exc:
                    logger.exception("ffmpeg source plan failed for %s: %s", root_vob, exc)

        if engineer_mode and self.profile.native_dump_available:
            try:
                commands.extend(self.native_dump.build(source_candidates, output))
            except Exception as exc:
                logger.exception("native dump strategy failed: %s", exc)

        for source in source_candidates:
            try:
                self.ffmpeg_source.build_ffmpeg_source_commands(
                    commands,
                    source,
                    f"Périphérique ({Path(source).name})",
                    output,
                    [],
                    engineer_mode,
                )
            except Exception as exc:
                logger.exception("ffmpeg source plan failed for %s: %s", source, exc)

        if engineer_mode and self.profile.handbrake:
            try:
                commands.extend(self.handbrake.build(base_device, mount_points, output))
            except Exception as exc:
                logger.exception("handbrake plan failed: %s", exc)

        deduped = self._dedupe(commands)
        if not deduped:
            raise RuntimeError("no extraction commands prepared")
        return deduped

    def _dedupe(self, commands: list[dict]) -> list[dict]:
        unique: list[dict] = []
        seen: set[tuple[str, ...]] = set()

        for cmd in commands:
            if self.profile.manager.debug_enabled:
                logger.debug("rip strategy: %s", cmd.get("label"))

            argv = cmd.get("argv")
            if isinstance(argv, list):
                key = tuple(str(item) for item in argv)
            else:
                pipeline = cmd.get("pipeline")
                if not pipeline:
                    continue
                key = tuple(f"pipeline:{str(step)}" for step in pipeline)

            if key in seen:
                continue
            seen.add(key)
            unique.append(cmd)

        return unique

    def _probe_source(self, source: str | Path) -> bool:
        try:
            return bool(self.profile.manager._probe_source_access(str(source))[0])
        except Exception:
            return False

    def scan_vob_titles(self, video_ts: Path) -> list[tuple[int, list[Path]]]:
        return self.vob_strategy.scan_vob_titles(video_ts)

    def build_vob_title_commands(self, mount_point: Path, output: str) -> list[dict]:
        return self.vob_strategy.build(mount_point, output)

    def build_vob_concat_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path | None = None,
    ) -> list[dict]:
        return self.vob_strategy.build_vob_concat_commands(title_id, parts, output, mount_point)

    def build_ffmpeg_source_commands(
        self,
        commands: list[dict],
        source: str,
        label: str,
        output: str,
        input_args: list[str],
        engineer_mode: bool,
        command_timeout: int | None = None,
    ) -> None:
        self.ffmpeg_source.build_ffmpeg_source_commands(
            commands,
            source,
            label,
            output,
            input_args,
            engineer_mode,
            command_timeout,
        )

    def build_native_dump_pipelines(self, source_candidates: list[str], output: str) -> list[dict]:
        return self.native_dump.build(source_candidates, output)

    def build_go_runner_vob_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path | None,
    ) -> list[dict]:
        return self.vob_strategy.build_go_runner_vob_commands(title_id, parts, output, mount_point)

    def build_homebrew_vob_commands(
        self,
        title_id: int,
        parts: list[Path],
        output: str,
        mount_point: Path,
    ) -> list[dict]:
        return self.vob_strategy.build_homebrew_vob_commands(title_id, parts, output, mount_point)

    def vob_has_video(self, file: Path) -> bool:
        return self.vob_strategy.vob_has_video(file)

    def safe_file_size(self, value: int | Path) -> int:
        return self.vob_strategy.safe_file_size(value)


# Compatibility alias expected by older callers.
FFmpegOnlyExtractionPlanBuilder = DvdExtractionPlanBuilder
