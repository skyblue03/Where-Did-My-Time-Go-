from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .categorize import categorize
from .db import resolve_db_path, connect, init_db, insert_run, fetch_runs_between, fetch_recent_runs
from .report import build_report, render_report_text, local_day_bounds, to_utc
from .utils import sanitize_command, format_duration


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="timetrace",
        description="Track command durations and generate local-first time reports.",
    )
    p.add_argument("--db", default=None, help="Path to the SQLite DB file (default: OS data directory).")
    p.add_argument("--version", action="store_true", help="Print version and exit.")

    sub = p.add_subparsers(dest="cmd", required=False)

    # run
    pr = sub.add_parser("run", help="Track a single command execution.")
    pr.add_argument("--tag", default=None, help="Optional tag to attach to this run (e.g., 'course', 'client').")
    pr.add_argument("--project", default=None, help="Optional project name override for grouping in reports.")
    pr.add_argument("--cwd", default=None, help="Working directory to run the command in (default: current dir).")
    pr.add_argument("double_dash", nargs="?", help="Use `--` before the command, e.g. timetrace run -- npm test")
    pr.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute (must follow --).")

    # report
    prp = sub.add_parser("report", help="Generate a report for a time window.")
    g = prp.add_mutually_exclusive_group()
    g.add_argument("--today", action="store_true", help="Report for today (local time).")
    g.add_argument("--yesterday", action="store_true", help="Report for yesterday (local time).")
    g.add_argument("--last", type=int, default=None, metavar="DAYS", help="Report for the last N days (local time).")
    prp.add_argument("--tag", default=None, help="Filter to a single tag.")
    prp.add_argument("--project", default=None, help="Filter to a single project name.")
    prp.add_argument("--category", default=None, help="Filter to a single category (testing/build/git/lint/...).")
    prp.add_argument("--limit", type=int, default=10000, help="Max runs to include.")

    # list
    pl = sub.add_parser("list", help="List recent tracked runs.")
    pl.add_argument("--limit", type=int, default=20, help="Number of runs to show.")

    # export
    pe = sub.add_parser("export", help="Export runs for a time window.")
    pe.add_argument("--format", choices=["json", "csv"], default="json", help="Export format.")
    pe.add_argument("--out", default=None, help="Output file path (default: stdout).")
    ge = pe.add_mutually_exclusive_group()
    ge.add_argument("--today", action="store_true", help="Export today (local time).")
    ge.add_argument("--yesterday", action="store_true", help="Export yesterday (local time).")
    ge.add_argument("--last", type=int, default=None, metavar="DAYS", help="Export the last N days (local time).")
    pe.add_argument("--tag", default=None, help="Filter to a single tag.")
    pe.add_argument("--project", default=None, help="Filter to a single project name.")
    pe.add_argument("--category", default=None, help="Filter to a single category.")
    pe.add_argument("--limit", type=int, default=100000, help="Max runs to include.")
    return p


def main(argv: list[str] | None = None) -> int:
    from . import __version__
    argv = argv if argv is not None else sys.argv[1:]
    p = _parser()
    args = p.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    if args.cmd is None:
        p.print_help()
        return 0

    paths = resolve_db_path(args.db)
    conn = connect(paths.db_path)
    init_db(conn)

    if args.cmd == "run":
        return _cmd_run(conn, args)
    if args.cmd == "report":
        return _cmd_report(conn, args)
    if args.cmd == "list":
        return _cmd_list(conn, args)
    if args.cmd == "export":
        return _cmd_export(conn, args)

    p.print_help()
    return 2


