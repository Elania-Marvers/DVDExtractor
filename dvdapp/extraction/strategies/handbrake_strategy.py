from __future__ import annotations

from pathlib import Path

from .base import ExtractionPlanStrategy


class HandBrakePlanStrategy(ExtractionPlanStrategy):
    """Stratégie de dernier recours: HandBrakeCLI."""

    name = "handbrake"
    priority = 80

    def supports(self) -> bool:
        return bool(self.profile.handbrake)

    def build(self, base_device: str, mount_points: list[Path], output: str) -> list[dict]:
        if not self.profile.handbrake:
            return []

        if not base_device:
            return []

        commands: list[dict] = []

        commands.append(
            {
                "label": "HandBrakeCLI périphérique (transcode)",
                "argv": [
                    self.profile.handbrake,
                    "-i",
                    base_device,
                    "-o",
                    output,
                    "-e",
                    "x264",
                    "--audio-lang-list",
                    "fra,eng",
                    "--all-audio",
                    "--all-subtitles",
                    "--encoder-preset",
                    "medium",
                    "--aencoder",
                    "ca_aac",
                    "--quality",
                    "22",
                    "--optimize",
                    "--x264-preset",
                    "fast",
                    "-v",
                    "1",
                ],
                "input_format": "handbrake",
                "input_source": base_device,
                "timeout": self.profile.command_timeout,
            }
        )

        for mount_point in mount_points:
            source = str(mount_point / "VIDEO_TS")
            commands.append(
                {
                    "label": f"HandBrakeCLI VIDEO_TS ({mount_point.name})",
                    "argv": [
                        self.profile.handbrake,
                        "-i",
                        source,
                        "-o",
                        output,
                        "--preset",
                        "Fast 1080p30",
                    ],
                    "input_format": "handbrake",
                    "input_source": source,
                    "timeout": self.profile.command_timeout,
                }
            )

        return commands


# Compatibility alias used by older modules/tests.
DvdHandBrakePlanStrategy = HandBrakePlanStrategy
