"""claude-at — constants, default settings, and persistence (settings + jobs).

This is the base layer: it imports nothing from the rest of catcore, and
everything else imports from here. jobs.json / settings.json live in the repo
root (the parent of this package).
"""

import json
import os
import re
import sys
from pathlib import Path

# catcore/ lives directly under the repo root; jobs.json + settings.json sit there.
TOOL_DIR = Path(__file__).resolve().parent.parent
JOBS_FILE = TOOL_DIR / "jobs.json"
SETTINGS_FILE = TOOL_DIR / "settings.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
TASK_FOLDER = "ClaudeAt"  # tasks live under \ClaudeAt\ in Task Scheduler

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
DAY_NAMES = {
    "MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday",
    "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday",
}
DAY_ORDER = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
PERMISSION_MODES = ["auto", "acceptEdits", "bypassPermissions", "dontAsk",
                    "plan", "default"]
MODELS = ["fable", "opus", "sonnet", "haiku"]
TERMINALS = ["wezterm", "wt", "console"]

DEFAULT_SETTINGS = {
    "model": "opus",
    "permission_mode": "auto",
    "terminal": "",                 # "" = auto-detect (wezterm/wt if installed,
                                    # else console); set a value here to pin one
    "require_network": True,
    "delete_after_run": True,       # one-shot tasks self-expire
    "sessions_days": 14,            # default recency filter in GUI
    "default_dir": str(Path.home()),  # no-pick fallback (folder picker's start dir)
    "claude_path": "",              # auto-detected when empty
    "wezterm_path": "",
    "wt_path": "",
    "missed_run_window_hours": 25,  # one-shots still fire this late if missed
}


# --------------------------------------------------------------------------
# settings / jobs persistence
# --------------------------------------------------------------------------

def _atomic_write_text(path, text):
    """Write text via a sibling .tmp + os.replace() so the target is never torn.

    os.replace() is atomic within a filesystem, so a crash mid-write can only
    damage the throwaway .tmp — the real file keeps its last good contents. This
    matters most for jobs.json: a truncated jobs.json makes load_jobs return []
    and the GUI 'forgets' every job (the scheduled Windows tasks still fire
    regardless; only the GUI's view is lost)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"warning: bad settings.json ignored ({e})", file=sys.stderr)
    return s


def save_settings(s):
    _atomic_write_text(SETTINGS_FILE, json.dumps(s, indent=2))


def load_jobs():
    if JOBS_FILE.exists():
        try:
            return json.loads(JOBS_FILE.read_text(encoding="utf-8"))["jobs"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            # TypeError covers a top-level JSON list/scalar where ["jobs"] is
            # invalid indexing — bad jobs.json should degrade to [], never crash.
            print(f"warning: bad jobs.json ignored ({e})", file=sys.stderr)
    return []


def save_jobs(jobs):
    _atomic_write_text(JOBS_FILE, json.dumps({"jobs": jobs}, indent=2))
