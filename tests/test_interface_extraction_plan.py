from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dvdapp.extraction.strategies.vob_strategy import VobPlanStrategy
from dvdapp.native_go_runner import GO_TOOL, build_extract_command
from dvdapp.native_homebrew import HOMEBREW_TOOL


class _FakeManager:
    MIN_MOVIE_DURATION_SECONDS = 20 * 60


class _FakeProfile:
    ffmpeg = "/usr/local/bin/ffmpeg"
    command_timeout = 60 * 120
    homebrew_available = True
    go_runner_available = True
    manager = _FakeManager()


@unittest.skipIf(not HOMEBREW_TOOL.exists() or not GO_TOOL.exists(), "native helpers are not built")
class InterfaceExtractionPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.mount = self.root / "JOURNEY_TO_TCOTE"
        self.video_ts = self.mount / "VIDEO_TS"
        self.video_ts.mkdir(parents=True)
        self.part = self.video_ts / "VTS_04_1.VOB"
        self.part.write_bytes(b"\x00\x00\x01\xba" + b"\x00" * 2044)
        self.output = str(self.root / "movie.mp4")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_go_runner_delegates_to_native_extract_with_long_timeout(self) -> None:
        command = build_extract_command(self.video_ts, self.output, title=4, ffmpeg=_FakeProfile.ffmpeg)
        self.assertIsNotNone(command)
        assert command is not None

        self.assertIn("extract", command)
        self.assertIn("--timeout", command)
        self.assertEqual(command[command.index("--timeout") + 1], "7200")
        self.assertIn("--work-dir", command)
        self.assertEqual(command[command.index("--work-dir") + 1], str(Path(self.output).parent))

    def test_normal_vob_plan_avoids_raw_vob_fallbacks(self) -> None:
        strategy = VobPlanStrategy(_FakeProfile())
        commands = strategy._build_title_commands(
            4,
            [self.part],
            self.output,
            self.mount,
            engineer_mode=False,
        )

        labels = [str(command.get("label", "")) for command in commands]
        self.assertTrue(any("extracteur natif" in label for label in labels))
        self.assertTrue(any("Go runner" in label for label in labels))
        self.assertFalse(any(label.startswith("Homebrew VOB") for label in labels))
        self.assertFalse(any(label.startswith("VOB titre") for label in labels))
        self.assertFalse(any(label.startswith("Concat VOB") for label in labels))

        self.assertTrue(all(command.get("timeout", 0) >= 7200 for command in commands))
        self.assertTrue(all(command.get("min_duration_seconds", 0) >= 1200 for command in commands))

    def test_engineer_vob_plan_keeps_raw_fallbacks(self) -> None:
        strategy = VobPlanStrategy(_FakeProfile())
        commands = strategy._build_title_commands(
            4,
            [self.part],
            self.output,
            self.mount,
            engineer_mode=True,
        )

        labels = [str(command.get("label", "")) for command in commands]
        self.assertTrue(any(label.startswith("Homebrew VOB") for label in labels))
        self.assertTrue(any(label.startswith("VOB titre") for label in labels))


if __name__ == "__main__":
    unittest.main()
