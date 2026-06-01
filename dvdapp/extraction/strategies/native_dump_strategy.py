from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from dvdapp.native_dvd_reader import NativeTitleProbe, dump_command_for_title, list_title_candidates

from .base import ExtractionPlanStrategy

LOGGER = logging.getLogger(__name__)


class NativeDumpPlanStrategy(ExtractionPlanStrategy):
    """Construit une chaîne dump natif libdvdread + transcode ffmpeg."""

    name = "native-dump"
    priority = 40

    def supports(self) -> bool:
        return bool(self.profile.ffmpeg) and bool(self.profile.native_dump_available)

    def build(self, source_candidates: list[str], output: str) -> list[dict]:
        if not self.profile.ffmpeg:
            return []

        commands: list[dict] = []

        for source in source_candidates:
            if not source:
                continue
            title_candidates: list[NativeTitleProbe] = []
            try:
                title_candidates = list_title_candidates(source)[:3]
            except Exception as exc:
                LOGGER.debug("native title probe failed for %s: %s", source, exc)

            if not title_candidates:
                title_candidates = [NativeTitleProbe(title=1, blocks=0, size_bytes=0)]

            for item in title_candidates[:2]:
                commands.extend(self._build_title_pipeline(source, item, output))

        deduped: list[dict] = []
        signatures: set[tuple[str, ...]] = set()
        for command in commands:
            signature = tuple(str(part) for part in (command.get("pipeline") or []))
            if signature in signatures:
                continue
            signatures.add(signature)
            deduped.append(command)

        return deduped

    def _build_title_pipeline(self, source: str, item: NativeTitleProbe, output: str) -> list[dict]:
        if not self.profile.ffmpeg:
            return []

        try:
            with tempfile.NamedTemporaryFile(
                prefix=f"dvd_native_{Path(source).name}_t{int(item.title):02d}_",
                suffix=".vob",
                delete=False,
            ) as dump_file:
                dump_path = Path(dump_file.name)
        except Exception as exc:
            LOGGER.debug("unable to create native dump temp for %s title %s: %s", source, item.title, exc)
            return []

        dump_cmd = dump_command_for_title(source, int(item.title), str(dump_path))
        ffmpeg_cmd = self._ffmpeg_pipeline(dump_path, output)

        base_label = (
            f"pipeline libdvdread (source={Path(source).name}, title={int(item.title)})"
        )

        commands = [
            {
                "label": base_label,
                "tool": "pipeline",
                "pipeline": [
                    {
                        "tool": "dvd_reader_dump",
                        "argv": dump_cmd,
                        "artifacts": [str(dump_path)],
                        "label": f"dvd_reader_dump titre {int(item.title)}",
                    },
                    {
                        "tool": "ffmpeg",
                        "argv": ffmpeg_cmd,
                        "label": f"ffmpeg transcode titre {int(item.title)}",
                        "artifacts": [],
                    },
                ],
                "artifacts": [str(dump_path)],
                "input_format": "native",
                "input_source": source,
            }
        ]

        if int(item.title) and item.blocks:
            commands.append(
                {
                    "label": base_label.replace("transcode", "sans audio"),
                    "tool": "pipeline",
                    "pipeline": [
                        {
                            "tool": "dvd_reader_dump",
                            "argv": dump_cmd,
                            "artifacts": [str(dump_path)],
                            "label": f"dvd_reader_dump titre {int(item.title)}",
                        },
                        {
                            "tool": "ffmpeg",
                            "argv": self._ffmpeg_pipeline(dump_path, output, without_audio=True),
                            "label": f"ffmpeg sans audio titre {int(item.title)}",
                            "artifacts": [],
                        },
                    ],
                    "artifacts": [str(dump_path)],
                    "input_format": "native",
                    "input_source": source,
                }
            )

        return commands

    def _ffmpeg_pipeline(self, dump_path: Path, output: str, without_audio: bool = False) -> list[str]:
        if not self.profile.ffmpeg:
            return []

        argv = [
            self.profile.ffmpeg,
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
            str(dump_path),
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
        ]

        if not without_audio:
            argv.extend(["-c:a", "aac", "-b:a", "192k", "-map", "0:a?"])

        argv.extend(
            [
                "-sn",
                "-dn",
                output,
            ]
        )

        return argv


# Compatibility alias used by previous imports.
DvdNativePlanStrategy = NativeDumpPlanStrategy
