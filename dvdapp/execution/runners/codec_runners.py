from __future__ import annotations

import re
from typing import Any

from ..runner_base import BaseAttemptRunner, PathTool, SubprocessAttemptRunner


class FfmpegAttemptRunner(SubprocessAttemptRunner):
    tool_name = "ffmpeg"

    def supports(self, command: dict, argv: list[str]) -> bool:
        selected_tool = str(command.get("tool", "")).lower()
        first = argv[0] if argv else ""
        return self.tool_name in selected_tool or self.tool_name in str(first).lower()

    def _run(self, job_id: str, command: dict, timeout: int | None = None) -> tuple[int | None, str | None]:
        argv = self.to_argv(command)
        if not argv:
            return 1, "empty ffmpeg command"

        def parse_output(clean: str, error_lines: list[str]) -> None:
            if clean.strip():
                if "time=" in clean:
                    match = re.search(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", clean)
                    if match and duration_seconds[0] is not None:
                        current = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
                        duration = duration_seconds[0]
                        if duration:
                            current_progress = min(100.0, max(0.0, (current / duration) * 100.0))
                            self.manager._set_job_state(job_id, progress=current_progress)

                if "Duration:" in clean:
                    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", clean)
                    if match:
                        duration_seconds[0] = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(
                            match.group(3)
                        )

        duration_seconds: list[float | None] = [None]
        result = self._run_subprocess(
            job_id,
            argv,
            timeout=timeout,
            on_output_line=parse_output,
            output_tokens=("error", "failed", "invalid", "unable", "cannot"),
            capture_mode="stderr",
            stream_name="stderr",
        )

        if result.return_code != 0:
            self.manager._append_job_tail(job_id, f"ffmpeg exited with code {result.return_code}")
            if result.timed_out:
                return result.return_code, f"ffmpeg timeout after {timeout or self.manager.DEFAULT_CMD_TIMEOUT_SECONDS}s"
            return result.return_code, self.manager._summarize_error(result.error_lines, result.return_code, "ffmpeg")

        return 0, self.manager._summarize_error(result.error_lines, 0, "ffmpeg")


class HandBrakeAttemptRunner(SubprocessAttemptRunner):
    tool_name = "handbrake"
    capture_mode = "stdout_and_stderr"
    stream_name = "stdout"

    def supports(self, command: dict, argv: list[str]) -> bool:
        selected_tool = str(command.get("tool", "")).lower()
        return (
            (argv and PathTool.from_argv0(argv[0]).lower().startswith("handbrakecli"))
            or (selected_tool and "handbrake" in selected_tool)
        )

    def _run(self, job_id: str, command: dict, timeout: int | None = None) -> tuple[int | None, str | None]:
        argv = self.to_argv(command)
        if not argv:
            return 1, "empty handbrake command"

        def parse_output(_clean: str, _error_lines: list[str]) -> None:
            # Keep hook for future parser evolutions; no job-specific parsing today.
            return

        result = self._run_subprocess(
            job_id,
            argv,
            timeout=timeout,
            on_output_line=parse_output,
            output_tokens=("error", "failed", "error while", "not found", "unable"),
            capture_mode="stdout_and_stderr",
            stream_name="stdout",
        )

        if result.return_code != 0:
            if result.return_code is not None:
                self.manager._append_job_tail(job_id, f"HandBrakeCLI exited with code {result.return_code}")
            if result.timed_out:
                return result.return_code, f"HandBrakeCLI timeout after {timeout or self.manager.DEFAULT_CMD_TIMEOUT_SECONDS}s"
            return result.return_code, self.manager._summarize_error(result.error_lines, result.return_code, "handbrake")

        return result.return_code, None if result.return_code == 0 else self.manager._summarize_error(
            result.error_lines,
            result.return_code,
            "handbrake",
        )


class PipelineAttemptRunner(BaseAttemptRunner):
    """Runs a structured pipeline of tool-specific attempt runners."""

    def __init__(self, manager: Any, executors: list[BaseAttemptRunner]) -> None:
        super().__init__(manager)
        self.executors = executors

    def supports(self, command: dict, argv: list[str]) -> bool:
        return command.get("tool") == "pipeline" or bool(command.get("pipeline"))

    def run(self, job_id: str, command: dict, timeout: int | None = None) -> tuple[int | None, str | None]:
        return self._run(job_id, command, timeout)

    def _run(self, job_id: str, command: dict, timeout: int | None = None) -> tuple[int | None, str | None]:
        steps = command.get("pipeline")
        if not isinstance(steps, list) or not steps:
            return 1, "invalid native pipeline"

        seen_errors: list[str] = []
        total_steps = len(steps)

        for idx, step in enumerate(steps, start=1):
            self.manager._append_job_tail(job_id, f"Pipeline étape {idx}/{total_steps}")

            if not isinstance(step, dict) or "argv" not in step:
                msg = f"pipeline step {idx} missing argv"
                self.manager._append_job_tail(job_id, msg)
                return 1, msg

            timeout_step = int(step.get("timeout") or command.get("timeout") or self.manager.DEFAULT_CMD_TIMEOUT_SECONDS)
            step_tool = str(step.get("tool") or "").lower()
            step_command = list(step.get("argv", []))
            if not step_command:
                return 1, "empty pipeline step"

            runner = self._select_runner(step_tool, step_command)
            if not runner:
                return 1, f"no runner for pipeline step {idx}"

            return_code, step_error = runner.run(job_id, step, timeout_step)
            if return_code != 0:
                if step_error:
                    return return_code, step_error
                return return_code, f"pipeline step failed ({step_tool or 'unknown'})"

            if step_error:
                seen_errors.append(step_error)

        return 0, seen_errors[-1] if seen_errors else None

    def _select_runner(self, step_tool: str, argv: list[str]) -> BaseAttemptRunner | None:
        tool = step_tool.lower()
        for executor in self.executors:
            if executor.supports({"tool": tool, "argv": argv}, argv):
                return executor
        return None
