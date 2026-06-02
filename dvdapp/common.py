from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CommandResult:
    return_code: int
    stdout: str
    stderr: str


def run_cmd(command, timeout: int = 8, check: bool = False, cwd: Optional[Path] = None) -> CommandResult:
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError:
        return CommandResult(127, "", f"command not found: {command[0]}")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return CommandResult(124, stdout, stderr or f"command timed out after {timeout}s: {command[0]}")
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, command, output=proc.stdout, stderr=proc.stderr)
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
