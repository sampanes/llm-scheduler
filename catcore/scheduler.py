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
from .jobmodel import task_name_for, describe_schedule, next_fire
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
    """One schtasks call -> {task_name: {status, next_run}} for \\ClaudeAt\\*."""
    r = schtasks("/Query", "/FO", "CSV", "/NH")
    out = {}
    if r.returncode != 0:
        return out
    import csv as _csv
    import io as _io
    for row in _csv.reader(_io.StringIO(r.stdout)):
        if len(row) >= 3 and row[0].startswith(f"\\{TASK_FOLDER}\\"):
            out[row[0].lstrip("\\")] = {"next_run": row[1], "status": row[2]}
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
    """Drop jobs whose task no longer exists (fired one-shots, manual deletes)."""
    jobs = load_jobs()
    keep, dropped = [], []
    qall = task_query_all()
    for j in jobs:
        q = qall.get(j.get("task_name", task_name_for(j)))
        overdue = (j["schedule"]["type"] == "once"
                   and next_fire(j) is None)
        if q is None or overdue:
            dropped.append(j)
        else:
            keep.append(j)
    if dropped:
        save_jobs(keep)
    if verbose:
        for j in dropped:
            print(f"pruned: {j['name']}-{j['id']} ({describe_schedule(j)})")
        if not dropped:
            print("nothing to prune")
    return dropped
