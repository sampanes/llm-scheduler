"""catcore — the GUI-free backend for claude-at.

Session discovery, the job model, Task Scheduler XML generation, and the
schtasks.exe wrappers. Both the CLI (claude_at.py) and the GUI build on this;
nothing here imports tkinter, pywebview, or any UI toolkit.

Layering (acyclic):
    config                      constants + settings/jobs persistence
    paths      -> config        executable resolution
    sessions   -> config        session discovery
    jobmodel   -> config        pure job-dict helpers
    taskxml    -> paths,jobmodel command + Task Scheduler XML (pure)
    scheduler  -> taskxml,...    schtasks side effects + job CRUD
    window     -> (stdlib only)  5h usage-window lookup + local slot grid
"""

from .config import (
    TOOL_DIR, JOBS_FILE, SETTINGS_FILE, PROJECTS_DIR, TASK_FOLDER,
    UUID_RE, DAY_NAMES, DAY_ORDER, PERMISSION_MODES, MODELS, TERMINALS,
    DEFAULT_SETTINGS, load_settings, save_settings, load_jobs, save_jobs,
)
from .paths import resolve_claude, resolve_terminal, default_terminal
from .sessions import scan_sessions, open_session_ids
from .jobmodel import (
    sanitize_name, task_name_for, describe_target, describe_schedule,
    next_fire, make_job, find_job,
)
from .taskxml import (
    build_claude_args, build_action, build_trigger_xml, build_task_xml,
)
from .scheduler import (
    schtasks, task_create, task_delete, task_run, task_query_all, task_query,
    register_job, delete_job, prune_jobs,
)
from .window import window_slots, fetch_resets_at, build_slots, WINDOW_HOURS

__all__ = [
    "TOOL_DIR", "JOBS_FILE", "SETTINGS_FILE", "PROJECTS_DIR", "TASK_FOLDER",
    "UUID_RE", "DAY_NAMES", "DAY_ORDER", "PERMISSION_MODES", "MODELS",
    "TERMINALS", "DEFAULT_SETTINGS", "load_settings", "save_settings",
    "load_jobs", "save_jobs", "resolve_claude", "resolve_terminal",
    "default_terminal",
    "scan_sessions", "open_session_ids", "sanitize_name", "task_name_for",
    "describe_target", "describe_schedule", "next_fire", "make_job",
    "find_job", "build_claude_args", "build_action", "build_trigger_xml",
    "build_task_xml", "schtasks", "task_create", "task_delete", "task_run",
    "task_query_all", "task_query", "register_job", "delete_job", "prune_jobs",
    "window_slots", "fetch_resets_at", "build_slots", "WINDOW_HOURS",
]
