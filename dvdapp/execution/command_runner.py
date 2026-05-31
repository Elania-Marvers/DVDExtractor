"""Execution layer façade.

The package keeps a small stable entry surface:
- concrete runners,
- dispatcher,
- shared base classes.
"""

from __future__ import annotations

from .runners import (
    FfmpegAttemptRunner,
    HandBrakeAttemptRunner,
    PipelineAttemptRunner,
    GoRunnerAttemptRunner,
    HomebrewAttemptRunner,
    NativeDumpAttemptRunner,
)
from .dispatcher import CommandAttemptDispatcher
from .runner_base import BaseAttemptRunner, SubprocessAttemptRunner

__all__ = [
    "FfmpegAttemptRunner",
    "HandBrakeAttemptRunner",
    "PipelineAttemptRunner",
    "GoRunnerAttemptRunner",
    "HomebrewAttemptRunner",
    "NativeDumpAttemptRunner",
    "BaseAttemptRunner",
    "SubprocessAttemptRunner",
    "CommandAttemptDispatcher",
]
