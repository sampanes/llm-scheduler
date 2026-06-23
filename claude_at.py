#!/usr/bin/env python3
"""claude-at — schedule Claude Code session resumes on Windows.

GUI + CLI to schedule "open claude at <time>, in <dir>, resuming <session>,
with <model> and <permission mode>" as Windows scheduled tasks. This file is
the thin CLI / entrypoint; all backend logic lives in the catcore package and
the GUIs live in webgui.py (default) and tkgui.py (legacy fallback).

Security design (managed Windows endpoint with Defender + AMSI):
  * Tasks are registered ONLY via native schtasks.exe (/Create /XML). No
    PowerShell is used anywhere, at schedule time or fire time.
  * The scheduled action runs signed binaries directly with all arguments
    baked in: wezterm-gui.exe -> claude.exe (or claude.exe alone for
    terminal=console). No script host, no cmd.exe, no Python in the
    fire-time chain.
  * Task settings (WakeToRun, StartWhenAvailable, battery overrides) are
    copied from a proven daily scheduled task that fires reliably from sleep.

Usage:
  claude_at.py                      # GUI (default)
  claude_at.py gui [--tk]
  claude_at.py sessions [--dir D] [--days N] [--all]
  claude_at.py add --dir D (--resume ID | --continue | --new)
                  (--at "YYYY-MM-DD HH:MM" | --daily --time HH:MM
                   | --weekly MON,WED --time HH:MM)
                  [--model opus] [--mode auto] [--effort high] [--terminal wezterm]
                  [--prompt TEXT] [--extra ARGS] [--name NAME]
                  [--keep] [--no-network-req] [--dry-run]
  claude_at.py list                 # pending runs, soonest first
  claude_at.py run  NAME_OR_ID      # fire a job now (schtasks /Run)
  claude_at.py rm   NAME_OR_ID      # delete job + its task
  claude_at.py xml  NAME_OR_ID      # print the task XML (debug)
  claude_at.py prune                # drop jobs whose task is gone/expired
  claude_at.py doctor               # check resolved paths
"""

import argparse
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from catcore import (
    TOOL_DIR, PROJECTS_DIR, JOBS_FILE, PERMISSION_MODES, CODEX_APPROVAL_MODES,
    TERMINALS, EFFORT_LEVELS, DAY_NAMES, TOOLS,
    load_settings, load_jobs, scan_sessions, make_job, find_job, next_fire,
    job_status, describe_target, describe_schedule, task_name_for,
    resolve_claude, resolve_codex,
    resolve_terminal, default_terminal, build_action, build_task_xml,
    register_job, delete_job, task_run, task_query_all, prune_jobs,
)


# --------------------------------------------------------------------------
# CLI commands
# --------------------------------------------------------------------------

def cmd_sessions(args, settings):
    days = 0 if args.all else (args.days if args.days is not None
                               else settings["sessions_days"])
    rows = scan_sessions(days=days, dir_filter=args.dir, search=args.search,
                         tool=args.tool)
    if not rows:
        print("no sessions found")
        return
    for s in rows:
        mark = "*" if s["active"] else " "
        print(f"{mark} {s['mtime']:%Y-%m-%d %H:%M}  {s['id']}  {s['dir']}")
        if s["title"]:
            print(f"      {s['title'][:100]}")


def parse_schedule_args(args):
    if args.at:
        dt = datetime.fromisoformat(args.at.replace(" ", "T", 1))
        return {"type": "once", "datetime": dt.isoformat(timespec="minutes")}
    if args.daily:
        if not args.time:
            raise SystemExit("--daily requires --time HH:MM")
        return {"type": "daily", "time": args.time}
    if args.weekly:
        if not args.time:
            raise SystemExit("--weekly requires --time HH:MM")
        days = [d.strip().upper()[:3] for d in args.weekly.split(",")]
        bad = [d for d in days if d not in DAY_NAMES]
        if bad:
            raise SystemExit(f"unknown day(s): {bad} (use MON..SUN)")
        return {"type": "weekly", "days": days, "time": args.time}
    raise SystemExit("need one of --at / --daily / --weekly")


