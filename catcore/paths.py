"""Resolve the claude.exe and terminal executables.

Honors explicit paths from settings.json first, then PATH, then known install
locations. Returns None when an executable can't be found.
"""

import shutil
from pathlib import Path


def resolve_claude(settings):
    # .is_file() (not .exists()): every candidate is an executable. A settings
    # path pointing at a directory must be rejected here, not accepted and then
    # failed cryptically when Task Scheduler tries to run it as the action.
    p = settings.get("claude_path")
    if p and Path(p).is_file():
        return p
    p = shutil.which("claude")
    if p:
        return p
    cand = Path.home() / ".local" / "bin" / "claude.exe"
    return str(cand) if cand.is_file() else None


def resolve_terminal(settings, terminal):
    if terminal == "wezterm":
        p = settings.get("wezterm_path")
        if p and Path(p).is_file():
            return p
        p = shutil.which("wezterm-gui")
        if p:
            return p
        cand = Path(r"C:\Program Files\WezTerm\wezterm-gui.exe")
        return str(cand) if cand.is_file() else None
    if terminal == "wt":
        p = settings.get("wt_path")
        if p and Path(p).is_file():
            return p
        return shutil.which("wt")
    return None  # console: claude.exe runs as the task action directly


def default_terminal(settings):
    """Which terminal to use when settings.json doesn't pin one.

    Picks the first of wezterm / Windows Terminal that's actually installed,
    else "console" (which needs nothing -- claude.exe just runs in the console
    Task Scheduler gives it). This is what keeps a fresh clone working without
    assuming any particular terminal is present; set "terminal" in settings.json
    to override the auto-pick.
    """
    for term in ("wezterm", "wt"):
        if resolve_terminal(settings, term):
            return term
    return "console"
