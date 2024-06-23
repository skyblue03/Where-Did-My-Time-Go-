from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from .db import resolve_db_path, connect, init_db, insert_run, fetch_runs_between
from .report import build_report, render_report_text, local_day_bounds, to_utc
from .utils import sanitize_command


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="timetrace",
        description="Track command durations and generate local-first time reports.",
    )
    p.add_argument(
        "--db",
        default=None,
        help="Path to the SQLite DB file (default: OS data directory).",
    )
    p.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit.",
    )

    sub = p.add_subparsers(dest="cmd", required=False)

    # run
    pr = sub.add_parser("run", help="Track a single command execution.")
    pr.add_argument("--tag", default=None, help="Optional tag to attach to this run (e.g., 'course', 'client').")
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
    prp.add_argument("--limit", type=int, default=10000, help="Max runs to include.")
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

    p.print_help()
    return 2


def _cmd_run(conn, args) -> int:
    # Expect `--` between timetrace args and command args
    cmd = args.command
    if not cmd:
        print("Error: No command provided. Usage: timetrace run -- <command...>", file=sys.stderr)
        return 2
    if args.double_dash != "--":
        # allow omission if user did `timetrace run npm test` by mistake, but warn
        # argparse puts first token into double_dash
        if args.double_dash:
            cmd = [args.double_dash] + cmd
        print("Note: For best results, use `timetrace run -- <command...>`.", file=sys.stderr)

    cwd = args.cwd or os.getcwd()
    safe_cmd_str = sanitize_command(cmd)

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
    )

    status = "✔" if exit_code == 0 else "✖"
    print(f"{status} Finished in {duration_s:.2f}s (exit {exit_code})")
    if args.tag:
        print(f"Saved run #{run_id}  tag={args.tag!r}")
    else:
        print(f"Saved run #{run_id}")
    return exit_code


def _cmd_report(conn, args) -> int:
    now_local = datetime.now().astimezone()
    if args.today:
        start_local, end_local = local_day_bounds(now_local)
        title = f"TimeTrace — {start_local.strftime('%b %d, %Y')} (today)"
    elif args.yesterday:
        start_local, end_local = local_day_bounds(now_local - timedelta(days=1))
        title = f"TimeTrace — {start_local.strftime('%b %d, %Y')} (yesterday)"
    elif args.last is not None:
        days = max(1, int(args.last))
        end_local = now_local
        start_local = now_local - timedelta(days=days)
        title = f"TimeTrace — last {days} days"
    else:
        # default: today
        start_local, end_local = local_day_bounds(now_local)
        title = f"TimeTrace — {start_local.strftime('%b %d, %Y')} (today)"

    start_utc = to_utc(start_local)
    end_utc = to_utc(end_local)

    runs = fetch_runs_between(conn, start_utc=start_utc, end_utc=end_utc, limit=args.limit, tag=args.tag)
    rep = build_report(runs, title=title)
    print(render_report_text(rep))
    return 0
