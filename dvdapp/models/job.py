from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass
class RipJob:
    id: str
    device: str
    output: str
    status: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: float | None = None
    log_tail: str = ""
    error: str | None = None
    pid: int | None = None
    attempts: int = 0
    attempts_total: int = 0
    current_command: str | None = None
    heartbeat: float = field(default_factory=time.time)
    mode: str = "normal"
    notes: list[str] = field(default_factory=list)
