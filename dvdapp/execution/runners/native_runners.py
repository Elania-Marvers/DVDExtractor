from __future__ import annotations

from pathlib import Path

from ..runner_base import BaseAttemptRunner, PathTool, SubprocessAttemptRunner


def _resolve_output_path(command: dict) -> Path | None:
    output_path = command.get("output_path")
    if not output_path and "--output" in command.get("argv", []):
        argv = list(command.get("argv", []))
        idx = argv.index("--output")
        if idx + 1 < len(argv):
            output_path = argv[idx + 1]
    elif isinstance(command.get("argv"), list) and command["argv"] and str(command["argv"][-1]).endswith(".vob"):
        output_path = command["argv"][-1]

    if output_path:
        return Path(str(output_path))
    return None


def _collect_error_lines(
    clean: str,
    error_lines: list[str],
    *,
    max_lines: int,
    tokens: tuple[str, ...],
) -> None:
    if len(error_lines) >= max_lines:
        return
    lowered = clean.lower()
    if any(token in lowered for token in tokens):
        error_lines.append(clean)


class NativeDumpAttemptRunner(SubprocessAttemptRunner):
    tool_name = "dvd_reader_dump"
    capture_mode = "stdout"
    stream_name = "stdout"

    def supports(self, command: dict, argv: list[str]) -> bool:
        return (argv and PathTool.from_argv0(argv[0]).endswith("dvd_reader_dump")) or command.get("tool") == "dvd_reader_dump"

    def _run(self, job_id: str, command: dict, timeout: int | None = None) -> tuple[int | None, str | None]:
        argv = self.to_argv(command)
        if not argv:
            return 1, "empty native command"

        output_file = _resolve_output_path(command)
        result = self._run_subprocess(
            job_id,
            argv,
            timeout=timeout,
            on_output_line=lambda clean, error_lines: _collect_error_lines(
                clean,
                error_lines,
                max_lines=self.manager.MAX_ATTEMPT_FAILURE_LINES,
                tokens=("error", "failed", "invalid", "cannot", "permission"),
            ),
            output_tokens=(),
            capture_mode="stdout",
            stream_name="stdout",
        )

        if output_file and output_file.exists() and output_file.stat().st_size <= 0:
            return 18, "native dump produced empty output"

        if result.return_code != 0:
            if output_file:
                size = output_file.stat().st_size if output_file.exists() else 0
                self.manager._append_job_tail(job_id, f"dvd_reader_dump exited with code {result.return_code}")
                self.manager._append_job_tail(job_id, f"dump output size={size}")
            if result.timed_out:
                return result.return_code, f"native dump timeout after {timeout or self.manager.DEFAULT_CMD_TIMEOUT_SECONDS}s"
            return result.return_code, self.manager._summarize_error(result.error_lines, result.return_code, "dvd_reader_dump")

        return 0, self.manager._summarize_error(result.error_lines, 0, "dvd_reader_dump")


class HomebrewAttemptRunner(SubprocessAttemptRunner):
    tool_name = "dvd_homebrew"
    capture_mode = "stdout_and_stderr"
    stream_name = "stdout"

    def supports(self, command: dict, argv: list[str]) -> bool:
        return (argv and PathTool.from_argv0(argv[0]).endswith("dvd_homebrew")) or command.get("tool") == "dvd_homebrew"

    def _run(self, job_id: str, command: dict, timeout: int | None = None) -> tuple[int | None, str | None]:
        argv = self.to_argv(command)
        if not argv:
            return 1, "empty homebrew command"

        output_file = _resolve_output_path(command)
        result = self._run_subprocess(
            job_id,
            argv,
            timeout=timeout,
            on_output_line=lambda clean, error_lines: _collect_error_lines(
                clean,
                error_lines,
                max_lines=self.manager.MAX_ATTEMPT_FAILURE_LINES,
                tokens=("error", "failed", "invalid", "permission", "homebrew_error"),
            ),
            output_tokens=(),
            capture_mode="stdout",
            stream_name="stdout",
        )

        if result.return_code == 0:
            if output_file and output_file.exists() and output_file.stat().st_size > 0:
                return 0, self.manager._summarize_error(result.error_lines, 0, "dvd_homebrew")
            return 18, "homebrew produced empty output"

        if result.timed_out:
            return result.return_code, f"homebrew timeout after {timeout or self.manager.DEFAULT_CMD_TIMEOUT_SECONDS}s"

        if output_file:
            size = output_file.stat().st_size if output_file.exists() else 0
            self.manager._append_job_tail(job_id, f"dvd_homebrew exited with code {result.return_code}")
            self.manager._append_job_tail(job_id, f"homebrew output size={size}")

        return result.return_code, self.manager._summarize_error(result.error_lines, result.return_code, "dvd_homebrew")


class GoRunnerAttemptRunner(SubprocessAttemptRunner):
    tool_name = "dvd_homebrew_runner"
    capture_mode = "stdout_and_stderr"
    stream_name = "stdout"

    def supports(self, command: dict, argv: list[str]) -> bool:
        return (argv and PathTool.from_argv0(argv[0]).endswith("dvd_homebrew_runner")) or command.get("tool") == "dvd_homebrew_runner"

    def _run(self, job_id: str, command: dict, timeout: int | None = None) -> tuple[int | None, str | None]:
        argv = self.to_argv(command)
        if not argv:
            return 1, "empty go homebrew command"

        output_file = _resolve_output_path(command)
        result = self._run_subprocess(
            job_id,
            argv,
            timeout=timeout,
            on_output_line=lambda clean, error_lines: _collect_error_lines(
                clean,
                error_lines,
                max_lines=self.manager.MAX_ATTEMPT_FAILURE_LINES,
                tokens=("error", "failed", "invalid", "permission"),
            ),
            output_tokens=(),
            capture_mode="stdout",
            stream_name="stdout",
        )

        if result.return_code == 0:
            if output_file and output_file.exists() and output_file.stat().st_size > 0:
                return 0, self.manager._summarize_error(result.error_lines, 0, "dvd_homebrew_runner")
            return 18, "go runner produced empty output"

        if result.timed_out:
            return result.return_code, f"go runner timeout after {timeout or self.manager.DEFAULT_CMD_TIMEOUT_SECONDS}s"

        if output_file:
            size = output_file.stat().st_size if output_file.exists() else 0
            self.manager._append_job_tail(job_id, f"dvd_homebrew_runner exited with code {result.return_code}")
            self.manager._append_job_tail(job_id, f"runner output size={size}")

        return result.return_code, self.manager._summarize_error(result.error_lines, result.return_code, "dvd_homebrew_runner")


__all__ = [
    "NativeDumpAttemptRunner",
    "HomebrewAttemptRunner",
    "GoRunnerAttemptRunner",
]
