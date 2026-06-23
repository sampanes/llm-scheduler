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
