"""Brand-themed pywebview GUI for claude-at.

The look is a swappable "brand pack": webui/brand/brand.json (accent, font, logo,
name) + the asset files it points at. compose_html() injects the font @font-face,
a :root accent/font override, and the logo into the page, so re-theming means
editing brand.json and dropping in a logo -- no code changes. app.css ships
white-label defaults (a Claude-coral accent) so an unbranded clone still looks
coherent.

Renders webui/ (index.html + app.css + app.js + the active brand pack) into a
single SELF-CONTAINED HTML build (webui/_app.html) that an embedded WebView2
control loads over a `file://` URL. (file:// — not pywebview's `html=`
/NavigateToString — because the js_api bridge only attaches when the page has a
real origin; `html=` yields a null origin and the bridge never appears. `html=`
survives only as a read-only-dir fallback in run_gui.) That means:

  * no HTTP server, no bound localhost port, nothing to orphan on close
    (pywebview only starts its bottle server for relative paths or
    http_server=True, neither of which we use);
  * Python <-> JS is the in-process js_api bridge (postMessage), not a socket;
  * fonts are base64-embedded and SVGs/CSS/JS inlined into the one file, so no
    sidecar relative loads are needed.

All scheduling/session logic comes from catcore; this file only adapts it to
JSON-friendly shapes for the front-end. The same composed _app.html can also be
opened in a plain browser to preview the look (the front-end falls back to mock
data when the bridge is absent).
"""

import base64
import json
import threading
from datetime import datetime
from pathlib import Path

from catcore import (
    scan_sessions, load_jobs, find_job, make_job, next_fire, describe_target,
    describe_schedule, task_name_for, build_action, build_task_xml,
    register_job, delete_job, task_run, task_query_all, prune_jobs,
    default_terminal, window_slots as _window_slots,
    MODELS, CODEX_MODELS, PERMISSION_MODES, CODEX_APPROVAL_MODES,
    TERMINALS, EFFORT_LEVELS, DAY_ORDER, UUID_RE,
)

WEBUI_DIR = Path(__file__).resolve().parent / "webui"
BRAND = WEBUI_DIR / "brand"
BRAND_FILE = BRAND / "brand.json"     # the active brand pack (accent/font/logo)
PRESETS_FILE = BRAND / "presets.json"  # canned themes a pack can pick by name
ICONS = BRAND / "icons"
APP_ICON = ICONS / "claude-at.ico"    # window/taskbar icon (Claude coral hourglass) — ours

# White-label fallback used when brand.json is missing/unreadable, so the GUI
# always composes. A brand pack overrides these in webui/brand/brand.json.
_DEFAULT_BRAND = {
    "name": "claude-at",
    "accent": "#c15f3c", "accentHover": "#a9502f", "accentActive": "#8f4327",
    "logo": None,
    "appId": "ClaudeAt",
    "font": {
        "family": "Lato",
        "stack": "'Lato', 'Helvetica Neue', 'Segoe UI', Arial, sans-serif",
        "files": [],
    },
}


def load_brand():
    """Read webui/brand/brand.json (the active brand pack). Missing or invalid
    -> the white-label defaults, so swapping brands never breaks composition.

    Resolution order: defaults -> named canned theme (brand.json's optional
    "preset", looked up in presets.json) -> brand.json's own keys. So a pack can
    grab a whole look with one word and still override any single key. With no
    "preset", this is exactly defaults + brand.json (unchanged behaviour)."""
    brand = json.loads(json.dumps(_DEFAULT_BRAND))  # deep copy of the defaults
    data = _read_brand_file()
    if data is None:
        return brand
    preset = data.get("preset")
    if preset:
        _merge_brand(brand, _load_preset(preset))
    _merge_brand(brand, data)
    return brand


