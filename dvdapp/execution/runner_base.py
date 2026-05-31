"""Execution primitives for concrete attempt runners.

The execution layer is intentionally split in small composable classes:
- base runners in this module,
- concrete tools in ``runners/*``,
- dispatcher in ``dispatcher.py``.
"""

from __future__ import annotations

import select
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


ErrorLineHandler = Callable[[str, list[str]], None]


@dataclass
class SubprocessRunResult:
    """Result object for a command-line runner execution."""

    return_code: int | None
    timed_out: bool
    error_lines: list[str] = field(default_factory=list)


class BaseAttemptRunner(ABC):
    """Template d'exécution d'une étape d'extraction."""

    tool_name = "base"

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    @abstractmethod
    def supports(self, command: dict, argv: list[str]) -> bool:
        raise NotImplementedError

    @staticmethod
    def to_argv(command_or_argv: dict | list[str] | None) -> list[str]:
        if isinstance(command_or_argv, list):
            return list(command_or_argv)
        if isinstance(command_or_argv, dict):
            argv = command_or_argv.get("argv", [])
            if isinstance(argv, list):
                return list(argv)
        return []

    def run(self, job_id: str, command: dict, timeout: int | None = None):
        return self._run(job_id, command, timeout)

    @abstractmethod
    def _run(self, job_id: str, command: dict, timeout: int | None) -> tuple[int | None, str | None]:
        raise NotImplementedError

    def _consume_lines(self, proc: subprocess.Popen[str], stream, on_line, timeout_seconds: int | None, job_id: str) -> bool:
        return _consume_lines(
            manager=self.manager,
            job_id=job_id,
            proc=proc,
            stream=stream,
            on_line=on_line,
            timeout_seconds=timeout_seconds,
        )

    def _request_cancel(self, proc: subprocess.Popen[str] | None) -> None:
        return self._kill_process(proc)

    @staticmethod
    def _kill_process(proc: subprocess.Popen[str] | None) -> None:
        if not proc:
            return
        if proc.poll() is not None:
            return

        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                return
            except Exception:
                continue

            for _ in range(5):
                if proc.poll() is not None:
                    return
                time.sleep(0.1)


class SubprocessAttemptRunner(BaseAttemptRunner, ABC):
    """Runner base for any command-line extractor/step."""

    tool_name = "subprocess"
    failure_tokens = ("error", "failed", "invalid", "unable", "cannot", "permission")
    stream_name = "stderr"
    capture_mode = "stderr"

    def _run_subprocess(
        self,
        job_id: str,
        argv: list[str],
        *,
        timeout: int | None = None,
        on_output_line: ErrorLineHandler,
        output_tokens: tuple[str, ...] | None = None,
        capture_mode: str | None = None,
        stream_name: str | None = None,
    ) -> SubprocessRunResult:
        if timeout is None:
            timeout = self.manager.DEFAULT_CMD_TIMEOUT_SECONDS

        if capture_mode is None:
            capture_mode = self.capture_mode
        if stream_name is None:
            stream_name = self.stream_name
        error_tokens = output_tokens or self.failure_tokens
        if not argv:
            return SubprocessRunResult(1, False, ["empty command"])

        capture_stdout = False
        capture_stderr = False
        if capture_mode == "stderr":
            capture_stderr = True
        elif capture_mode == "stdout":
            capture_stdout = True
        elif capture_mode == "stdout_and_stderr":
            capture_stdout = True
            capture_stderr = True

        stdout_target = subprocess.PIPE if capture_stdout else subprocess.DEVNULL
        stderr_target = subprocess.PIPE if capture_stderr else subprocess.DEVNULL
        if capture_mode == "stdout_and_stderr":
            stderr_target = subprocess.STDOUT

        try:
            proc = subprocess.Popen(
                argv,
                stdout=stdout_target,
                stderr=stderr_target,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            raise RuntimeError(f"cannot launch {self.tool_name} process: {exc}") from exc

        self.manager._set_job_state(job_id, pid=proc.pid)

        stream = proc.stdout if stream_name == "stdout" else proc.stderr
        error_lines: list[str] = []

        def on_line(raw: str) -> None:
            if raw is None:
                return
            clean = str(raw).rstrip()
            if not clean:
                return

            self.manager._append_job_tail(job_id, clean)
            lowered = clean.lower()
            on_output_line(clean, error_lines)

            if len(error_lines) < self.manager.MAX_ATTEMPT_FAILURE_LINES and any(
                token in lowered for token in error_tokens
            ):
                error_lines.append(clean)

        timed_out = self._consume_lines(
            proc=proc,
            stream=stream,
            on_line=on_line,
            timeout_seconds=timeout,
            job_id=job_id,
        )

        return_code: int | None
        try:
            return_code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._request_cancel(proc)
            return_code = proc.wait(timeout=10)
        except Exception:
            return_code = 1
            error_lines.append("process wait failed")

        self.manager._heartbeat_job(job_id)

        try:
            if proc.poll() is not None:
                proc.terminate()
                proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
        except Exception:
            pass

        self.manager._set_job_state(job_id, pid=None)
        return SubprocessRunResult(return_code, timed_out, error_lines)

    @staticmethod
    def from_list(command_or_argv: dict | list[str]) -> list[str]:
        return BaseAttemptRunner.to_argv(command_or_argv)


class PathTool:
    @staticmethod
    def from_argv0(value: str) -> str:
        return str(value).split("/")[-1]


def _consume_lines(
    manager: Any,
    job_id: str,
    proc: subprocess.Popen[str],
    stream,
    on_line,
    timeout_seconds: int | None,
) -> bool:
    idle_poll = manager.COMMAND_IDLE_POLL_SECONDS
    max_stall = manager.MAX_STALL_READ_ITERATIONS

    deadline = None
    if timeout_seconds and timeout_seconds > 0:
        deadline = time.time() + timeout_seconds

    stall_count = 0
    while True:
        if manager._is_cancelled(job_id):
            manager._request_cancelled_process(proc)
            break

        remaining = None
        if deadline is not None:
            remaining = deadline - time.time()
            if remaining <= 0:
                manager._append_job_tail(job_id, f"Process timeout after {timeout_seconds}s")
                manager._request_cancelled_process(proc)
                return True

        timeout = idle_poll
        if remaining is not None:
            timeout = min(idle_poll, max(0.05, remaining))

        if stream is None:
            timeout = None

        ready = False
        if stream is not None:
            try:
                ready_list, _, _ = select.select([stream], [], [], timeout)
                if ready_list:
                    ready = True
            except (OSError, ValueError):
                ready = False

        if ready and stream is not None:
            line = stream.readline()
            if line:
                on_line(line)
                manager._heartbeat_job(job_id)
                stall_count = 0
                continue

        poll = proc.poll()
        if poll is not None:
            break

        if not ready:
            stall_count += 1
            if stall_count > max_stall:
                manager._append_job_tail(job_id, "Process stalled, no output for too long")
                manager._request_cancelled_process(proc)
                return True

        if manager._is_cancelled(job_id):
            manager._request_cancelled_process(proc)
            return True

    return False