def cmd_add(args, settings):
    tool = args.tool or settings.get("tool", "claude")
    if tool == "claude" and args.mode and args.mode not in PERMISSION_MODES:
        raise SystemExit(f"unknown Claude permission mode: {args.mode}")
    if tool == "codex" and args.mode and args.mode not in CODEX_APPROVAL_MODES:
        raise SystemExit(f"unknown Codex approval mode: {args.mode}")
    if args.resume:
        mode, sid = "resume", args.resume
    elif args.new:
        mode, sid = "new", ""
    else:
        mode, sid = "continue", ""
    schedule = parse_schedule_args(args)
    name = args.name or f"{mode}-{Path(args.dir).name}"
    job = make_job(
        name, args.dir, mode, sid, schedule,
        args.model or (settings["model"] if tool == "claude"
                       else settings.get("codex_model", "default")),
        args.mode or (settings["permission_mode"] if tool == "claude"
                      else settings.get("codex_approval_mode", "default")),
        args.terminal or settings["terminal"] or default_terminal(settings),
        args.prompt, args.extra,
        not args.no_network_req if args.no_network_req is not None
        else settings["require_network"],
        not args.keep,
        tool=tool,
        # effort is claude-only (--effort); codex jobs store "" and never emit it
        effort=(args.effort or settings.get("effort", "")) if tool == "claude" else "",
    )
    if schedule["type"] == "once" and next_fire(job) is None:
        raise SystemExit("scheduled time is in the past")
    xml = register_job(job, settings, dry_run=args.dry_run)
    if args.dry_run:
        cmd, argstr, wd = build_action(job, settings)
        print("--- DRY RUN (nothing registered) ---")
        print(f"task name : {task_name_for(job)}")
        print(f"action    : {cmd}")
        print(f"args      : {argstr}")
        print(f"workdir   : {wd}")
        print(f"next fire : {next_fire(job)}")
        print("--- task XML ---")
        print(xml)
    else:
        print(f"scheduled: {job['name']}-{job['id']}  "
              f"{describe_schedule(job)}  -> {describe_target(job)}")


def cmd_list(args, settings):
    jobs = load_jobs()
    if not jobs:
        print("no jobs")
        return
    rows = sorted(jobs, key=lambda j: (next_fire(j) or datetime.max))
    qall = task_query_all()
    for j in rows:
        nf = next_fire(j)
        q = qall.get(j.get("task_name", task_name_for(j)))
        status = job_status(j, q)
        when = f"{nf:%Y-%m-%d %H:%M}" if nf else "expired"
        print(f"{when}  [{status:<8}] {j['name']}-{j['id']}  "
              f"{describe_schedule(j)}  {describe_target(j)}  "
              f"({j.get('tool', 'claude')}, {j['model']}, "
              f"{j['permission_mode']}, {j['terminal']})")


def cmd_rm(args, settings):
    job = find_job(load_jobs(), args.key)
    if not job:
        raise SystemExit(f"no job matching {args.key!r}")
    delete_job(job)
    print(f"deleted {job['name']}-{job['id']}")


def cmd_run(args, settings):
    job = find_job(load_jobs(), args.key)
    if not job:
        raise SystemExit(f"no job matching {args.key!r}")
    task_run(job.get("task_name", task_name_for(job)))
    print(f"fired {job['name']}-{job['id']}")


def cmd_xml(args, settings):
    job = find_job(load_jobs(), args.key)
    if not job:
        raise SystemExit(f"no job matching {args.key!r}")
    print(build_task_xml(job, settings))


