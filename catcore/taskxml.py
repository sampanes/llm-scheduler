"""Turn a job dict into the claude command and the Task Scheduler XML.

All functions here are pure (they read settings/executable paths but cause no
side effects), so the action string and XML can be previewed without
registering anything.

The XML mirrors a proven daily scheduled task that fires reliably from sleep
(WakeToRun, StartWhenAvailable, battery overrides), with two changes for
interactive sessions: ExecutionTimeLimit disabled (PT0S) so Task Scheduler
never kills a long-lived terminal, and MultipleInstancesPolicy=Parallel.
"""

import shlex
from datetime import datetime, timedelta
from subprocess import list2cmdline
from xml.sax.saxutils import escape as xml_escape

from .config import DAY_NAMES
from .paths import resolve_claude, resolve_codex, resolve_terminal
from .jobmodel import describe_target


def build_claude_args(job, settings):
    """Argument vector for claude.exe (excluding the exe itself)."""
    args = ["--model", job["model"], "--permission-mode", job["permission_mode"]]
    target = job["target"]
    if target["mode"] == "resume":
        args += ["--resume", target["session_id"]]
    elif target["mode"] == "continue":
        args += ["--continue"]
    extra = (job.get("extra_args") or "").strip()
    if extra:
        # posix shlex would eat Windows backslashes; double them first so
        # quoted paths like --add-dir "C:\x y" survive the split
        args += shlex.split(extra.replace("\\", "\\\\"))
    if (job.get("prompt") or "").strip():
        args.append(job["prompt"].strip())
    return args


def _split_extra(extra):
    extra = (extra or "").strip()
    if not extra:
        return []
    # posix shlex would eat Windows backslashes; double them first so quoted
    # paths like --add-dir "C:\x y" survive the split.
    return shlex.split(extra.replace("\\", "\\\\"))


def build_codex_args(job, settings):
    """Argument vector for codex.exe (excluding the exe itself)."""
    common = []
    model = job.get("model") or "default"
    if model != "default":
        common += ["-m", model]
    approval = job.get("permission_mode") or "default"
    if approval != "default":
        common += ["-a", approval]
    common += _split_extra(job.get("extra_args"))

    target = job["target"]
    prompt = (job.get("prompt") or "").strip()
    if target["mode"] == "resume":
        args = ["resume"] + common + [target["session_id"]]
    elif target["mode"] == "continue":
        args = ["resume"] + common + ["--last"]
    else:
        args = common
    if prompt:
        args.append(prompt)
    return args


def build_tool_args(job, settings):
    if job.get("tool", "claude") == "codex":
        return build_codex_args(job, settings)
    return build_claude_args(job, settings)


def build_action(job, settings):
    """(command, argument_string, working_dir) for the task's <Exec>."""
    tool = job.get("tool", "claude")
    if tool == "codex":
        exe = resolve_codex(settings)
        if not exe:
            raise RuntimeError("codex executable not found (set codex_path in settings.json)")
    else:
        exe = resolve_claude(settings)
        if not exe:
            raise RuntimeError("claude executable not found (set claude_path in settings.json)")
    cargs = build_tool_args(job, settings)
    wd = job["dir"]
    if job["terminal"] == "wezterm":
        term = resolve_terminal(settings, "wezterm")
        if not term:
            raise RuntimeError("wezterm-gui.exe not found")
        argstr = list2cmdline(["start", "--cwd", wd, "--", exe] + cargs)
        return term, argstr, wd
    if job["terminal"] == "wt":
        term = resolve_terminal(settings, "wt")
        if not term:
            raise RuntimeError("wt.exe not found")
        argstr = list2cmdline(["-d", wd, exe] + cargs)
        return term, argstr, wd
    # console: the tool exe is the action; Task Scheduler gives it a console
    return exe, list2cmdline(cargs), wd


def build_trigger_xml(job, settings):
    sch = job["schedule"]
    if sch["type"] == "once":
        dt = datetime.fromisoformat(sch["datetime"])
        start = dt.strftime("%Y-%m-%dT%H:%M:%S")
        end = ""
        if job.get("delete_after_run", True):
            window = int(settings.get("missed_run_window_hours", 25))
            endt = dt + timedelta(hours=window)
            end = f"\n      <EndBoundary>{endt.strftime('%Y-%m-%dT%H:%M:%S')}</EndBoundary>"
        return (f"    <TimeTrigger>\n"
                f"      <StartBoundary>{start}</StartBoundary>{end}\n"
                f"      <Enabled>true</Enabled>\n"
                f"    </TimeTrigger>")
    if sch["type"] == "daily":
        hh, mm = sch["time"].split(":")
        start = datetime.now().strftime(f"%Y-%m-%dT{hh}:{mm}:00")
        return (f"    <CalendarTrigger>\n"
                f"      <StartBoundary>{start}</StartBoundary>\n"
                f"      <Enabled>true</Enabled>\n"
                f"      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n"
                f"    </CalendarTrigger>")
    if sch["type"] == "weekly":
        hh, mm = sch["time"].split(":")
        start = datetime.now().strftime(f"%Y-%m-%dT{hh}:{mm}:00")
        days = "".join(f"<{DAY_NAMES[d]} />" for d in sch["days"])
        return (f"    <CalendarTrigger>\n"
                f"      <StartBoundary>{start}</StartBoundary>\n"
                f"      <Enabled>true</Enabled>\n"
                f"      <ScheduleByWeek>\n"
                f"        <WeeksInterval>1</WeeksInterval>\n"
                f"        <DaysOfWeek>{days}</DaysOfWeek>\n"
                f"      </ScheduleByWeek>\n"
                f"    </CalendarTrigger>")
    raise ValueError(f"unknown schedule type {sch['type']!r}")


def build_task_xml(job, settings):
    cmd, argstr, wd = build_action(job, settings)
    trigger = build_trigger_xml(job, settings)
    net = "true" if job.get("require_network", True) else "false"
    delete_after = ""
    if job["schedule"]["type"] == "once" and job.get("delete_after_run", True):
        delete_after = "\n    <DeleteExpiredTaskAfter>PT1H</DeleteExpiredTaskAfter>"
    tool = job.get("tool", "claude")
    desc = (f"claude-at {tool} job '{job['name']}' — {describe_target(job)} "
            f"({job['model']}, {job['permission_mode']})")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{xml_escape(desc)}</Description>
  </RegistrationInfo>
  <Triggers>
{trigger}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>{net}</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <WakeToRun>true</WakeToRun>{delete_after}
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{xml_escape(cmd)}</Command>
      <Arguments>{xml_escape(argstr)}</Arguments>
      <WorkingDirectory>{xml_escape(wd)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""
