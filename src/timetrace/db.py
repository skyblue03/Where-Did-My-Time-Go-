from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import RunRecord
from .utils import ensure_dir, default_data_dir


SCHEMA_VERSION = 3


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

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            ended_at_utc TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at_utc);
        CREATE INDEX IF NOT EXISTS idx_sessions_name ON sessions(name);

        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at_utc TEXT NOT NULL,
            finished_at_utc TEXT NOT NULL,
            duration_s REAL NOT NULL,
            exit_code INTEGER NOT NULL,
            cwd TEXT NOT NULL,
            command TEXT NOT NULL,
            tag TEXT,
            project TEXT,
            category TEXT,
            session_id INTEGER,
            FOREIGN KEY(session_id) REFERENCES sessions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at_utc);
        CREATE INDEX IF NOT EXISTS idx_runs_cwd ON runs(cwd);
        CREATE INDEX IF NOT EXISTS idx_runs_tag ON runs(tag);
        CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project);
        CREATE INDEX IF NOT EXISTS idx_runs_category ON runs(category);
        CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
        """
    )

    # If upgrading from older versions, columns may be missing in runs.
    if not _table_has_column(conn, "runs", "project"):
        conn.execute("ALTER TABLE runs ADD COLUMN project TEXT;")
    if not _table_has_column(conn, "runs", "category"):
        conn.execute("ALTER TABLE runs ADD COLUMN category TEXT;")
    if not _table_has_column(conn, "runs", "session_id"):
        conn.execute("ALTER TABLE runs ADD COLUMN session_id INTEGER;")

    cur = conn.execute("SELECT value FROM meta WHERE key='schema_version';")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?);", (str(SCHEMA_VERSION),))
    else:
        try:
            v = int(row["value"])
        except Exception:
            v = 0
        if v < SCHEMA_VERSION:
            conn.execute("UPDATE meta SET value=? WHERE key='schema_version';", (str(SCHEMA_VERSION),))
    conn.commit()


def get_active_session_id(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute("SELECT value FROM meta WHERE key='active_session_id';").fetchone()
    if not row:
        return None
    try:
        return int(row["value"])
    except Exception:
        return None


def set_active_session_id(conn: sqlite3.Connection, session_id: Optional[int]) -> None:
    if session_id is None:
        conn.execute("DELETE FROM meta WHERE key='active_session_id';")
    else:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('active_session_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
            (str(int(session_id)),),
        )
    conn.commit()


def create_session(conn: sqlite3.Connection, *, name: str, started_at_utc: datetime) -> int:
    cur = conn.execute(
        "INSERT INTO sessions(name, started_at_utc) VALUES(?, ?);",
        (name, to_iso(started_at_utc)),
    )
    conn.commit()
    return int(cur.lastrowid)


def end_session(conn: sqlite3.Connection, *, session_id: int, ended_at_utc: datetime) -> None:
    conn.execute("UPDATE sessions SET ended_at_utc=? WHERE id=?;", (to_iso(ended_at_utc), int(session_id)))
    conn.commit()


def fetch_sessions(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, started_at_utc, ended_at_utc FROM sessions ORDER BY started_at_utc DESC LIMIT ?;",
        (int(limit),),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "started_at_utc": str(r["started_at_utc"]),
                "ended_at_utc": (str(r["ended_at_utc"]) if r["ended_at_utc"] is not None else None),
            }
        )
    return out


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
    session_id: Optional[int],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO runs(
            started_at_utc, finished_at_utc, duration_s, exit_code, cwd, command, tag, project, category, session_id
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
            (int(session_id) if session_id is not None else None),
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
    tag: Optional[str] = None,
    project: Optional[str] = None,
    category: Optional[str] = None,
    session_id: Optional[int] = None,
) -> list[RunRecord]:
    q = """
        SELECT
            r.id, r.started_at_utc, r.finished_at_utc, r.duration_s, r.exit_code, r.cwd, r.command,
            r.tag, r.project, r.category, r.session_id, s.name AS session_name
        FROM runs r
        LEFT JOIN sessions s ON s.id = r.session_id
        WHERE r.started_at_utc >= ? AND r.started_at_utc < ?
    """
    params: list[object] = [to_iso(start_utc), to_iso(end_utc)]

    if tag:
        q += " AND r.tag = ?"
        params.append(tag)
    if project:
        q += " AND r.project = ?"
        params.append(project)
    if category:
        q += " AND r.category = ?"
        params.append(category)
    if session_id is not None:
        q += " AND r.session_id = ?"
        params.append(int(session_id))

    q += " ORDER BY r.started_at_utc ASC LIMIT ?"
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
                session_id=(int(r["session_id"]) if r["session_id"] is not None else None),
                session_name=(str(r["session_name"]) if r["session_name"] is not None else None),
            )
        )
    return out


def fetch_recent_runs(conn: sqlite3.Connection, *, limit: int = 20) -> list[RunRecord]:
    rows = conn.execute(
        """
        SELECT
            r.id, r.started_at_utc, r.finished_at_utc, r.duration_s, r.exit_code, r.cwd, r.command,
            r.tag, r.project, r.category, r.session_id, s.name AS session_name
        FROM runs r
        LEFT JOIN sessions s ON s.id = r.session_id
        ORDER BY r.started_at_utc DESC
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
                session_id=(int(r["session_id"]) if r["session_id"] is not None else None),
                session_name=(str(r["session_name"]) if r["session_name"] is not None else None),
            )
        )
    return out