def _cmd_run(conn, args) -> int:
    cmd = args.command
    if not cmd:
        print("Error: No command provided. Usage: timetrace run -- <command...>", file=sys.stderr)
        return 2
    if args.double_dash != "--":
        if args.double_dash:
            cmd = [args.double_dash] + cmd
        print("Note: For best results, use `timetrace run -- <command...>`.", file=sys.stderr)

    cwd = args.cwd or os.getcwd()
    safe_cmd_str = sanitize_command(cmd)
    cat = categorize(safe_cmd_str)

    started = datetime.now(timezone.utc)
    try:
        proc = subprocess.run(cmd, cwd=cwd)
        exit_code = int(proc.returncode)
    except FileNotFoundError:
        print(f"Error: Command not found: {cmd[0]!r}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    finally:
        finished = datetime.now(timezone.utc)

    duration_s = max(0.0, (finished - started).total_seconds())

    run_id = insert_run(
        conn,
        started_at_utc=started,
        finished_at_utc=finished,
        duration_s=duration_s,
        exit_code=exit_code,
        cwd=os.path.abspath(cwd),
        command=safe_cmd_str,
        tag=args.tag,
        project=args.project,
        category=cat,
    )

    status = "✔" if exit_code == 0 else "✖"
    print(f"{status} Finished in {format_duration(duration_s)} (exit {exit_code})")
    extra = []
    if args.project:
        extra.append(f"project={args.project!r}")
    if args.tag:
        extra.append(f"tag={args.tag!r}")
    extra.append(f"category={cat}")
    print(f"Saved run #{run_id}  " + "  ".join(extra))
    return exit_code


def _window(args) -> tuple[datetime, datetime, str]:
    now_local = datetime.now().astimezone()
    if getattr(args, "today", False):
        start_local, end_local = local_day_bounds(now_local)
        title = f"TimeTrace — {start_local.strftime('%b %d, %Y')} (today)"
    elif getattr(args, "yesterday", False):
        start_local, end_local = local_day_bounds(now_local - timedelta(days=1))
        title = f"TimeTrace — {start_local.strftime('%b %d, %Y')} (yesterday)"
    elif getattr(args, "last", None) is not None:
        days = max(1, int(args.last))
        end_local = now_local
        start_local = now_local - timedelta(days=days)
        title = f"TimeTrace — last {days} days"
    else:
        start_local, end_local = local_day_bounds(now_local)
        title = f"TimeTrace — {start_local.strftime('%b %d, %Y')} (today)"
    return to_utc(start_local), to_utc(end_local), title


def _cmd_report(conn, args) -> int:
    start_utc, end_utc, title = _window(args)
    runs = fetch_runs_between(
        conn,
        start_utc=start_utc,
        end_utc=end_utc,
        limit=args.limit,
        tag=args.tag,
        project=args.project,
        category=args.category,
    )
    rep = build_report(runs, title=title)
    print(render_report_text(rep))
    return 0


def _cmd_list(conn, args) -> int:
    runs = fetch_recent_runs(conn, limit=args.limit)
    if not runs:
        print("No runs recorded yet.")
        return 0

    print(f"Recent runs (showing {len(runs)}):")
    for r in runs:
        status = "ok" if r.exit_code == 0 else f"fail({r.exit_code})"
        proj = r.project or "-"
        cat = r.category or "-"
        tag = r.tag or "-"
        when = r.started_at_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(f"  #{r.id:<5} {when}  {format_duration(r.duration_s):>8}  {status:<8}  proj={proj}  cat={cat}  tag={tag}  {r.command}")
    return 0


def _cmd_export(conn, args) -> int:
    start_utc, end_utc, _title = _window(args)
    runs = fetch_runs_between(
        conn,
        start_utc=start_utc,
        end_utc=end_utc,
        limit=args.limit,
        tag=args.tag,
        project=args.project,
        category=args.category,
    )

    rows = []
    for r in runs:
        rows.append(
            {
                "id": r.id,
                "started_at": r.started_at_utc.isoformat(),
                "finished_at": r.finished_at_utc.isoformat(),
                "duration_s": r.duration_s,
                "exit_code": r.exit_code,
                "cwd": r.cwd,
                "command": r.command,
                "tag": r.tag,
                "project": r.project,
                "category": r.category,
            }
        )

    if args.format == "json":
        payload = json.dumps(rows, indent=2)
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
            print(f"Wrote {len(rows)} rows to {args.out}")
        else:
            print(payload)
        return 0

    # csv
    if args.out:
        out_f = open(args.out, "w", newline="", encoding="utf-8")
        close = True
    else:
        out_f = sys.stdout
        close = False

    try:
        fieldnames = ["id", "started_at", "finished_at", "duration_s", "exit_code", "cwd", "command", "tag", "project", "category"]
        w = csv.DictWriter(out_f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    finally:
        if close:
            out_f.close()
            print(f"Wrote {len(rows)} rows to {args.out}")
    return 0