def _read_brand_file():
    """brand.json parsed as a dict, or None if missing/unreadable/not an object."""
    try:
        data = json.loads(BRAND_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _merge_brand(brand, overlay):
    """Overlay a partial brand dict onto `brand` in place. The nested `font` dict
    merges key-by-key (an overlay can set just `stack`); other keys replace. The
    `preset` key is consumed here so it never leaks into the resolved brand."""
    if not isinstance(overlay, dict):
        return
    overlay = dict(overlay)            # copy: don't mutate the caller's dict
    overlay.pop("preset", None)
    font = overlay.pop("font", None)
    brand.update(overlay)
    if isinstance(font, dict):
        brand["font"].update(font)


def _load_preset(name):
    """A named canned theme from webui/brand/presets.json (a flat map of
    name -> partial brand dict). Unknown name or unreadable file -> {} (ignored,
    so a bad/missing preset never breaks composition)."""
    try:
        presets = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(presets, dict):
        return {}
    preset = presets.get(name)
    return preset if isinstance(preset, dict) else {}


def _hex_to_rgb(hexstr):
    """'#e31f26' (or '#e12') -> (227, 31, 38). Coral on anything unparseable."""
    h = (hexstr or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (193, 95, 60)


def _font_face_css(brand):
    """@font-face rules for the brand's bundled font files (base64-embedded)."""
    family = brand["font"].get("family") or "sans-serif"
    rules = []
    for f in brand["font"].get("files", []):
        p = BRAND / f.get("file", "")
        if not p.is_file():
            continue
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        rules.append(
            f"@font-face{{font-family:'{family}';"
            f"font-style:{f.get('style', 'normal')};"
            f"font-weight:{f.get('weight', 400)};font-display:swap;"
            f"src:url(data:font/ttf;base64,{b64}) format('truetype');}}")
    return "\n".join(rules)


# Optional per-theme surface overrides (brand.json "colors"). Each maps to a
# semantic token; omitted keys keep app.css's neutral defaults, so a pack can
# tweak just the background without restating the rest.
_COLOR_VARS = {"bg": "--bg", "surface": "--surface", "surface2": "--surface-2",
               "fg": "--fg", "fgMuted": "--fg-muted", "border": "--border"}


def _brand_root_css(brand):
    """A :root override (accent + font) generated from the brand pack, plus
    optional per-theme surface colours. Injected AFTER app.css so the pack's
    values win over app.css's white-label defaults."""
    accent = brand.get("accent") or "#c15f3c"
    r, g, b = _hex_to_rgb(accent)
    stack = brand["font"].get("stack") or "'Lato', 'Segoe UI', Arial, sans-serif"
    blocks = ["<style>", ":root{"
              f"--brand-accent:{accent};"
              f"--brand-accent-hover:{brand.get('accentHover') or accent};"
              f"--brand-accent-active:{brand.get('accentActive') or accent};"
              f"--brand-accent-rgb:{r}, {g}, {b};"
              f"--font-primary:{stack};"
              "}"]
    # Per-theme so a light override can't clobber dark mode (and vice-versa):
    # scope to [data-theme="..."], which app.css's dark block also uses.
    colors = brand.get("colors") or {}
    for theme in ("light", "dark"):
        spec = colors.get(theme) or {}
        decls = "".join(f"{_COLOR_VARS[k]}:{v};"
                        for k, v in spec.items() if k in _COLOR_VARS)
        if decls:
            blocks.append(f'[data-theme="{theme}"]{{{decls}}}')
    blocks.append("</style>")
    return "".join(blocks)


def _logo_markup(brand):
    """The brand logo as innerHTML: inline SVG for .svg, an <img> data-uri for a
    raster file (so a teammate can drop in a .png/.svg). None if unset/missing."""
    rel = brand.get("logo")
    if not rel:
        return None
    p = BRAND / rel
    if not p.is_file():
        return None
    if p.suffix.lower() == ".svg":
        return p.read_text(encoding="utf-8")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp"}.get(p.suffix.lower())
    if not mime:
        return None
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return (f'<img src="data:{mime};base64,{b64}" alt="" '
            'style="width:100%;height:100%;object-fit:contain;display:block">')


def _icons_js(brand):
    icons = {}
    for key, fn in {
        "chevron": "icon-chevron.svg", "close": "icon-close.svg",
        "expand": "icon-expand.svg", "collapse": "icon-collapse.svg",
        "drag": "icon-drag.svg",
        "spark": "icon-spark.svg",  # Claude coral sunburst (in-app accent) — ours
    }.items():
        p = ICONS / fn
        if p.is_file():
            icons[key] = p.read_text(encoding="utf-8")
    logo = _logo_markup(brand)
    if logo:
        icons["logo"] = logo
    return "window.ICONS = " + json.dumps(icons) + ";"


def _esc_attr(s):
    """Minimal escaping for text dropped into an HTML attribute (the brand name
    rendered into title="...")."""
    return (str(s).replace("&", "&amp;").replace('"', "&quot;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def compose_html(write_preview=True):
    brand = load_brand()
    html = (WEBUI_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEBUI_DIR / "app.css").read_text(encoding="utf-8")
    js = (WEBUI_DIR / "app.js").read_text(encoding="utf-8")
    html = html.replace("<!--FONTS-->", f"<style>{_font_face_css(brand)}</style>")
    html = html.replace("<!--APP_CSS-->", css)
    html = html.replace("<!--BRAND_CSS-->", _brand_root_css(brand))
    html = html.replace("<!--ICONS_JS-->", _icons_js(brand))
    html = html.replace("<!--APP_JS-->", js)
    html = html.replace("<!--BRAND_NAME-->",
                        _esc_attr(brand.get("name") or "claude-at"))
    if write_preview:
        try:
            (WEBUI_DIR / "_app.html").write_text(html, encoding="utf-8")
        except OSError:
            pass
    return html


def _loadable_app_file(html):
    """Return a path holding exactly `html`, loadable over file:// — which the
    js_api bridge REQUIRES (loading via html=/NavigateToString yields a null
    origin and the bridge never attaches, hanging the UI on the boot veil).

    Prefer the canonical webui/_app.html that compose_html best-effort wrote;
    trust it only if its content is current (a prior instance / OneDrive / AV
    scan can hold a write lock, leaving it stale or missing). Otherwise write a
    temp file we can load instead. Returns None only when nothing is writable —
    the caller then degrades to html= with the bridge disabled (and says so)."""
    canonical = WEBUI_DIR / "_app.html"
    try:
        if canonical.read_text(encoding="utf-8") == html:
            return canonical
    except OSError:
        pass
    import tempfile
    fallback = Path(tempfile.gettempdir()) / "claude-at_app.html"
    try:
        fallback.write_text(html, encoding="utf-8")
        return fallback
    except OSError:
        return None


_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _screenshots_dir():
    """Best-effort path to the user's Windows Screenshots folder.

    Authoritative source is the Screenshots known-folder location in the
    registry (a winreg *read* — not the persistence-key *write* the global rule
    forbids — and it handles Pictures/OneDrive redirection). Falls back to the
    common defaults when that value isn't set (it only appears once Explorer has
    used the folder; when absent, the Pictures default is used)."""
    import os
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            val, _ = winreg.QueryValueEx(
                k, "{B7BEDE81-DF94-4682-A7D8-57A52620B86F}")
        p = Path(os.path.expandvars(val))
        if p.is_dir():
            return p
    except (OSError, ImportError):
        pass
    cands = [Path.home() / "Pictures" / "Screenshots",
             Path.home() / "OneDrive" / "Pictures" / "Screenshots"]
    for env in ("OneDriveCommercial", "OneDrive", "OneDriveConsumer"):
        base = os.environ.get(env)
        if base:
            cands.append(Path(base) / "Pictures" / "Screenshots")
    for c in cands:
        if c.is_dir():
            return c
    return None


# --------------------------------------------------------------------------
# js_api bridge — methods are callable from JS as pywebview.api.<name>(...).
# Underscore-prefixed methods stay private (pywebview won't expose them).
# --------------------------------------------------------------------------

class Api:
    def __init__(self, settings):
        self.settings = settings
        # MUST stay underscore-prefixed. pywebview's inject_pywebview walks
        # dir(js_api) to discover exposed methods and RECURSES into every
        # non-underscore attribute that has a __module__ (util.py get_functions).
        # A bare `window` holds the native WinForms Form, whose
        # .native.AccessibilityObject.Bounds.Empty.Empty… is an infinite
        # pythonnet Rectangle chain -> RecursionError mid-inject -> the loaded
        # event never fires and the js_api bridge never attaches, hanging the UI
        # on the boot veil forever (worst under pythonw, where the cold-start
        # race reliably loses). The leading underscore makes the walker skip it.
        self._window = None
        self._calls = []  # records method names JS invokes (bridge diagnostics)
        # Prewarmed boot data (see _prewarm + run_gui): the cold, I/O-heavy first
        # session scan and the slow schtasks query run on a background thread
        # that overlaps WebView2's multi-second cold start, so the bridge calls
        # JS fires the instant it attaches return cached data instead of
        # blocking the UI-thread message pump (which is what makes the window go
        # "Not Responding"). Each cache is served exactly once; later refreshes
        # always hit the live backend.
        self._lock = threading.Lock()
        self._scan_cache = None   # (days, rows)
        self._jobs_cache = None   # rows
        self._window_cache = {}  # tool -> last good window_slots() result

    def _prewarm(self, days):
        """Compute the heavy boot data off the UI thread, during cold start.

        Best-effort: any failure just means the corresponding bridge call falls
        back to computing live (the prior behavior), so this can never crash the
        boot or wedge the thread."""
        try:
            rows = self._scan_rows(days, tool="claude")
            with self._lock:
                self._scan_cache = (("claude", days), rows)
        except Exception:  # noqa: BLE001 — prewarm must never raise
            pass
        try:
            jobs = self._build_jobs()
            with self._lock:
                self._jobs_cache = jobs
        except Exception:  # noqa: BLE001 — prewarm must never raise
            pass

    def _invalidate(self):
        """Drop the prewarm/boot caches so the next read goes live.

        Called after every write (schedule/delete/run_now/prune). A populated
        cache is a boot-time snapshot; if prewarm lands after boot-init (likeliest
        on the slow disks prewarm targets), serving it once more after a mutation
        would make the just-changed job look missing until a second refresh."""
        with self._lock:
            self._scan_cache = None
            self._jobs_cache = None

    # ---- reads ----
    def defaults(self):
        self._calls.append("defaults")
        s = self.settings
        return {
            "tools": [
                {"id": "claude", "label": "Claude Code", "models": MODELS,
                 "modes": PERMISSION_MODES, "mode_label": "Permissions",
                 "model": s["model"], "mode": s["permission_mode"],
                 "supports_sessions": True, "supports_window": True},
                {"id": "codex", "label": "Codex CLI", "models": CODEX_MODELS,
                 "modes": CODEX_APPROVAL_MODES, "mode_label": "Approval",
                 "model": s.get("codex_model", "default"),
                 "mode": s.get("codex_approval_mode", "default"),
                 "supports_sessions": True, "supports_window": True},
            ],
            "tool": s.get("tool", "claude"),
            "models": MODELS, "modes": PERMISSION_MODES, "terminals": TERMINALS,
            "efforts": EFFORT_LEVELS, "days": DAY_ORDER,
            "model": s["model"], "mode": s["permission_mode"],
            "effort": s.get("effort", ""),
            "terminal": s["terminal"] or default_terminal(s),
            "default_dir": s.get("default_dir") or str(Path.home()),
            "sessions_days": s.get("sessions_days", 14),
        }

    def _scan_rows(self, days, tool="claude"):
        return [{
            "id": r["id"], "dir": r["dir"], "title": r["title"],
            "active": r["active"], "mtime": r["mtime"].isoformat(),
        } for r in scan_sessions(days=days, tool=tool)]

    def scan(self, days=0, tool="claude"):
        self._calls.append("scan")
        try:
            days = int(days or 0)
        except (TypeError, ValueError):
            days = 0
        tool = tool if tool in ("claude", "codex") else "claude"
        with self._lock:
            if self._scan_cache and self._scan_cache[0] == (tool, days):
                rows, self._scan_cache = self._scan_cache[1], None
                return rows
        return self._scan_rows(days, tool=tool)

    def list_jobs(self):
        self._calls.append("list_jobs")
        with self._lock:
            if self._jobs_cache is not None:
                rows, self._jobs_cache = self._jobs_cache, None
                return rows
        return self._build_jobs()

    def _build_jobs(self):
        jobs = sorted(load_jobs(), key=lambda j: (next_fire(j) or datetime.max))
        qall = task_query_all()
        out = []
        for j in jobs:
            nf = next_fire(j)
            q = qall.get(j.get("task_name", task_name_for(j)))
            out.append({
                "id": j["id"],
                "label": f"{j['name']}-{j['id']}",
                "name": j["name"],
                "tool": j.get("tool", "claude"),
                "next": nf.isoformat() if nf else None,
                "schedule_disp": describe_schedule(j),
                "target_disp": describe_target(j),
                "model": j["model"], "mode": j["permission_mode"],
                "effort": j.get("effort", ""),
                "terminal": j["terminal"],
                "status": q["status"] if q else "MISSING",
                # for Load-into-form:
                "target_mode": j["target"]["mode"],
                "session_id": j["target"].get("session_id", ""),
                "dir": j["dir"], "schedule": j["schedule"],
                "prompt": j.get("prompt", ""), "extra_args": j.get("extra_args", ""),
                "require_network": j.get("require_network", True),
                "delete_after_run": j.get("delete_after_run", True),
            })
        return out

    def window_slots(self, tool="claude", refresh=False):
        """The Claude/Codex 5h-window quick-pick grid for the schedule form.

        Returns catcore.window_slots()'s dict. Session-cached: the future grid
        {anchor, +5h, …} stays correct as long as the anchor (next window open)
        is still ahead of now, so we reuse it and only re-hit the usage API on an
        explicit refresh, or once the anchor passes (we've entered a new window),
        or for the idle case (anchor=now drifts). Network/auth failures aren't
        cached — the next call retries — and never raise into the bridge."""
        self._calls.append("window_slots")
        tool = tool if tool in ("claude", "codex") else "claude"
        if not refresh:
            with self._lock:
                cache = self._window_cache.get(tool)
            if cache is not None and cache.get("active"):
                try:
                    if datetime.fromisoformat(cache["anchor_iso"]) > datetime.now():
                        return cache
                except (KeyError, TypeError, ValueError):
                    pass
        res = _window_slots(tool=tool, settings=self.settings)
        if res.get("ok"):
            with self._lock:
                self._window_cache[tool] = res
        return res

    # ---- build a job from the form dict (validates) ----
    def _job_from_form(self, f):
        tool = f.get("tool") if f.get("tool") in ("claude", "codex") else "claude"
        target_mode = f.get("target") or "continue"
        sid = (f.get("session") or "").strip()
        if target_mode == "resume" and not UUID_RE.match(sid):
            raise ValueError("Resume needs a session UUID (click one in Sessions).")
        d = (f.get("dir") or "").strip()
        if not d or not Path(d).is_dir():
            raise ValueError(f"Directory does not exist: {d or '(empty)'}")
        t = (f.get("time") or "").strip()
        try:
            datetime.strptime(t, "%H:%M")
        except ValueError:
            raise ValueError("Time must be HH:MM.")
        when = f.get("when") or "once"
        if when == "once":
            date = (f.get("date") or "").strip()
            try:
                dt = datetime.fromisoformat(f"{date}T{t}")
            except ValueError:
                raise ValueError("Pick a valid date for a one-shot run.")
            if dt <= datetime.now():
                raise ValueError("That date/time is in the past.")
            schedule = {"type": "once",
                        "datetime": dt.isoformat(timespec="minutes")}
        elif when == "daily":
            schedule = {"type": "daily", "time": t}
        else:
            days = [d_ for d_ in DAY_ORDER if d_ in (f.get("days") or [])]
            if not days:
                raise ValueError("Pick at least one weekday (or hit M–F).")
            schedule = {"type": "weekly", "days": days, "time": t}
        name = (f.get("name") or "").strip() or f"{target_mode}-{Path(d).name}"
        if tool == "codex":
            model = f.get("model") or self.settings.get("codex_model", "default")
            mode = f.get("mode") or self.settings.get("codex_approval_mode", "default")
        else:
            model = f.get("model") or self.settings["model"]
            mode = f.get("mode") or self.settings["permission_mode"]
        return make_job(
            name, d, target_mode, sid, schedule,
            model, mode,
            f.get("terminal") or self.settings["terminal"] or default_terminal(self.settings),
            f.get("prompt") or "", f.get("extra") or "",
            bool(f.get("net", True)), not bool(f.get("keep", False)),
            tool=tool,
            # effort is claude-only (--effort); codex jobs store "" and never emit it
            effort=((f.get("effort") or self.settings.get("effort", ""))
                    if tool == "claude" else ""),
        )

    # ---- writes ----
    def preview(self, f):
        try:
            job = self._job_from_form(f)
            cmd, argstr, wd = build_action(job, self.settings)
            xml = build_task_xml(job, self.settings)
            nf = next_fire(job)
            return {"ok": True, "action": cmd, "args": argstr, "workdir": wd,
                    "next": nf.strftime("%Y-%m-%d %H:%M") if nf else "—",
                    "xml": xml}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    def schedule(self, f):
        try:
            job = self._job_from_form(f)
            register_job(job, self.settings)
            self._invalidate()
            return {"ok": True, "name": f"{job['name']}-{job['id']}",
                    "id": job["id"], "desc": describe_schedule(job)}
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    def delete(self, job_id):
        j = find_job(load_jobs(), job_id)
        if not j:
            return {"error": "job not found"}
        try:
            delete_job(j)
            self._invalidate()
            return {"ok": True}
        except RuntimeError as e:
            return {"error": str(e)}

    def run_now(self, job_id):
        j = find_job(load_jobs(), job_id)
        if not j:
            return {"error": "job not found"}
        try:
            task_run(j.get("task_name", task_name_for(j)))
            self._invalidate()  # task status changed -> let the next read refresh it
            return {"ok": True}
        except RuntimeError as e:
            return {"error": str(e)}

    def prune(self):
        count = len(prune_jobs(verbose=False))
        self._invalidate()
        return {"ok": True, "count": count}

    def browse(self, initial=""):
        if not self._window:
            return {"path": None}
        import webview
        res = self._window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=initial or str(Path.home()))
        if res:
            p = res[0] if isinstance(res, (list, tuple)) else res
            return {"path": str(p)}
        return {"path": None}

    def browse_file(self, initial=""):
        # Pick a single existing file; the front-end appends its absolute path to
        # the kick-off prompt (e.g. to point the session at a screenshot). Opens
        # in `initial` (the form's working dir) when given, else home; the OS
        # dialog then remembers wherever the user last navigated.
        if not self._window:
            return {"path": None}
        import webview
        start = initial if initial and Path(initial).is_dir() else str(Path.home())
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, directory=start, allow_multiple=False)
        if res:
            p = res[0] if isinstance(res, (list, tuple)) else res
            return {"path": str(p)}
        return {"path": None}

    def latest_screenshot(self):
        # One click: append the newest image in the Screenshots folder, so the
        # user never has to know where captures land or hunt for the right file.
        d = _screenshots_dir()
        if not d:
            return {"path": None, "error": "Couldn't find your Screenshots folder."}
        newest, newest_m = None, -1.0
        try:
            for f in d.iterdir():
                if f.suffix.lower() not in _IMG_EXTS:
                    continue
                try:
                    m = f.stat().st_mtime
                except OSError:
                    continue
                if m > newest_m:
                    newest, newest_m = f, m
        except OSError as e:
            return {"path": None, "error": f"Can't read {d}: {e}"}
        if not newest:
            return {"path": None, "error": f"No images in {d}"}
        return {"path": str(newest), "name": newest.name}


def run_gui(settings, smoke=False):
    import logging
    import webview
    # This WebView2 build rejects a couple of ICoreWebView2Controller2 property
    # reads pywebview makes (DefaultBackgroundColor / AllowExternalDrop); they're
    # non-fatal and only logged. Hush them so the console stays clean.
    logging.getLogger("pywebview").setLevel(logging.CRITICAL)

    # Give the process its own AppUserModelID so the taskbar shows OUR window
    # under our icon instead of grouping it under the generic python.exe entry.
    # Must run before any window is shown. (A native COM call via ctypes — not a
    # registry/persistence write — so it doesn't trip the PowerShell AMSI rule.)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            load_brand().get("appId") or "ClaudeAt")
    except Exception:  # noqa: BLE001 — cosmetic; never block launch on it
        pass
    api = Api(settings)
    # Load the self-contained build via a file:// URL rather than html=
    # (NavigateToString): file:// gives a real origin, which the js_api bridge
    # needs to attach on WebView2. It's still server-less — pywebview only
    # starts its bottle server for relative paths or http_server=True, neither
    # of which we use — so there's still no bound port to orphan.
    html = compose_html(write_preview=True)
    app_file = _loadable_app_file(html)
    common = dict(js_api=api, width=1200, height=920, min_size=(900, 640))
    if app_file is not None:
        window = webview.create_window(
            "claude-at", url=app_file.resolve().as_uri(), **common)
    else:  # neither webui/ nor temp is writable — html= works but the js_api
        # bridge won't attach (null origin), so the UI would hang on the boot
        # veil. Nothing better available; log it loudly rather than fail silent.
        logging.getLogger("pywebview").critical(
            "claude-at: could not write a file:// HTML build; the js_api bridge "
            "will not attach and the UI will hang on the boot veil")
        window = webview.create_window("claude-at", html=html, **common)
    api._window = window  # internal; underscore keeps pywebview's walker out (see __init__)

    # Warm the heavy boot data (session scan + schtasks query) on a background
    # thread NOW, so it overlaps WebView2's cold start instead of running inside
    # the synchronous bridge round-trips JS fires the moment it attaches. With
    # the data already cached, those calls return instantly and the window keeps
    # pumping its message loop (no "Not Responding"). The initial JS scan uses
    # the saved sessions_days, so prewarm with the same value to land a hit.
    try:
        prewarm_days = int(settings.get("sessions_days", 14) or 0)
    except (TypeError, ValueError):
        prewarm_days = 0
    threading.Thread(target=api._prewarm, args=(prewarm_days,),
                     daemon=True).start()

    def _after():
        if not smoke:
            return
        import time
        loaded = window.events.loaded.wait(25)
        print("SMOKE_LOADED:", loaded, flush=True)
        if loaded:
            time.sleep(6)  # let bridge polling + the real scan settle
            try:
                dbg = window.evaluate_js(
                    "JSON.stringify({pv:typeof window.pywebview,"
                    "api:!!(window.pywebview&&window.pywebview.api),"
                    "mode:(window.__CAT__&&window.__CAT__.mode)||null,"
                    "sess:document.querySelectorAll('.sess-row').length,"
                    "chips:document.querySelectorAll('.win-chip').length})")
                print("SMOKE_DBG:", dbg, flush=True)
            except Exception as e:  # noqa: BLE001
                print("SMOKE_DBG_ERR:", e, flush=True)
        print("SMOKE_CALLS:", sorted(set(api._calls)), flush=True)
        window.destroy()

    # Persist WebView2's user-data dir across launches. pywebview defaults to
    # private_mode=True, which discards that dir every run -> every launch is a
    # full cold start. On a Defender-scanned box a cold WebView2 can sit
    # in "Not Responding" long enough that the js_api bridge misses its window
    # and the front-end would fall back to preview data. A warm (reused) user-
    # data dir attaches the bridge far faster. It's only a local cache dir (no
    # sockets, no persistence/Run keys) so it keeps the server-less, AMSI-clean
    # model. Best-effort: if the dir can't be made, fall back to the default.
    import os
    storage = os.path.join(os.environ.get("LOCALAPPDATA") or str(Path.home()),
                           "claude-at", "webview")
    try:
        os.makedirs(storage, exist_ok=True)
    except OSError:
        storage = None

    # icon= sets the WinForms Form.Icon, which drives BOTH the title-bar and
    # taskbar icon (paired with the AppUserModelID above). The backend ignores a
    # missing/nonexistent path, so guard on the file being present.
    icon = str(APP_ICON) if APP_ICON.is_file() else None
    webview.start(_after, icon=icon, private_mode=False, storage_path=storage)
