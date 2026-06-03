from __future__ import annotations

import logging

from .base import ExtractionPlanStrategy, FfmpegProfileSpec

LOGGER = logging.getLogger(__name__)
_FALLBACK_MIN_MOVIE_DURATION_SECONDS = 60 * 20


class FfmpegSourcePlanStrategy(ExtractionPlanStrategy):
    """Construit des stratégies ffmpeg directes (source disque / fichier)."""

    name = "ffmpeg-source"
    priority = 20

    def supports(self) -> bool:
        return bool(self.profile.ffmpeg)

    def build(
        self,
        source: str,
        label: str,
        output: str,
        input_args: list[str],
        engineer_mode: bool,
        command_timeout: int | None = None,
    ) -> list[dict]:
        commands: list[dict] = []
        self.build_ffmpeg_source_commands(
            commands,
            source,
            label,
            output,
            input_args,
            engineer_mode,
            command_timeout,
        )
        return commands

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
        if not source:
            return

        if not self._probe_source(source):
            return

        if command_timeout is None:
            command_timeout = self.profile.command_timeout
        min_duration = self._movie_min_duration()

        if not self.profile.ffmpeg:
            return

        base_input = FfmpegProfileSpec.base_argv(self.profile.ffmpeg)

        tolerant = ["-fflags", "+genpts", "-err_detect", "ignore_err", "-ignore_unknown"]
        common_map = ["-map", "0:v:0?", "-map", "0:a?"]
        mux_out = ["-sn", "-dn", "-movflags", "+faststart"]

        profiles: list[tuple[str, list[str]]] = [
            (
                "transcode",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
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
                    "-max_muxing_queue_size",
                    "4096",
                    *common_map,
                    *mux_out,
                ],
            ),
            (
                "transcode sans audio",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "22",
                    "-pix_fmt",
                    "yuv420p",
                    "-max_muxing_queue_size",
                    "4096",
                    "-map",
                    "0:v:0?",
                    *mux_out,
                ],
            ),
            (
                "copy video + AAC",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ac",
                    "2",
                    *common_map,
                    *mux_out,
                ],
            ),
            (
                "copy video sans audio",
                [
                    *base_input,
                    *input_args,
                    "-i",
                    source,
                    "-an",
                    "-c:v",
                    "copy",
                    "-map",
                    "0:v:0?",
                    *mux_out,
                ],
            ),
        ]

        for name, argv in profiles:
            commands.append(
                {
                    "label": f"{label} — {name}",
                    "argv": argv + [output],
                    "input_format": "mpeg",
                    "input_source": source,
                    "timeout": command_timeout,
                    "min_duration_seconds": min_duration,
                }
            )

        if engineer_mode:
            commands.append(
                {
                    "label": f"{label} — tolerant",
                    "argv": [
                        *base_input,
                        *input_args,
                        "-i",
                        source,
                        *tolerant,
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
                        "-max_muxing_queue_size",
                        "4096",
                        *common_map,
                        *mux_out,
                        output,
                    ],
                    "input_format": "mpeg",
                    "input_source": source,
                    "timeout": command_timeout,
                    "min_duration_seconds": min_duration,
                }
            )

    def _probe_source(self, source: str) -> bool:
        try:
            return bool(self.profile.manager._probe_source_access(str(source))[0])
        except Exception as exc:  # pragma: no cover - isolation from I/O
            LOGGER.debug("ffmpeg source probe failed for %s: %s", source, exc)
            return False

    def _movie_min_duration(self) -> int:
        try:
            return int(getattr(self.profile.manager, "MIN_MOVIE_DURATION_SECONDS"))
        except Exception:
            return _FALLBACK_MIN_MOVIE_DURATION_SECONDS


# Compatibility class name used in older modules/tests.
FfmpegSourceStrategy = FfmpegSourcePlanStrategy
