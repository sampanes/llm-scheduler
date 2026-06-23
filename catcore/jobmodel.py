"""Pure job-dict helpers: identity, human descriptions, scheduling math,
construction, and lookup. No side effects — nothing here touches schtasks or
disk, which keeps it trivially testable.
"""

import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .config import TASK_FOLDER, DAY_NAMES, DAY_ORDER, DEFAULT_SETTINGS


def sanitize_name(name):
    name = re.sub(r"[^A-Za-z0-9 _.-]", "", name).strip().replace(" ", "-")
    return name[:60] or "job"


def task_name_for(job):
    return f"{TASK_FOLDER}\\{job['name']}-{job['id']}"


def describe_target(job):
    t = job["target"]
    d = job["dir"]
    if t["mode"] == "resume":
        return f"resume {t['session_id'][:8]}… in {d}"
    if t["mode"] == "continue":
        return f"continue latest in {d}"
    return f"new session in {d}"


def describe_schedule(job):
    s = job["schedule"]
    if s["type"] == "once":
        return f"once @ {s['datetime'].replace('T', ' ')}"
    if s["type"] == "daily":
        return f"daily @ {s['time']}"
    return f"weekly {','.join(s['days'])} @ {s['time']}"


def next_fire(job, now=None):
    """Compute next fire locally (locale-proof). None = no future fire."""
    now = now or datetime.now()
    s = job["schedule"]
    if s["type"] == "once":
        dt = datetime.fromisoformat(s["datetime"])
        if dt > now:
            return dt
        window = timedelta(hours=DEFAULT_SETTINGS["missed_run_window_hours"])
        if not job.get("delete_after_run", True) or now - dt < window:
            return dt  # overdue but may still catch up via StartWhenAvailable
        return None
    hh, mm = (int(x) for x in s["time"].split(":"))
    if s["type"] == "daily":
        cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return cand if cand > now else cand + timedelta(days=1)
    if s["type"] == "weekly":
        wanted = {DAY_ORDER.index(d) for d in s["days"]}
        for off in range(8):
            cand = (now + timedelta(days=off)).replace(
                hour=hh, minute=mm, second=0, microsecond=0)
            if cand.weekday() in wanted and cand > now:
                return cand
    return None


def make_job(name, dirpath, target_mode, session_id, schedule, model,
             permission_mode, terminal, prompt, extra_args,
             require_network, delete_after_run, tool="claude", effort=""):
    return {
        "id": uuid.uuid4().hex[:8],
        "name": sanitize_name(name),
        "tool": tool or "claude",
        "dir": str(Path(dirpath)),
        "target": {"mode": target_mode, "session_id": session_id or ""},
        "schedule": schedule,
        "model": model,
        "permission_mode": permission_mode,
        "effort": effort or "",
        "terminal": terminal,
        "prompt": prompt or "",
        "extra_args": extra_args or "",
        "require_network": bool(require_network),
        "delete_after_run": bool(delete_after_run),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def find_job(jobs, key):
    for j in jobs:
        if j["id"] == key or j["name"] == key or task_name_for(j) == key:
            return j
    return None


# --------------------------------------------------------------------------
# Task Scheduler run-state — authoritative for whether a one-shot has fired.
#
# next_fire() above is pure clock math: it cannot know whether a one-shot has
# already run, so a successfully-fired job stays "pending <past time>" for the
# whole missed-run catch-up window and resists pruning. Task Scheduler DOES know
# (Last Run Time / Last Result), so completion is read from there instead of
# re-derived from the clock. These helpers are still pure — they take an
# already-fetched task_query_all() entry; the schtasks call lives in scheduler.
# --------------------------------------------------------------------------

_NEVER_RUN = ("", "n/a", "never")  # schtasks Last Run Time sentinels


def _parse_task_dt(s):
    """Parse a schtasks Last/Next Run Time string to a datetime, else None.

    schtasks prints locale-formatted timestamps ('6/22/2026 20:01:00' or
    '6/22/2026 8:01:00 PM'); the never-run sentinel is 'N/A' or the historic
    '11/30/1999 12:00:00 AM'. Unparseable or pre-2000 -> None (treated as
    'never ran'), so we never mistake the sentinel for a real firing."""
    s = (s or "").strip()
    if s.lower() in _NEVER_RUN:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S",
                "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.year >= 2000 else None
        except ValueError:
            continue
    return None


def once_run_info(job, q):
    """Task Scheduler's run-state for a one-shot, or None if it hasn't run / is
    not a one-shot / has no task info.

    `q` is a task_query_all() entry ({status, next_run, last_run, last_result}).
    Returns {"last_run": datetime, "last_result": str, "ok": bool} when the task
    has a Last Run Time at/after the scheduled time (i.e. THIS one-shot fired);
    `ok` is True only on a zero (success) result. A run stamped before the
    schedule belongs to an earlier task and is ignored."""
    if not q or job.get("schedule", {}).get("type") != "once":
        return None
    lr = _parse_task_dt(q.get("last_run", ""))
    if lr is None:
        return None
    try:
        sched = datetime.fromisoformat(job["schedule"]["datetime"])
    except (KeyError, ValueError):
        return None
    if lr + timedelta(minutes=1) < sched:
        return None
    res = str(q.get("last_result", "")).strip()
    return {"last_run": lr, "last_result": res, "ok": res in ("0", "0x0")}


def job_status(job, q):
    """Display label for a job's task status, authoritative for fired one-shots.

    A one-shot Task Scheduler reports as run shows 'Ran' (or 'Ran (kept)' when
    delete_after_run is off, or 'Failed (N)' on a non-zero result) rather than
    the misleading raw 'Ready' beside a past 'next run'. Anything else passes the
    raw schtasks status through (Ready/Running/…); a gone task is 'MISSING'.

    Kept ASCII-only on purpose: this label is printed by the CLI to a Windows
    console (cp1252), where a non-ASCII glyph would raise UnicodeEncodeError."""
    if q is None:
        return "MISSING"
    info = once_run_info(job, q)
    if info:
        if not info["ok"]:
            return f"Failed ({info['last_result'] or '?'})"
        return "Ran (kept)" if not job.get("delete_after_run", True) else "Ran"
    return q.get("status", "?")
