from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .db import resolve_db_path

DEFAULT_IGNORE_PREFIXES = [
    "cd",
    "dir",
    "ls",
    "pwd",
    "clear",
    "cls",
    "exit",
    "history",
    "timetrace",  # avoid recursive logging
]

@dataclass
class TTConfig:
    ignore_prefixes: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE_PREFIXES))
    ignore_regex: list[str] = field(default_factory=list)

    def should_ignore(self, command_str: str) -> bool:
        s = command_str.strip()
        if not s:
            return True
        first = s.split()[0].strip("'"")
        if first in self.ignore_prefixes:
            return True
        for pat in self.ignore_regex:
            try:
                if re.search(pat, s):
                    return True
            except re.error:
                # Ignore invalid regex entries rather than breaking the tool
                continue
        return False


def config_path(explicit_db_path: Optional[str] = None) -> Path:
    paths = resolve_db_path(explicit_db_path)
    return paths.data_dir / "config.json"


def load_config(explicit_db_path: Optional[str] = None) -> TTConfig:
    p = config_path(explicit_db_path)
    if not p.exists():
        return TTConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return TTConfig()

    cfg = TTConfig()
    if isinstance(data, dict):
        if isinstance(data.get("ignore_prefixes"), list):
            cfg.ignore_prefixes = [str(x) for x in data["ignore_prefixes"]]
        if isinstance(data.get("ignore_regex"), list):
            cfg.ignore_regex = [str(x) for x in data["ignore_regex"]]
    return cfg


def save_config(cfg: TTConfig, explicit_db_path: Optional[str] = None) -> Path:
    p = config_path(explicit_db_path)
    payload = {
        "ignore_prefixes": cfg.ignore_prefixes,
        "ignore_regex": cfg.ignore_regex,
    }
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p
