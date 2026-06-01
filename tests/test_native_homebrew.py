from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOMEBREW = ROOT / "native" / "build" / "dvd_homebrew"


def make_vob_payload(seed: int) -> bytes:
    block = bytearray(2048)
    block[0:4] = b"\x00\x00\x01\xba"
    block[32:36] = b"\x00\x00\x01\xbb"
    block[96:100] = b"\x00\x00\x01\xb3"
    block[160:164] = b"\x00\x00\x01\xbf"
    for index in range(200, len(block)):
        block[index] = (index + seed) % 251
    return bytes(block) * 2


class NativeHomebrewSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        if not HOMEBREW.exists():
            self.skipTest("native/build/dvd_homebrew is not built")

        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.video_ts = self.root / "VIDEO_TS"
        self.video_ts.mkdir()
        (self.video_ts / "VTS_01_1.VOB").write_bytes(make_vob_payload(1))
        (self.video_ts / "VTS_01_2.VOB").write_bytes(make_vob_payload(2))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_homebrew(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(HOMEBREW), *args],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )

    def test_scan_returns_sorted_title_manifest(self) -> None:
        result = self.run_homebrew("scan", str(self.video_ts))
        self.assertEqual(result.returncode, 0, result.stderr)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["titles"][0]["id"], 1)
        self.assertEqual(len(payload["titles"][0]["parts"]), 2)
        self.assertGreater(payload["titles"][0]["size"], 0)

    def test_preflight_uses_c_probe_and_reports_mpeg_signatures(self) -> None:
        result = self.run_homebrew("preflight", str(self.video_ts), "--title", "1")
        self.assertEqual(result.returncode, 0, result.stderr)

        payload = json.loads(result.stdout)
        self.assertEqual(payload["title"], 1)
        self.assertEqual(len(payload["parts"]), 2)
        self.assertTrue(all(part["likely_program_stream"] for part in payload["parts"]))
        self.assertTrue(all(part["pack_sync"] > 0 for part in payload["parts"]))

    def test_concat_writes_contiguous_vob_stream(self) -> None:
        output = self.root / "joined.vob"
        parts = [str(self.video_ts / "VTS_01_1.VOB"), str(self.video_ts / "VTS_01_2.VOB")]
        result = self.run_homebrew("concat", "--output", str(output), *parts)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertTrue(output.exists())
        expected_size = sum(Path(part).stat().st_size for part in parts)
        self.assertEqual(output.stat().st_size, expected_size)
        self.assertEqual(output.read_bytes()[:4], b"\x00\x00\x01\xba")


if __name__ == "__main__":
    unittest.main()
