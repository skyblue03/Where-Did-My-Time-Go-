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
    from .config import load_config, save_config
    from .db import (
        resolve_db_path,
        connect,
        init_db,
        insert_run,
        fetch_runs_between,
        fetch_recent_runs,
        get_active_session_id,
        set_active_session_id,
        create_session,
        end_session,
        fetch_sessions,
        from_iso,
        to_iso,
    )
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

        # run (manual tracking wrapper)
        pr = sub.add_parser("run", help="Track a single command execution (manual wrapper).")
        pr.add_argument("--tag", default=None, help="Optional tag to attach to this run (e.g., 'course', 'client').")
        pr.add_argument("--project", default=None, help="Optional project name override for grouping in reports.")
        pr.add_argument("--cwd", default=None, help="Working directory to run the command in (default: current dir).")
        pr.add_argument("double_dash", nargs="?", help="Use `--` before the command, e.g. timetrace run -- npm test")
        pr.add_argument("command", nargs=argparse.REMAINDER, help="Command to execute (must follow --).")

        # record (internal; used by shell hooks)
        prec = sub.add_parser("record", help="Record a run without executing it (used by shell hooks).")
        prec.add_argument("--started", required=True, help="Start time (ISO 8601, local or UTC).")
        prec.add_argument("--finished", required=True, help="Finish time (ISO 8601, local or UTC).")
        prec.add_argument("--exit", required=True, type=int, help="Exit code.")
        prec.add_argument("--cwd", required=True, help="Working directory.")
        prec.add_argument("--command", required=True, help="Command string.")
        prec.add_argument("--tag", default=None, help="Optional tag.")
        prec.add_argument("--project", default=None, help="Optional project override.")

        # report
        prp = sub.add_parser("report", help="Generate a report for a time window.")
        g = prp.add_mutually_exclusive_group()
        g.add_argument("--today", action="store_true", help="Report for today (local time).")
        g.add_argument("--yesterday", action="store_true", help="Report for yesterday (local time).")
        g.add_argument("--last", type=int, default=None, metavar="DAYS", help="Report for the last N days (local time).")
        prp.add_argument("--tag", default=None, help="Filter to a single tag.")
        prp.add_argument("--project", default=None, help="Filter to a single project name.")
        prp.add_argument("--category", default=None, help="Filter to a single category (testing/build/git/lint/...).")
        prp.add_argument("--session", type=int, default=None, help="Filter to a session id.")
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
        pe.add_argument("--session", type=int, default=None, help="Filter to a session id.")
        pe.add_argument("--limit", type=int, default=100000, help="Max runs to include.")

        # session
        ps = sub.add_parser("session", help="Manage focused work sessions.")
        ss = ps.add_subparsers(dest="session_cmd", required=True)
        sstart = ss.add_parser("start", help="Start a session and make it active.")
        sstart.add_argument("name", help="Session name.")
        sstop = ss.add_parser("stop", help="Stop the active session.")
        sstatus = ss.add_parser("status", help="Show the active session.")
        slist = ss.add_parser("list", help="List recent sessions.")
        slist.add_argument("--limit", type=int, default=20, help="Number of sessions to show.")

        # ignore rules
        pi = sub.add_parser("ignore", help="Manage ignore rules for auto-tracking.")
        isi = pi.add_subparsers(dest="ignore_cmd", required=True)
        ilist = isi.add_parser("list", help="Show ignore rules.")
        iaddp = isi.add_parser("add-prefix", help="Add an ignored command prefix (e.g., 'cd').")
        iaddp.add_argument("prefix")
        iaddr = isi.add_parser("add-regex", help="Add an ignored regex pattern.")
        iaddr.add_argument("pattern")
        irm = isi.add_parser("remove-prefix", help="Remove an ignored prefix.")
        irm.add_argument("prefix")
        irmr = isi.add_parser("remove-regex", help="Remove an ignored regex.")
        irmr.add_argument("pattern")

        # shell init
        pin = sub.add_parser("init", help="Print shell hook code for auto-tracking.")
        pin.add_argument("shell", choices=["bash", "zsh", "powershell"], help="Shell type.")
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

        if args.cmd == "init":
            print(_init_script(args.shell))
            return 0

        # DB needed for everything else
        paths = resolve_db_path(args.db)
        conn = connect(paths.db_path)
        init_db(conn)

        if args.cmd == "run":
            return _cmd_run(conn, args, explicit_db=args.db)
        if args.cmd == "record":
            return _cmd_record(conn, args, explicit_db=args.db)
        if args.cmd == "report":
            return _cmd_report(conn, args)
        if args.cmd == "list":
            return _cmd_list(conn, args)
        if args.cmd == "export":
            return _cmd_export(conn, args)
        if args.cmd == "session":
            return _cmd_session(conn, args)
        if args.cmd == "ignore":
            return _cmd_ignore(conn, args, explicit_db=args.db)

        p.print_help()
        return 2


    def _active_session(conn) -> int | None:
        return get_active_session_id(conn)


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


    def _cmd_run(conn, args, *, explicit_db: str | None) -> int:
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

        cfg = load_config(explicit_db)
        if cfg.should_ignore(safe_cmd_str):
            # Still execute, just don't record
            try:
                proc = subprocess.run(cmd, cwd=cwd)
                return int(proc.returncode)
            except FileNotFoundError:
                print(f"Error: Command not found: {cmd[0]!r}", file=sys.stderr)
                return 127

        cat = categorize(safe_cmd_str)
        session_id = _active_session(conn)

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
            session_id=session_id,
        )

        status = "✔" if exit_code == 0 else "✖"
        print(f"{status} Finished in {format_duration(duration_s)} (exit {exit_code})")
        extra = []
        if args.project:
            extra.append(f"project={args.project!r}")
        if args.tag:
            extra.append(f"tag={args.tag!r}")
        if session_id:
            extra.append(f"session={session_id}")
        extra.append(f"category={cat}")
        print(f"Saved run #{run_id}  " + "  ".join(extra))
        return exit_code


    def _parse_dt(s: str) -> datetime:
        # allow ISO with or without tz. If no tz, interpret as local.
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.astimezone(timezone.utc)


    def _cmd_record(conn, args, *, explicit_db: str | None) -> int:
        cmd_str = sanitize_command(args.command.split())
        cfg = load_config(explicit_db)
        if cfg.should_ignore(cmd_str):
            return 0

        started = _parse_dt(args.started)
        finished = _parse_dt(args.finished)
        duration_s = max(0.0, (finished - started).total_seconds())
        cat = categorize(cmd_str)
        session_id = _active_session(conn)

        run_id = insert_run(
            conn,
            started_at_utc=started,
            finished_at_utc=finished,
            duration_s=duration_s,
            exit_code=int(args.exit),
            cwd=os.path.abspath(args.cwd),
            command=cmd_str,
            tag=args.tag,
            project=args.project,
            category=cat,
            session_id=session_id,
        )
        return 0


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
            session_id=args.session,
        )
        # If session filter is active, reflect it in title.
        if args.session is not None:
            title = f"{title}  (session {args.session})"
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
            sess = r.session_name or ("-" if not r.session_id else f"#{r.session_id}")
            when = r.started_at_utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            print(f"  #{r.id:<5} {when}  {format_duration(r.duration_s):>8}  {status:<9}  proj={proj}  cat={cat}  tag={tag}  sess={sess}  {r.command}")
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
            session_id=args.session,
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
                    "session_id": r.session_id,
                    "session_name": r.session_name,
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
            fieldnames = [
                "id",
                "started_at",
                "finished_at",
                "duration_s",
                "exit_code",
                "cwd",
                "command",
                "tag",
                "project",
                "category",
                "session_id",
                "session_name",
            ]
            w = csv.DictWriter(out_f, fieldnames=fieldnames)
            w.writeheader()
            for row in rows:
                w.writerow(row)
        finally:
            if close:
                out_f.close()
                print(f"Wrote {len(rows)} rows to {args.out}")
        return 0


    def _cmd_session(conn, args) -> int:
        sc = args.session_cmd
        if sc == "start":
            active = get_active_session_id(conn)
            if active is not None:
                print(f"A session is already active (id={active}). Stop it first.")
                return 2
            sid = create_session(conn, name=args.name, started_at_utc=datetime.now(timezone.utc))
            set_active_session_id(conn, sid)
            print(f"Started session #{sid}: {args.name}")
            return 0

        if sc == "stop":
            active = get_active_session_id(conn)
            if active is None:
                print("No active session.")
                return 0
            end_session(conn, session_id=active, ended_at_utc=datetime.now(timezone.utc))
            set_active_session_id(conn, None)
            print(f"Stopped session #{active}.")
            return 0

        if sc == "status":
            active = get_active_session_id(conn)
            if active is None:
                print("No active session.")
                return 0
            # best-effort: show name
            sessions = fetch_sessions(conn, limit=50)
            name = None
            for s in sessions:
                if s["id"] == active:
                    name = s["name"]
                    break
            if name:
                print(f"Active session: #{active} — {name}")
            else:
                print(f"Active session: #{active}")
            return 0

        if sc == "list":
            sessions = fetch_sessions(conn, limit=args.limit)
            if not sessions:
                print("No sessions yet.")
                return 0
            print(f"Sessions (showing {len(sessions)}):")
            for s in sessions:
                ended = s["ended_at_utc"] or "-"
                print(f"  #{s['id']:<5} {s['name']:<24} started={s['started_at_utc']}  ended={ended}")
            return 0

        return 2


    def _cmd_ignore(conn, args, *, explicit_db: str | None) -> int:
        cfg = load_config(explicit_db)
        ic = args.ignore_cmd

        if ic == "list":
            print("Ignore prefixes:")
            for pfx in cfg.ignore_prefixes:
                print(f"  - {pfx}")
            print("Ignore regex:")
            for pat in cfg.ignore_regex:
                print(f"  - {pat}")
            return 0

        if ic == "add-prefix":
            pfx = str(args.prefix).strip()
            if pfx and pfx not in cfg.ignore_prefixes:
                cfg.ignore_prefixes.append(pfx)
                save_config(cfg, explicit_db)
                print(f"Added ignore prefix: {pfx}")
            return 0

        if ic == "add-regex":
            pat = str(args.pattern)
            if pat and pat not in cfg.ignore_regex:
                cfg.ignore_regex.append(pat)
                save_config(cfg, explicit_db)
                print(f"Added ignore regex: {pat}")
            return 0

        if ic == "remove-prefix":
            pfx = str(args.prefix).strip()
            cfg.ignore_prefixes = [x for x in cfg.ignore_prefixes if x != pfx]
            save_config(cfg, explicit_db)
            print(f"Removed ignore prefix: {pfx}")
            return 0

        if ic == "remove-regex":
            pat = str(args.pattern)
            cfg.ignore_regex = [x for x in cfg.ignore_regex if x != pat]
            save_config(cfg, explicit_db)
            print(f"Removed ignore regex: {pat}")
            return 0

        return 2


    def _init_script(shell: str) -> str:
        # These hooks call `timetrace record ...` after each command.
        # They are designed to be copy-pasteable and explicit.
        if shell in ("bash", "zsh"):
            return r'''# timetrace auto-tracking for bash/zsh
# Usage:
#   eval "$(timetrace init bash)"   # or zsh
#
# Notes:
# - Uses DEBUG trap to capture the command and start time
# - Uses PROMPT_COMMAND (bash) / precmd (zsh) to record after command completes
#
# Disable:
#   unset TIMETRACE__LAST_CMD TIMETRACE__START_NS TIMETRACE__LAST_RECORDED_ID
#   trap - DEBUG

timetrace__now_ns() {
  python - <<'PY'
import time
print(time.time_ns())
PY
}

timetrace__iso_now() {
  python - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat())
PY
}

timetrace__record() {
  local exit_code="$1"
  local cmd="$2"
  local start_iso="$3"
  local end_iso="$4"
  local cwd="$5"
  # project override optional: set TIMETRACE_PROJECT
  local project="${TIMETRACE_PROJECT:-}"
  local tag="${TIMETRACE_TAG:-}"
  timetrace record --started "$start_iso" --finished "$end_iso" --exit "$exit_code" --cwd "$cwd" --command "$cmd" ${tag:+--tag "$tag"} ${project:+--project "$project"} >/dev/null 2>&1 || true
}

timetrace__preexec() {
  # Capture start time + raw command before execution
  TIMETRACE__LAST_CMD="$BASH_COMMAND"
  TIMETRACE__START_ISO="$(timetrace__iso_now)"
}

# DEBUG trap fires before each simple command; keep it lightweight
trap 'timetrace__preexec' DEBUG

timetrace__postcmd() {
  local exit_code="$?"
  # Record only for the top-level command line
  if [ -n "${TIMETRACE__LAST_CMD:-}" ] && [ -n "${TIMETRACE__START_ISO:-}" ]; then
    local end_iso
    end_iso="$(timetrace__iso_now)"
    timetrace__record "$exit_code" "$TIMETRACE__LAST_CMD" "$TIMETRACE__START_ISO" "$end_iso" "$PWD"
  fi
}

# bash: PROMPT_COMMAND
if [ -n "${BASH_VERSION:-}" ]; then
  PROMPT_COMMAND="timetrace__postcmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
fi

# zsh: precmd
if [ -n "${ZSH_VERSION:-}" ]; then
  autoload -Uz add-zsh-hook
  add-zsh-hook precmd timetrace__postcmd
fi
'''
        if shell == "powershell":
            # PowerShell uses Get-History which contains timing metadata
            return r'''# timetrace auto-tracking for PowerShell
# Usage:
#   Invoke-Expression (& timetrace init powershell)
#
# Notes:
# - Uses Get-History to capture last command + start/end execution time
# - Records on each prompt render (cheap + reliable)
# - Configure optional grouping:
#     $env:TIMETRACE_PROJECT="Handzplay"
#     $env:TIMETRACE_TAG="course"
#
$global:TimetraceLastHistoryId = -1

function global:prompt {
    try {
        $h = Get-History -Count 1
        if ($null -ne $h -and $h.Id -ne $global:TimetraceLastHistoryId) {
            $global:TimetraceLastHistoryId = $h.Id
            $cmd = $h.CommandLine
            $started = $h.StartExecutionTime.ToUniversalTime().ToString("o")
            $finished = $h.EndExecutionTime.ToUniversalTime().ToString("o")
            $exitCode = $LASTEXITCODE
            $cwd = (Get-Location).Path
            $project = $env:TIMETRACE_PROJECT
            $tag = $env:TIMETRACE_TAG
            $args = @("record","--started",$started,"--finished",$finished,"--exit",$exitCode,"--cwd",$cwd,"--command",$cmd)
            if ($tag) { $args += @("--tag",$tag) }
            if ($project) { $args += @("--project",$project) }
            & timetrace @args | Out-Null
        }
    } catch {
        # ignore hook failures
    }
    "PS " + $(Get-Location) + "> "
}
'''
        return ""


    if __name__ == "__main__":
        raise SystemExit(main())
