from __future__ import annotations

from abc import ABC
from pathlib import Path
from typing import Final


class NativeToolError(RuntimeError):
    """Error emitted by a native tool adapter."""


class NativeExecutableAdapter(ABC):
    """Base contract for command-line native executables."""

    tool_name: str
    executable: Final[Path]

    def __init__(self, executable: Path | str, *, tool_name: str | None = None) -> None:
        self.tool_name = tool_name or self.__class__.__name__
        self.executable = Path(executable)

    @property
    def available(self) -> bool:
        return self.executable.exists()

    def command(self, *args: str) -> list[str] | None:
        if not self.available:
            return None
        return [str(self.executable), *args]

    def command_or_error(self, *args: str) -> list[str]:
        command = self.command(*args)
        if not command:
            raise NativeToolError(f"{self.tool_name} is not available: {self.executable}")
        return command


class NativeToolFactory(ABC):
    """Factory contract to expose explicit native command builders."""

    def __init__(self, adapter: NativeExecutableAdapter):
        self.adapter = adapter

    @property
    def executable(self) -> Path:
        return self.adapter.executable

    @property
    def available(self) -> bool:
        return self.adapter.available

    def command(self, *args: str) -> list[str] | None:
        return self.adapter.command(*args)
