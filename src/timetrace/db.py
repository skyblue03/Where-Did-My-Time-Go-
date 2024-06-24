from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import RunRecord
from .utils import ensure_dir, default_data_dir


SCHEMA_VERSION = 2


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


@dataclass(frozen=True)
class DBPaths:
    data_dir: Path
    db_path: Path


def resolve_db_path(explicit_path: Optional[str] = None) -> DBPaths:
    if explicit_path:
        p = Path(explicit_path).expanduser().resolve()
        ensure_dir(p.parent)
        return DBPaths(data_dir=p.parent, db_path=p)

    data_dir = default_data_dir()
    ensure_dir(data_dir)
    db_path = data_dir / "timetrace.db"
    return DBPaths(data_dir=data_dir, db_path=db_path)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def _table_has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r["name"] == col for r in rows)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at_utc TEXT NOT NULL,
            finished_at_utc TEXT NOT NULL,
            duration_s REAL NOT NULL,
            exit_code INTEGER NOT NULL,
            cwd TEXT NOT NULL,
            command TEXT NOT NULL,
            tag TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at_utc);
        CREATE INDEX IF NOT EXISTS idx_runs_cwd ON runs(cwd);
        CREATE INDEX IF NOT EXISTS idx_runs_tag ON runs(tag);
        """
    )

    # Lightweight schema migration for v2:
    # Add columns if missing.
    if not _table_has_column(conn, "runs", "project"):
        conn.execute("ALTER TABLE runs ADD COLUMN project TEXT;")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project);")
    if not _table_has_column(conn, "runs", "category"):
        conn.execute("ALTER TABLE runs ADD COLUMN category TEXT;")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_category ON runs(category);")

    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version';")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?);", (str(SCHEMA_VERSION),))
    else:
        # bump stored schema version if older
        try:
            v = int(row["value"])
        except Exception:
            v = 0
        if v < SCHEMA_VERSION:
            conn.execute("UPDATE meta SET value=? WHERE key='schema_version';", (str(SCHEMA_VERSION),))
    conn.commit()


def insert_run(
    conn: sqlite3.Connection,
    *,
    started_at_utc: datetime,
    finished_at_utc: datetime,
    duration_s: float,
    exit_code: int,
    cwd: str,
    command: str,
    tag: Optional[str],
    project: Optional[str],
    category: Optional[str],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs(
            started_at_utc, finished_at_utc, duration_s, exit_code, cwd, command, tag, project, category
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            to_iso(started_at_utc),
            to_iso(finished_at_utc),
            float(duration_s),
            int(exit_code),
            str(cwd),
            str(command),
            tag,
            project,
            category,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def fetch_runs_between(
    conn: sqlite3.Connection,
    *,
    start_utc: datetime,
    end_utc: datetime,
    limit: int = 10000,
    cwd_prefix: Optional[str] = None,
    tag: Optional[str] = None,
    project: Optional[str] = None,
    category: Optional[str] = None,
) -> list[RunRecord]:
    q = """
        SELECT id, started_at_utc, finished_at_utc, duration_s, exit_code, cwd, command, tag, project, category
        FROM runs
        WHERE started_at_utc >= ? AND started_at_utc < ?
    """
    params: list[object] = [to_iso(start_utc), to_iso(end_utc)]

    if cwd_prefix:
        q += " AND cwd LIKE ?"
        params.append(str(cwd_prefix) + "%")
    if tag:
        q += " AND tag = ?"
        params.append(tag)
    if project:
        q += " AND project = ?"
        params.append(project)
    if category:
        q += " AND category = ?"
        params.append(category)

    q += " ORDER BY started_at_utc ASC LIMIT ?"
    params.append(int(limit))

    rows = conn.execute(q, params).fetchall()
    out: list[RunRecord] = []
    for r in rows:
        out.append(
            RunRecord(
                id=int(r["id"]),
                started_at_utc=from_iso(r["started_at_utc"]),
                finished_at_utc=from_iso(r["finished_at_utc"]),
                duration_s=float(r["duration_s"]),
                exit_code=int(r["exit_code"]),
                cwd=str(r["cwd"]),
                command=str(r["command"]),
                tag=(str(r["tag"]) if r["tag"] is not None else None),
                project=(str(r["project"]) if r["project"] is not None else None),
                category=(str(r["category"]) if r["category"] is not None else None),
            )
        )
    return out


def fetch_recent_runs(conn: sqlite3.Connection, *, limit: int = 20) -> list[RunRecord]:
    rows = conn.execute(
        """
        SELECT id, started_at_utc, finished_at_utc, duration_s, exit_code, cwd, command, tag, project, category
        FROM runs
        ORDER BY started_at_utc DESC
        LIMIT ?;
        """,
        (int(limit),),
    ).fetchall()

    out: list[RunRecord] = []
    for r in rows:
        out.append(
            RunRecord(
                id=int(r["id"]),
                started_at_utc=from_iso(r["started_at_utc"]),
                finished_at_utc=from_iso(r["finished_at_utc"]),
                duration_s=float(r["duration_s"]),
                exit_code=int(r["exit_code"]),
                cwd=str(r["cwd"]),
                command=str(r["command"]),
                tag=(str(r["tag"]) if r["tag"] is not None else None),
                project=(str(r["project"]) if r["project"] is not None else None),
                category=(str(r["category"]) if r["category"] is not None else None),
            )
        )
    return out
