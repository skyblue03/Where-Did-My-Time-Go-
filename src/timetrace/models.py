from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class RunRecord:
    id: int
    started_at_utc: datetime
    finished_at_utc: datetime
    duration_s: float
    exit_code: int
    cwd: str
    command: str
    tag: Optional[str]
