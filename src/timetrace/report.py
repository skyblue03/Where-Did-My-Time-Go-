from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import RunRecord
from .utils import format_duration, abbreviate_path


def local_day_bounds(now_local: datetime) -> tuple[datetime, datetime]:
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start, end


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Report:
    title: str
    total_s: float
    success_s: float
    failed_s: float
    by_project: list[tuple[str, float]]
    by_category: list[tuple[str, float]]
    top_commands: list[tuple[str, float, int, int]]  # cmd, total_s, runs, failed_runs


def _project_key(r: RunRecord) -> str:
    if r.project:
        return r.project
    # fallback: use last directory name, but keep a hint if path is long
    p = r.cwd.rstrip("/\\")
    last = p.split("\\")[-1].split("/")[-1] if p else p
    if not last:
        last = abbreviate_path(r.cwd)
    return last


def build_report(runs: Iterable[RunRecord], title: str) -> Report:
    total_s = 0.0
    success_s = 0.0
    failed_s = 0.0

    proj: dict[str, float] = {}
    cat: dict[str, float] = {}
    cmd_total: dict[str, float] = {}
    cmd_runs: dict[str, int] = {}
    cmd_failed: dict[str, int] = {}

    for r in runs:
        total_s += r.duration_s
        if r.exit_code == 0:
            success_s += r.duration_s
        else:
            failed_s += r.duration_s

        pk = _project_key(r)
        proj[pk] = proj.get(pk, 0.0) + r.duration_s

        ck = r.category or "other"
        cat[ck] = cat.get(ck, 0.0) + r.duration_s

        cmd_total[r.command] = cmd_total.get(r.command, 0.0) + r.duration_s
        cmd_runs[r.command] = cmd_runs.get(r.command, 0) + 1
        if r.exit_code != 0:
            cmd_failed[r.command] = cmd_failed.get(r.command, 0) + 1

    by_project = sorted(proj.items(), key=lambda x: x[1], reverse=True)[:12]
    by_category = sorted(cat.items(), key=lambda x: x[1], reverse=True)[:12]

    top_cmds_sorted = sorted(cmd_total.items(), key=lambda x: x[1], reverse=True)[:12]
    top_commands: list[tuple[str, float, int, int]] = []
    for cmd, tot in top_cmds_sorted:
        top_commands.append((cmd, tot, cmd_runs.get(cmd, 0), cmd_failed.get(cmd, 0)))

    return Report(
        title=title,
        total_s=total_s,
        success_s=success_s,
        failed_s=failed_s,
        by_project=by_project,
        by_category=by_category,
        top_commands=top_commands,
    )


def render_report_text(rep: Report) -> str:
    lines: list[str] = []
    lines.append(rep.title)
    lines.append("")
    lines.append(f"Total tracked: {format_duration(rep.total_s)}")
    lines.append(f"Successful:   {format_duration(rep.success_s)}")
    lines.append(f"Failed:       {format_duration(rep.failed_s)}")
    if rep.total_s > 0 and rep.failed_s > 0:
        ratio = rep.failed_s / rep.total_s
        lines.append(f"Fail ratio:   {ratio:.0%}")
    lines.append("")

    if rep.by_category and rep.total_s > 0:
        lines.append("By category:")
        for name, secs in rep.by_category:
            lines.append(f"  {name:<14} {format_duration(secs):>10}")
        lines.append("")
    else:
        lines.append("By category: (no data)")
        lines.append("")

    if rep.by_project:
        lines.append("By project:")
        for name, secs in rep.by_project:
            lines.append(f"  {name:<20} {format_duration(secs):>10}")
        lines.append("")
    else:
        lines.append("By project: (no data)")
        lines.append("")

    if rep.top_commands:
        lines.append("Top commands:")
        for cmd, secs, runs, failed_runs in rep.top_commands:
            fail_note = f", {failed_runs} failed" if failed_runs else ""
            lines.append(f"  {format_duration(secs):>10}  ({runs} runs{fail_note})  {cmd}")
        lines.append("")
    else:
        lines.append("Top commands: (no data)")
        lines.append("")

    if rep.failed_s > 0:
        lines.append(f"Wasted-time signal: {format_duration(rep.failed_s)} on failing commands.")
    return "\n".join(lines)
