"""schtasks.exe wrappers + job registration / deletion / pruning.

Security design (managed Windows endpoint with Defender + AMSI):
tasks are registered ONLY via native schtasks.exe (/Create /XML). No PowerShell
is used anywhere, at schedule time or fire time. The scheduled action runs
signed binaries directly (wezterm-gui.exe -> claude.exe, or claude.exe alone),
so there is no script host in the fire-time chain for AMSI to scan.

This module is the only one with side effects (it shells out and writes
jobs.json); everything it needs to build is delegated to taskxml.
"""

import os
import subprocess
import tempfile
from datetime import datetime

from .config import TASK_FOLDER, load_jobs, save_jobs
from .jobmodel import (
    task_name_for, describe_schedule, next_fire, once_run_info,
)
from .taskxml import build_task_xml


def schtasks(*args):
    return subprocess.run(["schtasks"] + list(args),
                          capture_output=True, text=True)


def task_create(task_name, xml_text):
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                     encoding="utf-16") as f:
        f.write(xml_text)
        tmp = f.name
    try:
        r = schtasks("/Create", "/TN", task_name, "/XML", tmp, "/F")
        if r.returncode != 0:
            raise RuntimeError(f"schtasks /Create failed: {r.stderr.strip() or r.stdout.strip()}")
    finally:
        os.unlink(tmp)


def task_delete(task_name):
    return schtasks("/Delete", "/TN", task_name, "/F")


def task_run(task_name):
    r = schtasks("/Run", "/TN", task_name)
    if r.returncode != 0:
        raise RuntimeError(f"schtasks /Run failed: {r.stderr.strip() or r.stdout.strip()}")


def task_query_all():
    """One schtasks call -> {task_name: {status, next_run, last_run, last_result}}
    for \\ClaudeAt\\*.

    Uses VERBOSE CSV so a fired one-shot (real Last Run Time + Last Result) is
    distinguishable from one merely sitting 'Ready' — see jobmodel.once_run_info.
    Verbose column order is stable (0 HostName, 1 TaskName, 2 Next Run Time,
    3 Status, 4 Logon Mode, 5 Last Run Time, 6 Last Result), but rather than
    hardcode index 1 we locate TaskName by its \\ClaudeAt\\ prefix and read the
    rest by fixed offset, so a leading-column shift can't silently misalign us.
    A short/garbled row is skipped (the job then reads as 'not fired' and falls
    back to the clock-based behaviour — never a false 'finished')."""
    r = schtasks("/Query", "/FO", "CSV", "/NH", "/V")
    out = {}
    if r.returncode != 0:
        return out
    import csv as _csv
    import io as _io
    for row in _csv.reader(_io.StringIO(r.stdout)):
        idx = next((i for i, c in enumerate(row)
                    if c.startswith(f"\\{TASK_FOLDER}\\")), None)
        if idx is None or len(row) < idx + 6:
            continue
        out[row[idx].lstrip("\\")] = {
            "next_run": row[idx + 1], "status": row[idx + 2],
            "last_run": row[idx + 4], "last_result": row[idx + 5],
        }
    return out


def task_query(task_name):
    """Return dict of interesting fields, or None if the task is gone."""
    r = schtasks("/Query", "/TN", task_name, "/V", "/FO", "LIST")
    if r.returncode != 0:
        return None
    info = {}
    for line in r.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return {
        "status": info.get("Status", "?"),
        "last_run": info.get("Last Run Time", ""),
        "last_result": info.get("Last Result", ""),
        "next_run": info.get("Next Run Time", ""),
    }


def register_job(job, settings, dry_run=False):
    xml = build_task_xml(job, settings)
    tn = task_name_for(job)
    job["task_name"] = tn
    if dry_run:
        return xml
    task_create(tn, xml)
    jobs = load_jobs()
    jobs.append(job)
    save_jobs(jobs)
    return xml


def delete_job(job):
    task_delete(job.get("task_name", task_name_for(job)))
    jobs = [j for j in load_jobs() if j["id"] != job["id"]]
    save_jobs(jobs)


def prune_jobs(verbose=True):
    """Drop jobs that are finished, and clean up any task we're forgetting.

    A job is finished when ANY of:
      * its Task Scheduler task is gone (q is None) — manual delete / auto-clean;
      * it's a one-shot that has already FIRED (Last Run Time at/after its
        scheduled time) and is NOT a keep-the-task job — the authoritative
        signal, independent of the catch-up window or whether Windows has gotten
        around to auto-deleting the expired task;
      * it's a one-shot now past its catch-up window that never ran (next_fire
        is None) — abandoned.
    Fired keep-jobs (delete_after_run=False) are RETAINED so their record/label
    ('Ran ✓ (kept)') stays for inspection. Whenever we drop a record whose
    Windows task still lingers, we delete that task too, so a forgotten job never
    leaves an orphan task behind."""
    jobs = load_jobs()
    keep, dropped, orphans = [], [], []
    qall = task_query_all()
    for j in jobs:
        tn = j.get("task_name", task_name_for(j))
        q = qall.get(tn)
        once = j["schedule"]["type"] == "once"
        fired = once and once_run_info(j, q) is not None
        overdue = once and next_fire(j) is None
        finished = (q is None) or overdue or (
            fired and j.get("delete_after_run", True))
        if finished:
            dropped.append(j)
            if q is not None:           # task still around -> clean the orphan
                orphans.append(tn)
        else:
            keep.append(j)
    if dropped:
        save_jobs(keep)
    for tn in orphans:
        task_delete(tn)                 # best-effort; ignore "already gone"
    if verbose:
        for j in dropped:
            print(f"pruned: {j['name']}-{j['id']} ({describe_schedule(j)})")
        if not dropped:
            print("nothing to prune")
    return dropped
