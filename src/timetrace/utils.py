from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_APP_DIRNAME = "timetrace"


def default_data_dir() -> Path:
    """Return OS-appropriate data directory for timetrace.

    - Windows: %APPDATA%\timetrace
    - macOS:  ~/Library/Application Support/timetrace
    - Linux:  ~/.local/share/timetrace (or $XDG_DATA_HOME)
    """
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / DEFAULT_APP_DIRNAME
        return Path.home() / "AppData" / "Roaming" / DEFAULT_APP_DIRNAME

    # XDG for *nix
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / DEFAULT_APP_DIRNAME

    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / DEFAULT_APP_DIRNAME

    return Path.home() / ".local" / "share" / DEFAULT_APP_DIRNAME


def sys_platform() -> str:
    import platform
    return platform.system().lower()


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def sanitize_command(argv: Sequence[str], max_len: int = 300) -> str:
    """Sanitize command arguments to reduce risk of storing secrets.

    Heuristics:
    - If argument matches --password=..., --token=..., etc, redact RHS
    - If argument is a flag that implies the next arg is secret, redact next
    - Redact long base64-like blobs
    """
    secret_keys = {
        "--password",
        "--pass",
        "--token",
        "--apikey",
        "--api-key",
        "--secret",
        "--client-secret",
        "--access-token",
        "--refresh-token",
        "--bearer",
    }
    secret_prefixes = tuple(k + "=" for k in secret_keys)

    redacted: list[str] = []
    it = iter(range(len(argv)))
    i = 0
    while i < len(argv):
        a = argv[i]
        # flag=secret
        if a.startswith(secret_prefixes):
            k = a.split("=", 1)[0]
            redacted.append(f"{k}=<redacted>")
            i += 1
            continue

        # flag secret
        if a in secret_keys and i + 1 < len(argv):
            redacted.append(a)
            redacted.append("<redacted>")
            i += 2
            continue

        # base64-like long strings (token-ish)
        if _looks_like_secret_blob(a):
            redacted.append("<redacted>")
            i += 1
            continue

        redacted.append(a)
        i += 1

    s = " ".join(shlex.quote(x) for x in redacted)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


_BLOB_RE = re.compile(r"^[A-Za-z0-9_\-]{40,}$")


def _looks_like_secret_blob(s: str) -> bool:
    if len(s) < 40:
        return False
    if _BLOB_RE.match(s) is None:
        return False
    # if it has at least one digit and one letter, more likely a token
    has_digit = any(c.isdigit() for c in s)
    has_alpha = any(c.isalpha() for c in s)
    return has_digit and has_alpha


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def abbreviate_path(p: str, max_len: int = 36) -> str:
    path = Path(p)
    parts = path.parts
    if len(str(path)) <= max_len:
        return str(path)
    if len(parts) <= 2:
        return "…" + str(path)[-max_len + 1 :]
    return f"…{os.sep}{parts[-2]}{os.sep}{parts[-1]}"
