from __future__ import annotations

from typing import Any

from .runner_base import BaseAttemptRunner
from .runners import (
    FfmpegAttemptRunner,
    HandBrakeAttemptRunner,
    PipelineAttemptRunner,
    GoRunnerAttemptRunner,
    HomebrewAttemptRunner,
    NativeDumpAttemptRunner,
)


class CommandAttemptDispatcher:
    """Point d'entrée unique pour exécuter un essai."""

    _HAND_BRAKE_KEYWORDS = ("handbrake",)

    _TOOL_ALIASES = {
        "dvd_reader_dump": "native",
        "dvd_homebrew": "homebrew",
        "dvd_homebrew_runner": "go_runner",
        "dvdhomebrewrunner": "go_runner",
        "dvd_homebrew-go": "go_runner",
        "go_homebrew": "go_runner",
        "pipeline": "pipeline",
    }

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.native = NativeDumpAttemptRunner(manager)
        self.homebrew = HomebrewAttemptRunner(manager)
        self.go_runner = GoRunnerAttemptRunner(manager)
        self.ffmpeg = FfmpegAttemptRunner(manager)
        self.handbrake = HandBrakeAttemptRunner(manager)
        self.pipeline = PipelineAttemptRunner(
            manager,
            executors=[self.native, self.homebrew, self.go_runner, self.ffmpeg, self.handbrake],
        )

        self.ordered_runners: list[BaseAttemptRunner] = [
            self.pipeline,
            self.native,
            self.homebrew,
            self.go_runner,
            self.ffmpeg,
            self.handbrake,
        ]

    def run(self, job_id: str, command: dict) -> tuple[int | None, str | None]:
        if not isinstance(command, dict):
            return 1, "invalid command definition"

        if self.pipeline.supports(command, list(command.get("argv", []))):
            return self.pipeline.run(job_id, command)

        steps = command.get("pipeline")
        if isinstance(steps, list):
            return self.pipeline.run(job_id, command)

        argv = list(command.get("argv", []))
        if not argv:
            return 1, "empty command"

        runner = self._resolve_runner(command, argv)
        return runner.run(job_id, command, int(command.get("timeout") or self.manager.DEFAULT_CMD_TIMEOUT_SECONDS))

    def _resolve_runner(self, command: dict, argv: list[str]) -> BaseAttemptRunner:
        explicit_tool = str(command.get("tool") or "").strip().lower()
        if explicit_tool:
            runner = self._resolve_by_tool(explicit_tool)
            if runner is not None:
                return runner

        for runner in self.ordered_runners[1:]:
            if runner.supports(command, argv):
                return runner
        return self.ffmpeg

    def _resolve_by_tool(self, tool: str) -> BaseAttemptRunner | None:
        normalized = str(tool).strip().lower()
        if not normalized:
            return None

        if alias := self._TOOL_ALIASES.get(normalized):
            return {
                "native": self.native,
                "homebrew": self.homebrew,
                "go_runner": self.go_runner,
                "pipeline": self.pipeline,
            }.get(alias)

        if any(token in normalized for token in self._HAND_BRAKE_KEYWORDS):
            return self.handbrake
        return None
