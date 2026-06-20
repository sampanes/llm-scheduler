"""Session discovery from ~/.claude/projects/<munged>/<uuid>.jsonl.

Reads the head of each session file to recover its working directory and a
human title, flags sessions that belong to a currently-running claude process
(via the live ~/.claude/sessions registry), and returns them newest-first.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .config import CODEX_SESSIONS_DIR, PROJECTS_DIR, UUID_RE


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


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("text", "input_text"):
                text = block.get("text")
                if text:
                    parts.append(text)
        return "\n".join(parts) if parts else None
    return None


def _is_synthetic_codex_title(text):
    text = (text or "").lstrip()
    return text.startswith((
        "# AGENTS.md instructions",
        "<environment_context",
        "<permissions instructions",
        "<collaboration_mode",
        "<apps_instructions",
        "<skills_instructions",
        "The following is the Codex agent history whose request action you are assessing",
        "The following is the Codex agent history added since your last approval assessment",
    ))


def _extract_codex_session_meta(path, max_bytes=262144, max_lines=300):
    """Best-effort id + cwd + human title from a Codex rollout JSONL."""
    sid, cwd, title = None, None, None
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
                typ = rec.get("type")
                payload = rec.get("payload") or {}
                if typ == "session_meta":
                    sid = sid or payload.get("id")
                    cwd = cwd or payload.get("cwd")
                elif typ == "event_msg" and title is None:
                    if payload.get("type") == "user_message":
                        candidate = payload.get("message")
                        if not _is_synthetic_codex_title(candidate):
                            title = candidate
                elif typ == "response_item" and title is None:
                    if payload.get("type") == "message" and payload.get("role") == "user":
                        candidate = _content_text(payload.get("content"))
                        if not _is_synthetic_codex_title(candidate):
                            title = candidate
                if sid and cwd and title:
                    break
    except OSError:
        pass
    if title:
        title = " ".join(title.split())
    return sid, cwd, title


_CUSTOM_TITLE_TAIL = 262144  # only the file's tail can hold the newest rename


def _find_custom_title(path, tail_bytes=_CUSTOM_TITLE_TAIL):
    """Last title record in the jsonl, or None.

    Claude has used both custom-title/customTitle and ai-title/aiTitle records.
    Renames are appended, so scanning the tail backwards gives the newest
    visible title without reading every byte of large session logs.
    """
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
    for raw in reversed(data.splitlines()):
        if b"custom-title" not in raw and b"ai-title" not in raw:
            continue
        try:
            rec = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        typ = rec.get("type")
        if typ == "custom-title" and rec.get("customTitle"):
            return rec["customTitle"]
        if typ == "ai-title" and rec.get("aiTitle"):
            return rec["aiTitle"]
    return None


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


def _scan_claude_sessions(days=0, dir_filter=None, search=None):
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


def _scan_codex_sessions(days=0, dir_filter=None, search=None):
    sessions = []
    if not CODEX_SESSIONS_DIR.exists():
        return sessions
    cutoff = None
    if days and days > 0:
        cutoff = datetime.now() - timedelta(days=days)
    for f in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
        try:
            st = f.stat()
        except OSError:
            continue
        mtime = datetime.fromtimestamp(st.st_mtime)
        if cutoff and mtime < cutoff:
            continue
        sid, cwd, title = _extract_codex_session_meta(f)
        if not sid or not UUID_RE.match(sid):
            continue
        cwd = cwd or str(f.parent)
        if dir_filter and cwd.lower() != dir_filter.lower():
            continue
        entry = {
            "id": sid,
            "dir": cwd,
            "title": (title or "")[:120],
            "custom": False,
            "mtime": mtime,
            "size": st.st_size,
            "active": False,
        }
        if search:
            hay = f"{sid} {cwd} {entry['title']}".lower()
            if search.lower() not in hay:
                continue
        sessions.append(entry)
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    seen, deduped = set(), []
    for s in sessions:
        if s["id"] not in seen:
            seen.add(s["id"])
            deduped.append(s)
    return deduped


def scan_sessions(days=0, dir_filter=None, search=None, tool="claude"):
    """Return session dicts sorted newest-first."""
    if tool == "codex":
        return _scan_codex_sessions(days=days, dir_filter=dir_filter, search=search)
    return _scan_claude_sessions(days=days, dir_filter=dir_filter, search=search)