def cmd_doctor(args, settings):
    print(f"tool dir      : {TOOL_DIR}")
    print(f"claude        : {resolve_claude(settings)}")
    print(f"codex         : {resolve_codex(settings)}")
    print(f"wezterm-gui   : {resolve_terminal(settings, 'wezterm')}")
    print(f"wt            : {resolve_terminal(settings, 'wt')}")
    print(f"default term  : {settings['terminal'] or default_terminal(settings) + ' (auto)'}")
    print(f"schtasks      : {shutil.which('schtasks')}")
    print(f"projects dir  : {PROJECTS_DIR} "
          f"({'ok' if PROJECTS_DIR.exists() else 'MISSING'})")
    print(f"jobs file     : {JOBS_FILE} ({len(load_jobs())} jobs)")
    print(f"settings      : {json.dumps(load_settings(), indent=2)}")


def cmd_gui(args, settings):
    """Launch the GUI. Default is the pywebview web GUI; --tk forces the
    legacy tkinter GUI (and is the automatic fallback if pywebview is absent)."""
    use_tk = getattr(args, "tk", False)
    smoke = getattr(args, "smoke", False)
    if not use_tk:
        try:
            import webgui
            webgui.run_gui(settings, smoke=smoke)
            return
        except ImportError:
            pass  # pywebview/webgui not available yet -> fall back to tkinter
    import tkgui
    tkgui.run_gui(settings, smoke=smoke)


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog="claude-at", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    g = sub.add_parser("gui", help="open the GUI (default)")
    g.add_argument("--tk", action="store_true",
                   help="use the legacy tkinter GUI instead of the web GUI")
    g.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)

    s = sub.add_parser("sessions", help="list known sessions")
    s.add_argument("--tool", choices=TOOLS, default="claude")
    s.add_argument("--dir"); s.add_argument("--days", type=int)
    s.add_argument("--all", action="store_true")
    s.add_argument("--search")

    a = sub.add_parser("add", help="schedule a run")
    a.add_argument("--tool", choices=TOOLS, default="claude")
    a.add_argument("--dir", required=True)
    tgt = a.add_mutually_exclusive_group()
    tgt.add_argument("--resume", metavar="SESSION_ID")
    tgt.add_argument("--continue", dest="cont", action="store_true")
    tgt.add_argument("--new", action="store_true")
    a.add_argument("--at", help='once: "YYYY-MM-DD HH:MM"')
    a.add_argument("--daily", action="store_true")
    a.add_argument("--weekly", metavar="MON,WED")
    a.add_argument("--time", metavar="HH:MM")
    a.add_argument("--model"); a.add_argument("--mode")
    a.add_argument("--effort", choices=EFFORT_LEVELS)
    a.add_argument("--terminal", choices=TERMINALS)
    a.add_argument("--prompt"); a.add_argument("--extra")
    a.add_argument("--name")
    a.add_argument("--keep", action="store_true",
                   help="keep one-shot task after it runs")
    a.add_argument("--no-network-req", action="store_true", default=None)
    a.add_argument("--dry-run", action="store_true")

    sub.add_parser("list", help="pending runs, soonest first")
    r = sub.add_parser("rm", help="delete a job"); r.add_argument("key")
    rn = sub.add_parser("run", help="fire a job now"); rn.add_argument("key")
    x = sub.add_parser("xml", help="print task XML"); x.add_argument("key")
    sub.add_parser("prune", help="drop completed/missing jobs")
    sub.add_parser("doctor", help="check resolved paths")

    args = p.parse_args(argv)
    settings = load_settings()

    # gui (the default when no subcommand is given) and prune don't take the
    # (args, settings) shape the rest share. cmd_gui reads its flags defensively
    # via getattr, so it's safe even on the bare top-level namespace.
    if args.cmd in (None, "gui"):
        cmd_gui(args, settings)
    elif args.cmd == "prune":
        prune_jobs()
    else:
        {
            "sessions": cmd_sessions, "add": cmd_add, "list": cmd_list,
            "rm": cmd_rm, "run": cmd_run, "xml": cmd_xml, "doctor": cmd_doctor,
        }[args.cmd](args, settings)


if __name__ == "__main__":
    main()
