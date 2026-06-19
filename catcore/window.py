"""Claude 5-hour usage-window lookup + the local "window-open" slot grid.

The 5h subscription window is SERVER-AUTHORITATIVE: there's no local clock to
reverse-engineer. We ask Anthropic (the same call UsageMonitorForClaude makes)
when the current window resets — which is exactly when the next one opens — and
trust the answer:

    GET https://api.anthropic.com/api/oauth/usage
    -> response["five_hour"]["resets_at"]   (ISO-8601 UTC; empty/absent = idle)

This is the GUI-free port of the validated window-reset.py CLI: same endpoint,
same headers, but it RETURNS a result (never sys.exit / never prints) so the GUI
can degrade gracefully when the call fails. Reading the OAuth token is a local
*file read* in Python (not a PowerShell script), so it doesn't trip the
AMSI PowerShell-scan rule on a managed Windows box.

Two layers:
  * fetch_resets_at() — the network call + parse (impure; needs creds + net).
  * build_slots()     — pure math: given the next window-open instant in local
                        time, produce the {anchor, +5h, +10h, …} fire grid.
window_slots() composes them into the JSON-friendly shape the GUI consumes.

All datetimes handed to the rest of catcore are NAIVE LOCAL, matching the job
model (schedule "once" stores local ISO; next_fire compares against
datetime.now()). resets_at arrives as UTC and is converted on the way out.
"""

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDS = Path.home() / ".claude" / ".credentials.json"
# Match UsageMonitorForClaude's request shape (the validated window-reset.py).
_HEADERS = {
    "Content-Type": "application/json",
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-code/2.1.85",
}
_TIMEOUT = 10
WINDOW_HOURS = 5  # the subscription window length == the chaining step
# Stop chaining chips at the next local time-of-day (hh, mm): past this the daily
# 04:30 Usage-Maximizer takes over, so windows beyond it aren't worth hand-picking.
GRID_CUTOFF = (4, 30)
_MAX_SLOTS = 24   # hard safety cap so an absent/bad cutoff can't loop unbounded


def _read_token(creds_path):
    try:
        data = json.loads(Path(creds_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "no Claude credentials yet — sign in to Claude Code first"
    except (OSError, ValueError) as e:
        return None, f"can't read Claude credentials: {e}"
    tok = (data.get("claudeAiOauth") or {}).get("accessToken")
    if not tok:
        return None, "credentials file has no OAuth access token"
    return tok, None


def fetch_resets_at(creds_path=CREDS):
    """Ask Anthropic when the current 5h window resets.

    Returns (resets_at_utc, active, error):
      resets_at_utc : tz-aware UTC datetime, or None when idle/unavailable.
      active        : True iff a window is open now (resets_at present and
                      utilization > 0); False when idle or on error.
      error         : None on success (the idle case is a success), else a
                      short human string.
    """
    token, err = _read_token(creds_path)
    if err:
        return None, False, err
    req = urllib.request.Request(
        USAGE_URL, headers={**_HEADERS, "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            usage = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None, False, ("the usage API rejected the token (401) — it "
                                 "refreshes on the Claude CLI's next use")
        return None, False, f"usage API error: HTTP {e.code} {e.reason}"
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return None, False, f"couldn't reach the usage API: {e}"

    five = usage.get("five_hour") or {}
    raw = five.get("resets_at") or ""
    util = five.get("utilization")
    if not raw:
        return None, False, None  # idle: no window open right now
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, False, f"usage API returned an unparseable reset time {raw!r}"
    # util absent (None) -> trust resets_at's presence; else require >0.
    active = util is None or util > 0
    return dt, active, None


def _ceil_minute(dt):
    """Round a datetime UP to the next whole minute (drops sub-minute, so the
    HH:MM fire time we emit is never a hair in the past)."""
    if dt.second or dt.microsecond:
        dt = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return dt


def build_slots(anchor_local, count=None, cutoff=None, step_hours=WINDOW_HOURS,
                offset_minutes=1, active=True):
    """Pure: from the next window-open instant (naive local), build the chip grid.

    Each window opens at anchor + k*step_hours; we fire `offset_minutes` after it
    so the run lands just inside a fresh window. Slot 0's label distinguishes the
    active case ("Next window") from idle ("Open now"), where the anchor is now.

    The grid stops at whichever bound is given:
      cutoff : a naive-local datetime — include a window only if it opens at/before
               it (slot 0 is always kept, even past the cutoff, as the default pick).
      count  : a hard slot count.
    With neither, it falls back to 3 (and _MAX_SLOTS guards either way).
    Returns a list of {label, date, time, iso, offset_h} dicts (local time).
    """
    if count is None and cutoff is None:
        count = 3
    base = _ceil_minute(anchor_local)
    slots = []
    for k in range(_MAX_SLOTS):
        open_k = base + timedelta(hours=step_hours * k)   # window-open instant
        if k > 0:
            if count is not None and k >= count:
                break
            if cutoff is not None and open_k > cutoff:
                break
        fire = open_k + timedelta(minutes=offset_minutes)
        label = ("Next window" if active else "Open now") if k == 0 \
            else f"+{step_hours * k}h"
        slots.append({
            "label": label,
            "date": fire.strftime("%Y-%m-%d"),
            "time": fire.strftime("%H:%M"),
            "iso": fire.strftime("%Y-%m-%dT%H:%M"),
            "offset_h": step_hours * k,
        })
    return slots


def _next_cutoff(now, hhmm):
    """The next local datetime-of-day hhmm strictly after now (today or tomorrow)."""
    if not hhmm:
        return None
    hh, mm = hhmm
    c = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return c if c > now else c + timedelta(days=1)


def window_slots(creds_path=CREDS, now=None, step_hours=WINDOW_HOURS,
                 offset_minutes=1, cutoff_hhmm=GRID_CUTOFF):
    """High-level: fetch + build the slot grid in local time.

    Returns a JSON-friendly dict the GUI bridge can hand straight to JS:
      {ok, error, active, anchor_iso, slots:[{label,date,time,iso,offset_h}]}

    The grid chains windows only up to the next `cutoff_hhmm` (default 04:30,
    where the daily Usage-Maximizer takes over), so it never spans further than
    the user cares to hand-schedule. Cases:
      * active window  -> ok, active=True, anchor = resets_at (local).
      * idle (no window) -> ok, active=False, anchor = now: open a window ASAP
        (with automation firing it, every window does work, so there's no reason
        to wait — see the design note for why opening early always wins here).
      * fetch failed   -> ok=False with an error; slots=[] so the GUI keeps the
        manual time picker and shows the reason, never blocking scheduling.
    """
    now = now or datetime.now()
    resets_utc, active, err = fetch_resets_at(creds_path)
    if err:
        return {"ok": False, "error": err, "active": False, "slots": []}
    if resets_utc is None:               # idle -> anchor on now (open ASAP)
        anchor_local = now
    else:                                # convert UTC -> naive local
        anchor_local = resets_utc.astimezone().replace(tzinfo=None)
    slots = build_slots(anchor_local, cutoff=_next_cutoff(now, cutoff_hhmm),
                        step_hours=step_hours, offset_minutes=offset_minutes,
                        active=active)
    return {"ok": True, "error": None, "active": active,
            "anchor_iso": anchor_local.strftime("%Y-%m-%dT%H:%M"),
            "slots": slots}
