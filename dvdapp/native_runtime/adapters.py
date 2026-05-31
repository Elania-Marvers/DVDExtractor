from __future__ import annotations

from pathlib import Path

from .base import NativeExecutableAdapter, NativeToolFactory


class HomebrewAdapter(NativeExecutableAdapter):
    """Adapter for dvd_homebrew_tool."""

    def __init__(self) -> None:
        tool_root = Path(__file__).resolve().parents[1]
        super().__init__(tool_root / "native" / "build" / "dvd_homebrew", tool_name="dvd_homebrew")

    def build_scan(self, video_ts: str | Path) -> list[str] | None:
        return self.command("scan", str(video_ts))

    def build_copy(self, source: str | Path, output: str | Path) -> list[str] | None:
        return self.command("copy", "--source", str(source), "--output", str(output))

    def build_concat(self, output: str | Path, parts: list[str] | list[Path]) -> list[str] | None:
        return self.command("concat", "--output", str(output), *[str(item) for item in parts])


class GoHomebrewAdapter(NativeExecutableAdapter):
    """Adapter for go_homebrew_runner binary."""

    def __init__(self) -> None:
        tool_root = Path(__file__).resolve().parents[1]
        super().__init__(tool_root / "bin" / "dvd_homebrew_runner", tool_name="dvd_homebrew_runner")

    def build_extract(self, video_ts: str | Path, output: str, title: int | None = None, ffmpeg: str | None = None) -> list[str] | None:
        if not self.available:
            return None

        command = [str(self.executable), "extract", "--video-ts", str(video_ts), "--output", str(output), "--timeout", "1200"]
        if title:
            command.extend(["--title", str(int(title))])
        if ffmpeg:
            command.extend(["--ffmpeg", str(ffmpeg)])
        return command


class NativeReaderAdapter(NativeExecutableAdapter):
    """Adapter for dvd_reader_dump executable."""

    def __init__(self) -> None:
        tool_root = Path(__file__).resolve().parents[1]
        super().__init__(tool_root / "native" / "build" / "dvd_reader_dump", tool_name="dvd_reader_dump")

    def build_list_titles(self, source: str | Path) -> list[str] | None:
        return self.command("--list-titles", str(source))

    def build_dump(self, source: str, title: int, output: str | Path) -> list[str] | None:
        return self.command(str(source), "--title", str(title), "--output", str(output))


class SignalProbeAdapter(NativeExecutableAdapter):
    """Adapter for dvd_signal_probe executable."""

    def __init__(self) -> None:
        tool_root = Path(__file__).resolve().parents[1]
        super().__init__(tool_root / "native" / "build" / "dvd_signal_probe", tool_name="dvd_signal_probe")

    def build_analyze(self, source: str | Path, sample_bytes: int | None = None) -> list[str] | None:
        if sample_bytes is not None:
            return self.command(str(source), str(int(sample_bytes)))
        return self.command(str(source))


class VobManifestAdapter(NativeExecutableAdapter):
    """Adapter for dvd_vob_manifest executable."""

    def __init__(self) -> None:
        tool_root = Path(__file__).resolve().parents[1]
        super().__init__(tool_root / "native" / "build" / "dvd_vob_manifest", tool_name="dvd_vob_manifest")

    def build_scan(self, video_ts: str | Path) -> list[str] | None:
        return self.command(str(video_ts))


class HomebrewToolFactory(NativeToolFactory):
    def __init__(self) -> None:
        super().__init__(HomebrewAdapter())


class GoHomebrewToolFactory(NativeToolFactory):
    def __init__(self) -> None:
        super().__init__(GoHomebrewAdapter())


class NativeReaderToolFactory(NativeToolFactory):
    def __init__(self) -> None:
        super().__init__(NativeReaderAdapter())


class SignalProbeToolFactory(NativeToolFactory):
    def __init__(self) -> None:
        super().__init__(SignalProbeAdapter())


class VobManifestToolFactory(NativeToolFactory):
    def __init__(self) -> None:
        super().__init__(VobManifestAdapter())
