# claude-at

Current status: Claude Code and Codex CLI are both schedulable. Claude uses the
Anthropic usage API for 5-hour window chips; Codex uses `codex app-server
--stdio` and `account/rateLimits/read` for the same window picker.

Schedule Claude Code session resumes on Windows: "open claude at *time*, in
*directory*, resuming *that chat*, model *fable*, permission mode *auto*" —
with a GUI for picking the chat and seeing pending runs.

A companion to headless `claude -p` automation; this one opens **interactive**
sessions in a terminal.

## Quick start

```
ClaudeAt-GUI.cmd          # GUI, no console window
claude-at.cmd             # same GUI via console shim
claude-at.cmd doctor      # verify resolved paths first run
```

The GUI is a **brand-themed web UI** rendered in an embedded WebView2
control via [pywebview](https://pywebview.flowlib.com/). It is **server-less** —
the page is handed to the webview as a self-contained HTML string over the
in-process `js_api` bridge, so there is **no `localhost` port and nothing to
orphan** when you close the window. A `gui --tk` flag (and automatic fallback)
still launches the legacy tkinter GUI if pywebview is unavailable.

GUI layout, top to bottom:

1. **Sessions** — every session under `~/.claude/projects`, newest first,
   with directory + first-prompt/summary title. Filter by directory, text,
   or recency (`Days`, 0 = all). A red **live** dot/chip marks sessions that
   belong to a currently-running `claude` process (from the live
   `~/.claude/sessions` registry). **Click a row** to make it the target of
   the form below. The session scan runs on a background thread behind a
   spinner overlay.
2. **Schedule a run** — target (resume specific session / `--continue`
   latest in dir / new session), once / daily / weekly, model (default
   `opus`), permission mode (default `auto`), terminal (WezTerm / Windows
   Terminal / bare console), optional kick-off prompt that is submitted when
   the session opens, raw extra `claude` flags. *Preview command + XML* shows
   exactly what would be registered without registering it.
3. **Pending runs** — all jobs sorted by next fire time, with live Task
   Scheduler status. Run now / Delete / Load into form / Prune done.

A light/dark theme toggle lives in the top bar (light is the brand-preferred
default). The same UI can be previewed in any browser by opening the composed
`webui/_app.html` — it falls back to mock data when the Python bridge is absent.

CLI equivalents (`claude-at.cmd add --help`), e.g.:

```
claude-at.cmd add --dir "C:\Projects\acme-api" ^
    --resume 11111111-1111-4111-8111-111111111111 ^
    --at "2026-06-13 07:00" --model opus --mode auto --prompt "continue where we left off"
claude-at.cmd add --dir C:\some\repo --continue --daily --time 06:45
claude-at.cmd add --tool codex --dir C:\some\repo --continue --daily --time 06:45
claude-at.cmd list
claude-at.cmd add ... --dry-run     # print task XML, register nothing
```

## Theming — make it your own

The look is a **brand pack**: a single `webui/brand/brand.json` plus the asset
files it points at. To re-brand, edit `brand.json` and drop in your logo — no
code changes:

```json
{
  "name": "Acme",
  "accent": "#2563eb",
  "logo": "logos/acme.svg",
  "font": { "stack": "'Inter', 'Segoe UI', sans-serif", "files": [] }
}
```

* **`preset`** *(optional)* — name a canned theme from
  `webui/brand/presets.json` (ships `coral`, `ocean`, `forest`, `violet`,
  `amber`, `slate`) to grab a whole accent set in one word:
  `{ "preset": "ocean" }`. Any key you also set below still wins over it, so you
  can start from a preset and tweak. Omit it and nothing changes.
* **`accent`** — the one brand color (buttons, emphasis, focus). Hover/active
  shades default to it; set `accentHover` / `accentActive` to override.
* **`logo`** — a path under `webui/brand/` to an `.svg` or raster (`.png`, …),
  embedded at launch and shown top-left + on the boot splash. Omit it to show
  just the wordmark.
* **`font`** — `stack` is the CSS `font-family`; list bundled `files`
  (`{ file, weight, style }`, paths under `webui/brand/`) to embed a custom
  font, or leave `files: []` to use whatever the stack resolves to on the box.
* **`name`** — the brand tooltip; **`appId`** — the Windows taskbar identity.
* **`colors`** *(optional)* — tweak the page/surface palette per theme without
  touching CSS. Any subset of `bg`, `surface`, `surface2`, `fg`, `fgMuted`,
  `border` under `light` and/or `dark`; omitted keys keep the neutral defaults:

  ```json
  "colors": {
    "light": { "bg": "#f7f8f9", "surface": "#ffffff" },
    "dark":  { "bg": "#101418", "surface": "#1a2230" }
  }
  ```

At launch `webgui.py` reads the pack and injects a `:root` accent/font override
plus the `@font-face` rules over `app.css`'s white-label defaults. The
Claude-coral app icon and the in-app spark accent ride along on every brand
(they mark this as a Claude tool) and aren't part of the pack.

## Extending

Want to schedule a different LLM CLI (Codex, aider, the `llm` CLI, …) instead of
just Claude, or rename the project for your own fork? See
[`docs/EXTENDING.md`](docs/EXTENDING.md) — it maps the "tool profile" design
(declare a tool's flags as data) and a rename checklist that flags which bits are
cosmetic vs. load-bearing. The default terminal is already auto-detected
(wezterm → Windows Terminal → console), so a fresh clone works without assuming
any particular terminal — pin one in `settings.json` to override.

## How it schedules (security-relevant — managed endpoint)

On a managed Windows endpoint (Defender + AMSI), to avoid tripping PowerShell
script scanning:

* Tasks are registered **only via native `schtasks.exe /Create /XML`**
  (under the `\ClaudeAt\` task folder). No PowerShell at schedule time.
* The scheduled action is **signed binaries only, no script hosts**:
  `wezterm-gui.exe start --cwd <dir> -- <tool.exe> <args>` (or the tool exe
  directly for terminal=console). Codex resolves to the vendored native
  `codex.exe`, not the npm `codex.cmd` shim. No PowerShell / cmd / Python in the
  fire-time chain — nothing for AMSI to scan, minimal behavioral surface.
* Python (this tool) only runs when *you* open the GUI/CLI.

## Laptop-lid / Modern Standby behavior

Task settings mirror a proven daily task (`StartWhenAvailable`, battery
overrides, `WakeToRun`), which fire reliably **from inside Modern Standby**
even though `WakeToRun` is nominally dead under S0ix. Differences for
interactive use: `ExecutionTimeLimit` disabled (Task Scheduler must never kill
a long-lived terminal) and `MultipleInstancesPolicy=Parallel`.

Notes:

* If the machine is **fully off** at fire time, a one-shot still fires on
  next boot within `missed_run_window_hours` (default 25), then expires.
* If the screen is **locked** when a job fires, the terminal launches in
  your session and is waiting for you at unlock; claude runs regardless.
* One-shot tasks self-delete ~1 h after their catch-up window
  (`DeleteExpiredTaskAfter`) unless "Keep one-shot task after run" is set;
  `Prune done` clears finished entries from `jobs.json`.

## Files

| File | Purpose |
|---|---|
| `claude_at.py` | thin CLI / entrypoint — argument parsing + dispatch into `catcore` |
| `catcore/` | GUI-free backend package (imports no UI toolkit): |
| &nbsp;&nbsp;`config.py` | constants, `DEFAULT_SETTINGS`, settings/jobs persistence |
| &nbsp;&nbsp;`paths.py` | resolve Claude, native Codex, and terminal executables |
| &nbsp;&nbsp;`sessions.py` | session discovery from `~/.claude/projects` and `~/.codex/sessions` |
| &nbsp;&nbsp;`jobmodel.py` | pure job-dict helpers (identity, descriptions, `next_fire`, `make_job`) |
| &nbsp;&nbsp;`taskxml.py` | build Claude/Codex commands + Task Scheduler XML (pure) |
| &nbsp;&nbsp;`scheduler.py` | `schtasks.exe` wrappers + job register/delete/prune (side effects) |
| `webgui.py` | default GUI — pywebview `Api` bridge + self-contained HTML composer |
| `webui/` | front-end: `index.html`, `app.css`, `app.js`, and `brand/` (the active **brand pack** — `brand.json` + logo/fonts/icons; see Theming) |
| `tkgui.py` | legacy tkinter GUI (fallback via `gui --tk`); superseded by the web GUI |
| `claude-at.cmd` / `ClaudeAt-GUI.cmd` | console / windowless shims |
| `jobs.json` | job store (created at runtime; git-ignored) |
| `settings.json` | optional defaults override (model, mode, terminal, paths, `sessions_days`, `missed_run_window_hours`) — see `DEFAULT_SETTINGS` in `catcore/config.py` (git-ignored) |
| `tests/` | stdlib `unittest` suite for the pure backend core (`jobmodel`, `taskxml`) |

## Tests

Pure-logic unit tests (no scheduler, no disk — `next_fire` takes an injectable
clock and the executable resolvers are patched), so they run anywhere in
milliseconds:

```
python -m unittest discover -s tests -v
```

They cover `next_fire` scheduling math (once catch-up window, daily/weekly
rollover), `build_claude_args` Windows-path quoting, the trigger/task XML, and
the **security invariant** that the fire-time action is a signed binary only
(wezterm / wt / claude) — never PowerShell, cmd, or Python.

## Requirements

* Python 3.10+ (3.14 installed).
* `pip install -r requirements.txt` for the web GUI (pulls `pythonnet`; uses the
  **WebView2 runtime**, which ships with Windows 11). Falls back to the
  built-in tkinter GUI (`gui --tk`) if pywebview is missing.
* `claude` and/or `codex` on PATH, depending on which tool you schedule.
* WezTerm and/or Windows Terminal for those terminal options.
