# Extending & renaming this scheduler

This tool schedules **interactive Claude Code sessions** as Windows scheduled
tasks. Two enhancements come up often enough to be worth documenting up front:

1. **Supporting other LLM CLIs** (Codex, aider, the `llm` CLI, …), not just Claude.
2. **Renaming the project** to your own name.

Neither is wired up yet — this is a map of how to do them cleanly, written so a
fresh clone can pick it up without spelunking. The good news, established by a
full audit of the codebase: the hard, security-critical, machine-validated half
of the app is **already tool-agnostic**. The Claude-specific parts are small and
concentrated.

---

## Architecture: the generic engine vs. the Claude adapter

| Layer | Where | Coupling |
|---|---|---|
| schtasks wrappers (create/delete/run/query/prune) | `catcore/scheduler.py` | **Generic** — operates on task names + XML strings only. |
| Task Scheduler XML + triggers | `catcore/taskxml.py` `build_trigger_xml`, `build_task_xml` | **Generic** — only needs `(command, args, workdir)`. |
| Terminal wrapping (wezterm / wt / console) | `catcore/taskxml.py` `build_action` | **Generic** — wraps *any* exe. Terminal is auto-detected (see below). |
| GUI scaffolding + caching | `webgui.py` (`_prewarm`/`_invalidate`/`_lock`), front-end dropdowns | **Generic** — dropdowns are already fed from `Api.defaults()`, not hardcoded in JS. |
| **Argument building** | `catcore/taskxml.py` `build_claude_args` | Claude-specific (`--model`, `--permission-mode`, `--resume`, `--continue`). |
| **Executable resolution** | `catcore/paths.py` `resolve_claude` + the `claude_path` setting | Claude-specific. |
| **Model / permission lists** | `catcore/config.py` `MODELS`, `PERMISSION_MODES` | Claude-specific constants. |
| **Job shape** | `catcore/jobmodel.py` `make_job` (`model`, `permission_mode`, `target`) | Claude-shaped. |
| **Session browser** | `catcore/sessions.py` (reads `~/.claude/projects`, Claude's JSONL schema) | Strongly Claude-specific. |
| **Usage-window chips** | `catcore/window.py` (Claude's 5-hour usage API) | Claude-only feature. |

> **Already done:** the default terminal is no longer hardcoded to wezterm.
> `DEFAULT_SETTINGS["terminal"]` ships as `""` (auto), and `paths.default_terminal()`
> picks the first installed of wezterm → Windows Terminal → `console` (which needs
> nothing). Pin a terminal in `settings.json` to override. This is the template
> for how the rest of the Claude-specific bits should become *resolved*, not
> *assumed*.

---

## 1. Adding other LLM CLIs — the "tool profile" design

The clean approach mirrors how branding already works in this repo (`webui/brand/`):
**declare the tool as data, ship built-ins, let people drop in their own.** A
`tools/` directory of JSON profiles discovered at launch, with `claude` as the
built-in reference profile.

### Profile schema (proposed)

```jsonc
// tools/claude.json — the reference profile (must reproduce today's behavior exactly)
{
  "id": "claude", "label": "Claude Code",
  "exe": "claude", "exeFallbacks": ["~/.local/bin/claude.exe"], "settingsKey": "claude_path",
  "args": {
    "template": [ {"flag": "--model", "from": "model"},
                  {"flag": "--permission-mode", "from": "permissionMode"} ],
    "prompt": { "style": "positional" },   // positional | {style:"flag",flag:"-p"} | stdin
    "passthrough": "extraArgs"
  },
  "fields": {                               // which inputs the GUI shows + their options
    "model":          {"label":"Model","options":["fable","opus","sonnet","haiku"],"default":"opus"},
    "permissionMode": {"label":"Permissions","options":["auto","acceptEdits","bypassPermissions","dontAsk","plan","default"],"default":"auto"}
  },
  "targets": {                              // resume / continue / new capability
    "resume":   {"args":[{"flag":"--resume","from":"sessionId"}]},
    "continue": {"args":["--continue"]},
    "new":      {"args":[]}
  },
  "sessions": { "kind": "claude-projects" },// enables the session browser; null = hide it
  "window":   { "kind": "claude-5h" }       // enables the usage-window chips; null = hide them
}
```

```jsonc
// tools/codex.json — a tool with no resume and no session store
{
  "id":"codex","label":"Codex CLI","exe":"codex",
  "args":{"template":[{"flag":"-m","from":"model"}],"prompt":{"style":"positional"},"passthrough":"extraArgs"},
  "fields":{"model":{"label":"Model","options":["gpt-5.1-codex","o4-mini"],"default":"gpt-5.1-codex"}},
  "targets":{"new":{"args":[]}},            // only "new" — no resume/continue
  "sessions": null, "window": null
}
```

`fields` / `targets` / `sessions` / `window` map exactly onto the GUI controls
that are Claude-specific today — so the front-end just renders whatever the
active profile declares.

### Job shape + back-compat

Today a job carries fixed `model` + `permission_mode`. Generalize to:

```jsonc
{ "tool": "claude", "params": {"model":"opus","permissionMode":"auto"},
  "target": {"mode":"resume","sessionId":"…"} /* + generic dir/schedule/prompt/extraArgs/terminal */ }
```

`build_tool_args(job, profile)` walks the profile's template, substituting from
`params` / `target`. **Migration shim:** any job loaded without a `tool` key is
treated as `tool:"claude"`, mapping legacy `model`→`params.model` and
`permission_mode`→`params.permissionMode`. Existing `jobs.json` and already-
registered scheduled tasks keep working untouched. Add a **golden test** that
`build_tool_args(claude_job, claude_profile)` is byte-identical to today's
`build_claude_args` output, so the Claude path provably doesn't change.

### Security invariant (must hold from day one)

The fire-time action must stay a **real executable + an argument list — never a
script host.** This is both a safety property and what keeps endpoint antivirus
from flagging the scheduled action. Enforce it in the profile loader:

- The action stays `exe + arg-list` (already true via `subprocess.list2cmdline`).
- `resolve_tool` requires the resolved exe to be a real file (`.is_file()`).
- **Reject** any profile whose exe basename is a shell / script host
  (`cmd`, `powershell`, `pwsh`, `wscript`, `cscript`, `bash`, `sh`). Add a test.
  This keeps "front-load any tool" from becoming "front-load a shell payload."

### Suggested phasing (each phase leaves the app working + tests green)

1. **Profile core** — new `catcore/tools.py` (schema, `load_tools`, `resolve_tool`,
   `build_tool_args` + the security guard); refactor `taskxml.build_action` and
   `paths` to use it; `make_job` gains `tool` + `params`; move `MODELS` /
   `PERMISSION_MODES` into the claude profile (keep deprecated re-exports). Claude
   stays byte-identical (golden test).
2. **Bridge** — `Api.defaults()` returns a `tools` list + per-tool field schema;
   `_job_from_form` / `_build_jobs` read `tool` + `params`; `scan()` gated to
   session-capable tools. The caching layer needs no changes.
3. **Front-end** — add a Tool `<select>` (from `defaults().tools`); render the
   model / permission fields dynamically from the active profile's `fields`;
   show/hide the target radios (`targets`), session panel (`sessions`), and usage
   chips (`window`); relabel "Extra claude args" → "Extra args".
4. **Session source registry** — abstract `scan_sessions` behind a `sessions.kind`
   registry; ship `claude-projects`; tools without it show no browser. Gate the
   usage-window feature behind `window.kind == "claude-5h"`.
5. **CLI** — `add --tool` (default `claude`); `--model`/`--mode`/`--resume`/
   `--continue` become claude-profile sugar validated against the active tool;
   add a generic `--param k=v`.

When shipping a real non-Claude profile, get the tool's **exact** flag syntax
(model flag, prompt placement, any resume concept) — that's the "front-load the
flags" the whole design hinges on.

---

## 2. Renaming the project

Most occurrences of `claude-at` / `ClaudeAt` are cosmetic (docstrings, README,
window title, CSS comments) and safe to find/replace. A handful are
**load-bearing** — change them atomically with their callers, or recreate by hand:

| Item | Why it's risky | What to do |
|---|---|---|
| `TASK_FOLDER = "ClaudeAt"` (`catcore/config.py`) | It prefixes every Windows task path (`\ClaudeAt\…`). Changing it **orphans all already-registered tasks** under the old folder. | Either keep the value, or make it a setting and ship a one-time migration that re-registers existing `\ClaudeAt\*` tasks under the new folder, then deletes the old ones. |
| `claude_at.py` (filename) | Both `.cmd` shims invoke it by name. | Rename the file **and** update `claude-at.cmd` and `ClaudeAt-GUI.cmd` in the same change. |
| `claude-at.cmd` / `ClaudeAt-GUI.cmd` (filenames) | `claude-at.cmd` is the console entry point; the desktop shortcut targets `ClaudeAt-GUI.cmd` by name. | Rename together; recreate the desktop shortcut (`.lnk`) by hand — it lives on the desktop, not in the repo. |
| `catcore/` (package dir) | Every `from catcore import …` across `claude_at.py`, `webgui.py`, `tkgui.py`, and all tests depends on this name. | **Recommended: keep it.** It's purely internal, has zero user-facing value, and renaming is high-churn. Rename only if you want it pristine. |
| Default brand `name` / `appId` (`webgui.py` `_DEFAULT_BRAND`) | Drives the wordmark, the Windows taskbar identity, and the `LOCALAPPDATA\claude-at\webview` data dir (changing the dir just re-warms WebView2 once — benign). | Rename freely. Update the brand-name assertions in `tests/test_brand.py` alongside. |
| `cat-theme` localStorage key / `window.__CAT__` (`webui/app.js`) | Renaming the key drops the user's saved light/dark preference. | Keep, or migrate the key. Trivial either way. |

Everything else — the README, module/CSS/JS comments, the page `<title>`, the
boot-veil text, the CLI `prog=` name — is cosmetic.
