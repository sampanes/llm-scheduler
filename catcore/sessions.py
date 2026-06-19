"""Session discovery from ~/.claude/projects/<munged>/<uuid>.jsonl.

Reads the head of each session file to recover its working directory and a
human title, flags sessions that belong to a currently-running claude process
(via the live ~/.claude/sessions registry), and returns them newest-first.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import PROJECTS_DIR, UUID_RE


def _extract_session_meta(path, max_bytes=262144, max_lines=300):
    """Best-effort cwd + human title from the head of a session jsonl."""
    cwd, title = None, None
    bytes_read = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                bytes_read += len(line)
                if i >= max_lines or bytes_read > max_bytes:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if cwd is None and rec.get("cwd"):
                    cwd = rec["cwd"]
                if rec.get("type") == "summary" and rec.get("summary"):
                    title = rec["summary"]  # best title; keep last seen
                if title is None and rec.get("type") == "user":
                    content = (rec.get("message") or {}).get("content")
                    text = None
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text")
                                break
                    if text:
                        text = text.strip()
                        if text and not text.startswith("<") \
                                and not text.startswith("Caveat:"):
                            title = " ".join(text.split())
                if cwd and title:
                    # keep scanning a few lines for a summary upgrade only
                    if rec.get("type") == "summary" or i > 30:
                        break
    except OSError:
        pass
    return cwd, title


_CUSTOM_TITLE_TAIL = 262144  # only the file's tail can hold the newest rename


def _find_custom_title(path, tail_bytes=_CUSTOM_TITLE_TAIL):
    """Last user-assigned title in the jsonl (renames append custom-title
    records), or None. Searches backwards so the newest rename wins.

    Renames are *appended*, so the most recent custom-title always lives near
    EOF — we read only the file's tail rather than the whole (often multi-MB)
    jsonl. That keeps the session scan from reading every byte of every session
    on a cold disk, which is the dominant boot-time I/O cost."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            start = max(0, f.tell() - tail_bytes)
            f.seek(start)
            data = f.read()
    except OSError:
        return None
    if start:  # we seeked into the middle of a line — drop the partial head
        nl = data.find(b"\n")
        data = data[nl + 1:] if nl >= 0 else b""
    pos = len(data)
    while True:
        idx = data.rfind(b'"type":"custom-title"', 0, pos)
        if idx < 0:
            return None
        s = data.rfind(b"\n", 0, idx) + 1
        e = data.find(b"\n", idx)
        if e < 0:
            e = len(data)
        try:
            rec = json.loads(data[s:e].decode("utf-8", "replace"))
            if rec.get("type") == "custom-title" and rec.get("customTitle"):
                return rec["customTitle"]
        except ValueError:
            pass
        pos = idx


def open_session_ids():
    """Session ids of currently running claude processes (live registry)."""
    ids = set()
    for f in (Path.home() / ".claude" / "sessions").glob("*.json"):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            if rec.get("sessionId"):
                ids.add(rec["sessionId"])
        except (OSError, ValueError):
            pass
    return ids


def scan_sessions(days=0, dir_filter=None, search=None):
    """Return session dicts sorted newest-first."""
    sessions = []
    if not PROJECTS_DIR.exists():
        return sessions
    cutoff = None
    if days and days > 0:
        cutoff = datetime.now() - timedelta(days=days)
    live = open_session_ids()
    for proj in PROJECTS_DIR.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            sid = f.stem
            if not UUID_RE.match(sid):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            mtime = datetime.fromtimestamp(st.st_mtime)
            if cutoff and mtime < cutoff:
                continue
            cwd, first_msg = _extract_session_meta(f)
            cwd = cwd or proj.name
            if dir_filter and cwd.lower() != dir_filter.lower():
                continue
            custom = _find_custom_title(f)
            entry = {
                "id": sid,
                "dir": cwd,
                "title": (custom or first_msg or "")[:120],
                "custom": bool(custom),
                "mtime": mtime,
                "size": st.st_size,
                "active": sid in live,
            }
            if search:
                hay = f"{sid} {cwd} {entry['title']}".lower()
                if search.lower() not in hay:
                    continue
            sessions.append(entry)
    # the same session id can exist under two project folders (cwd changed
    # mid-session); keep only the most recently touched copy
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    seen, deduped = set(), []
    for s in sessions:
        if s["id"] not in seen:
            seen.add(s["id"])
            deduped.append(s)
    return deduped
