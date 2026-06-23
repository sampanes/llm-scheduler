/* claude-at front-end. Talks to the Python backend over the pywebview js_api
   bridge (no HTTP). Degrades to mock data when opened in a plain browser so the
   layout can be previewed without the app running. */
(function () {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // ---- icons (window.ICONS injected by webgui.py) ----
  const ICONS = window.ICONS || {};
  function paintIcons(root = document) {
    $$("[data-icon]", root).forEach((el) => {
      const k = el.dataset.icon;
      if (ICONS[k]) el.innerHTML = ICONS[k];
    });
  }

  // ---- backend bridge (or mock) ----
  const MOCK = {
    defaults: () => ({
      tools: [
        { id: "claude", label: "Claude Code", models: ["fable", "opus", "sonnet", "haiku"],
          modes: ["auto", "acceptEdits", "bypassPermissions", "dontAsk", "plan", "default"],
          mode_label: "Permissions", model: "opus", mode: "auto",
          supports_sessions: true, supports_window: true },
        { id: "codex", label: "Codex CLI", models: ["default", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "codex-auto-review"],
          modes: ["default", "untrusted", "on-request", "never", "on-failure"],
          mode_label: "Approval", model: "default", mode: "default",
          supports_sessions: true, supports_window: true },
      ],
      tool: "claude",
      models: ["fable", "opus", "sonnet", "haiku"],
      modes: ["auto", "acceptEdits", "bypassPermissions", "dontAsk", "plan", "default"],
      terminals: ["wezterm", "wt", "console"],
      efforts: ["low", "medium", "high", "xhigh", "max"],
      days: ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
      model: "opus", mode: "auto", terminal: "wezterm", effort: "",
      default_dir: "C:\\Users\\you\\Documents\\Projects", sessions_days: 14,
    }),
    scan: () => ([
      { id: "11111111-1111-4111-8111-111111111111", dir: "C:\\Projects\\acme-api",
        title: "Investigate the failing integration tests", mtime: new Date(Date.now() - 7.2e6).toISOString(), active: true },
      { id: "22222222-2222-4222-8222-222222222222", dir: "C:\\Projects\\acme-web",
        title: "Refactor the dashboard data layer", mtime: new Date(Date.now() - 9e7).toISOString(), active: false },
      { id: "33333333-3333-4333-8333-333333333333", dir: "C:\\Projects\\notes",
        title: "Draft the release notes for v2.0", mtime: new Date(Date.now() - 1.7e8).toISOString(), active: false },
    ]),
    list_jobs: () => ([
      { id: "81c23ed0", name: "nightly-integration", next: new Date(Date.now() + 8.6e7).toISOString(),
        schedule_disp: "daily @ 06:45", target_disp: "resume 11111111… in C:\\Projects\\acme-api",
        model: "opus", mode: "auto", terminal: "wezterm", status: "Ready" },
    ]),
    preview: (f) => ({ ok: true, action: "wezterm-gui.exe", args: "start --cwd … -- claude …",
      workdir: f.dir, next: "(preview)", xml: "<Task>… (mock preview) …</Task>" }),
    // State-changing actions must NOT fake success in preview/mock mode — doing
    // so once told the user "Scheduled!" while nothing real was created, so they
    // clicked again fearing a double-run. Mock writes now refuse, loudly.
    schedule: () => ({ error: NOT_CONNECTED }),
    update: () => ({ error: NOT_CONNECTED }),
    delete: () => ({ error: NOT_CONNECTED }),
    run_now: () => ({ error: NOT_CONNECTED }),
    prune: () => ({ error: NOT_CONNECTED }),
    browse: () => ({ path: null }),
    browse_file: () => ({ path: null }),
    latest_screenshot: () => ({           // sample so the preview shows the append
      path: "C:\\Users\\you\\Pictures\\Screenshots\\Screenshot 2026-06-18 111623.png",
      name: "Screenshot 2026-06-18 111623.png" }),
    window_slots: () => {                 // demo grid so preview shows the chips
      const p = (x) => String(x).padStart(2, "0");
      const fmt = (d) => ({ date: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`,
        time: `${p(d.getHours())}:${p(d.getMinutes())}` });
      const base = new Date(Date.now() + 73 * 60000);  // ~73 min out
      const mk = (h, label) => { const f = fmt(new Date(base.getTime() + h * 3.6e6));
        return { label, date: f.date, time: f.time, iso: `${f.date}T${f.time}`, offset_h: h }; };
      const b = fmt(base);
      return { ok: true, active: true, error: null, anchor_iso: `${b.date}T${b.time}`,
        slots: [mk(0, "Next window"), mk(5, "+5h"), mk(10, "+10h")] };
    },
  };
  const NOT_CONNECTED =
    "Preview mode — NOT connected to the claude-at backend, so this did nothing. " +
    "Launch the claude-at app (not the browser preview) to schedule for real.";
  let API = null;            // resolved after pywebviewready
  const isMock = () => API === MOCK;
  function call(method, ...args) {
    const api = API || MOCK;
    try {
      const r = api[method](...args);
      return Promise.resolve(r);
    } catch (e) { return Promise.reject(e); }
  }

  // Append an absolute path to the kick-off prompt on its own line (used by both
  // the "Latest screenshot" and "Add file path…" buttons), then park the cursor
  // at the end so the user can keep typing.
  function appendToPrompt(path) {
    const ta = $("#f-prompt");
    const cur = ta.value.replace(/\s+$/, "");
    ta.value = cur ? cur + "\n" + path : path;
    ta.focus();
    ta.selectionStart = ta.selectionEnd = ta.value.length;
  }

  // ---- status / toast ----
  const statusEl = $("#status"), statusSpin = $("#status-spin");
  function status(msg, busy = false) { statusEl.textContent = msg; statusSpin.style.display = busy ? "" : "none"; }
  let toastT = null;
  function toast(msg, isErr = false) {
    const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (isErr ? " err" : "");
    clearTimeout(toastT); toastT = setTimeout(() => { t.className = "toast"; }, 3200);
  }

  // ---- theme ----
  function setTheme(v) {
    document.documentElement.dataset.theme = v;
    $$("#theme-toggle button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themeVal === v)));
    try { localStorage.setItem("cat-theme", v); } catch (_) {}
  }

  // ---- relative time ----  (hours carry minutes, e.g. "in 5h 30m", so the
  // buffer before a run is visible at a glance rather than rounded to a bare hour)
  function rel(iso) {
    if (!iso) return "";
    const ms = new Date(iso) - new Date(), a = Math.abs(ms) / 1000;
    let txt;
    if (a < 90) txt = `${Math.round(a)}s`;
    else if (a < 5400) txt = `${Math.round(a / 60)}m`;          // < 90 min
    else if (a < 1.3e5) {                                       // < ~36 h: h + m
      let h = Math.floor(a / 3600), m = Math.round((a % 3600) / 60);
      if (m === 60) { h++; m = 0; }
      txt = m ? `${h}h ${m}m` : `${h}h`;
    } else {                                                    // days + h
      let d = Math.floor(a / 86400), h = Math.round((a % 86400) / 3600);
      if (h === 24) { d++; h = 0; }
      txt = h ? `${d}d ${h}h` : `${d}d`;
    }
    return ms < 0 ? `${txt} ago` : `in ${txt}`;
  }
  // dates: MM/DD/YY + 24h "military" time
  const _p2 = (x) => String(x).padStart(2, "0");
  function fmtDate(iso) {                 // "06/18/26 18:12"
    if (!iso) return "expired";
    const d = new Date(iso);
    return `${_p2(d.getMonth() + 1)}/${_p2(d.getDate())}/${_p2(d.getFullYear() % 100)}`
      + ` ${_p2(d.getHours())}:${_p2(d.getMinutes())}`;
  }
  function fmtShort(iso) {                // "06/18 18:12" (sessions; year dropped for width)
    if (!iso) return "";
    const d = new Date(iso);
    return `${_p2(d.getMonth() + 1)}/${_p2(d.getDate())} ${_p2(d.getHours())}:${_p2(d.getMinutes())}`;
  }
  function todayStr() { const d = new Date();   // YYYY-MM-DD, for comparing to slot.date
    return `${d.getFullYear()}-${_p2(d.getMonth() + 1)}-${_p2(d.getDate())}`; }
  function mdy(s) { const [y, m, d] = s.split("-"); return `${m}/${d}/${y.slice(2)}`; }  // -> 06/18/26
  function md(s) { const [, m, d] = s.split("-"); return `${m}/${d}`; }                  // -> 06/18
  // chip colour for a job's task status (labels come from the backend job_status):
  // a fired one-shot is green ("Ran"/"Ran (kept)"), a failed one red, a running
  // job the brand accent, everything else (Ready/MISSING) neutral.
  function statusClass(s) {
    s = s || "";
    if (s.indexOf("Ran") === 0) return "chip-done";
    if (s.indexOf("Failed") === 0) return "chip-err";
    if (s === "Running") return "chip-accent";
    return "";
  }

  // ---- form state ----
  const state = { tool: "claude", target: "continue", session: "", sessionTitle: "", dir: "", sessions: [], tools: [],
    origin: null, originLabel: "" };

  function toolDef(id = state.tool) {
    return state.tools.find((t) => t.id === id) || state.tools[0] || {
      id: "claude", label: "Claude Code", models: [], modes: [],
      mode_label: "Permissions", supports_sessions: true, supports_window: true,
    };
  }

  function fillOptions(sel, arr, val) {
    $(sel).innerHTML = (arr || []).map((x) => `<option${x === val ? " selected" : ""}>${esc(x)}</option>`).join("");
  }

  function applyToolFields(id) {
    const t = toolDef(id);
    state.tool = t.id;
    $("#f-model-label").textContent = "Model";
    $("#f-mode-label").textContent = t.mode_label || "Mode";
    fillOptions("#f-model", t.models || [], t.model || (t.models || [])[0] || "");
    fillOptions("#f-mode", t.modes || [], t.mode || (t.modes || [])[0] || "");
    // --effort is claude-only: hide + clear the field for codex.
    const effCol = $("#f-effort").closest(".field-col");
    if (effCol) effCol.style.display = t.id === "codex" ? "none" : "";
    if (t.id === "codex") $("#f-effort").value = "";
    $("#win-lead").textContent = t.id === "codex" ? "Codex 5-hour window" : "Claude 5-hour window";
    $("#btn-win-refresh").title = `Re-check your current ${t.label || t.id} usage window`;
  }

  function updateTargetDesc() {
    const d = esc(state.dir || "—");
    const label = esc(toolDef().label || state.tool);
    let html;
    if (state.target === "resume" && state.session) {
      const t = state.sessionTitle || (state.session.slice(0, 8) + "…");
      html = `${label}: resume <b>"${esc(t.slice(0, 64))}"</b> in <b>${d}</b>`;
    } else if (state.target === "new") {
      html = `${label}: <b>new</b> session in <b>${d}</b>`;
    } else {
      html = `${label}: <b>continue</b> latest session in <b>${d}</b>`;
    }
    $("#target-desc").innerHTML = html;
  }

  // ---- sessions ----
  function renderSessions() {
    const list = $("#sess-list");
    const needle = $("#sess-search").value.trim().toLowerCase();
    const wantDir = $("#sess-dir").value;
    const rows = state.sessions.filter((s) => {
      if (wantDir && s.dir.toLowerCase() !== wantDir.toLowerCase()) return false;
      if (needle && !`${s.id} ${s.dir} ${s.title}`.toLowerCase().includes(needle)) return false;
      return true;
    });
    $("#sess-count").textContent = `(${rows.length})`;
    if (!rows.length) { list.innerHTML = `<div class="empty">No sessions match.</div>`; return; }
    list.innerHTML = rows.map((s) => `
      <div class="sess-row${s.id === state.session ? " selected" : ""}" data-id="${esc(s.id)}" tabindex="0">
        <span class="live-dot${s.active ? "" : " idle"}" title="${s.active ? "running now" : ""}"></span>
        <span class="when">${esc(fmtShort(s.mtime))}</span>
        <div class="who">
          <div class="title">${esc(s.title || "(untitled)")}</div>
          <div class="dir">${esc(s.dir)}</div>
        </div>
        ${s.active ? `<span class="chip chip-live"><span class="dot"></span>live</span>` : `<span></span>`}
      </div>`).join("");
    $$(".sess-row", list).forEach((row) => {
      row.addEventListener("click", () => pickSession(row.dataset.id));
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") pickSession(row.dataset.id); });
    });
  }

  function pickSession(id) {
    const s = state.sessions.find((x) => x.id === id);
    if (!s) return;
    state.target = "resume"; state.session = id; state.sessionTitle = s.title; state.dir = s.dir;
    $("#f-session").value = id; $("#f-dir").value = s.dir;
    $(`input[name=target][value=resume]`).checked = true;
    if (s.title) $("#f-name").value = sanitize(s.title.slice(0, 40));
    renderSessions(); updateTargetDesc();
    status(`targeting session ${id.slice(0, 8)}…`);
  }

  function sanitize(name) {
    return (name.replace(/[^A-Za-z0-9 _.-]/g, "").trim().replace(/\s+/g, "-").slice(0, 60)) || "job";
  }

  async function refreshSessions() {
    const days = parseInt($("#sess-days").value || "0", 10) || 0;
    $("#sess-spin").style.display = ""; status("scanning sessions…", true);
    $("#overlay").classList.add("show");
    try {
      const rows = await call("scan", days, state.tool);
      state.sessions = rows || [];
      const dirs = Array.from(new Set(state.sessions.map((s) => s.dir))).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
      const sel = $("#sess-dir");
      sel.innerHTML = `<option value="">All</option>` + dirs.map((d) => `<option value="${esc(d)}">${esc(d)}</option>`).join("");
      renderSessions();
      status(`${state.sessions.length} ${toolDef().label || state.tool} sessions${isMock() ? " (mock data — open in the app for real ones)" : ""}`);
    } catch (e) {
      status("scan failed"); toast("Session scan failed: " + e, true);
    } finally {
      $("#sess-spin").style.display = "none"; $("#overlay").classList.remove("show");
    }
  }

  // ---- pending jobs ----
  async function refreshPending() {
    status("loading pending runs…", true);
    try {
      const jobs = await call("list_jobs");
      const list = $("#pend-list");
      $("#pend-count").textContent = `(${jobs.length})`;
      if (!jobs.length) { list.innerHTML = `<div class="empty">No scheduled runs. Pick a session above and hit Schedule.</div>`; status("no pending runs"); return; }
      list.innerHTML = jobs.map((j) => `
        <div class="job" data-id="${esc(j.id)}">
          <div class="next">${esc(fmtDate(j.next))}<span class="rel">${esc(rel(j.next))}</span></div>
          <div class="meta">
            <div class="name">${esc(j.label)}</div>
            <div class="sub">${esc(j.schedule_disp)} · ${esc(j.target_disp)}</div>
            <div class="tags">
              <span class="chip">${esc(j.tool || "claude")}</span>
              <span class="chip">${esc(j.model)}</span>
              <span class="chip">${esc(j.mode)}</span>
              <span class="chip">${esc(j.terminal)}</span>
              <span class="chip ${statusClass(j.status)}">${esc(j.status)}</span>
            </div>
          </div>
          <div class="job-actions">
            <button class="btn btn-secondary btn-sm" data-act="run">Run now</button>
            <button class="btn btn-ghost btn-sm" data-act="load">Load</button>
            <button class="btn btn-ghost btn-sm" data-act="del" title="Delete"><span class="ico ico-sm" data-icon="close"></span></button>
          </div>
        </div>`).join("");
      paintIcons(list);
      $$(".job", list).forEach((row) => {
        const id = row.dataset.id;
        row.querySelector('[data-act=run]').onclick = () => runNow(id);
        row.querySelector('[data-act=load]').onclick = () => loadJob(id, jobs.find((x) => x.id === id));
        row.querySelector('[data-act=del]').onclick = () => delJob(id, jobs.find((x) => x.id === id));
      });
      status(`${jobs.length} pending run(s)`);
    } catch (e) { status("failed to load jobs"); toast("" + e, true); }
  }

  async function runNow(id) {
    const r = await call("run_now", id);
    if (r && r.ok) { toast("Fired now."); status("fired " + id); }
    else toast((r && r.error) || "run failed", true);
  }
  async function delJob(id, j) {
    if (!confirm(`Delete this scheduled run?\n\n${j ? j.label : id}\n${j ? j.schedule_disp : ""}`)) return;
    const r = await call("delete", id);
    if (r && r.ok) { toast("Deleted."); refreshPending(); }
    else toast((r && r.error) || "delete failed", true);
  }
  function loadJob(id, j) {
    if (!j) return;
    state.tool = j.tool || "claude";
    $("#f-tool").value = state.tool;
    applyToolFields(state.tool);
    state.target = j.target_mode || "continue"; state.session = j.session_id || ""; state.dir = j.dir || "";
    $(`input[name=target][value=${state.target}]`).checked = true;
    $("#f-session").value = state.session; $("#f-dir").value = state.dir;
    $("#f-model").value = j.model; $("#f-mode").value = j.mode; $("#f-effort").value = j.effort || ""; $("#f-term").value = j.terminal;
    $("#f-name").value = j.name; $("#f-prompt").value = j.prompt || ""; $("#f-extra").value = j.extra_args || "";
    $("#f-net").checked = j.require_network !== false; $("#f-keep").checked = j.delete_after_run === false;
    const s = j.schedule || {};
    setWhen(s.type || "once");
    if (s.type === "once" && s.datetime) { const [d, t] = s.datetime.split("T"); $("#f-date").value = d; $("#f-time").value = (t || "").slice(0, 5); }
    else if (s.time) $("#f-time").value = s.time;
    $$("#days-row .day-btn").forEach((b) => b.setAttribute("aria-pressed", String(s.type === "weekly" && (s.days || []).includes(b.dataset.day))));
    openAdv(true); updateTargetDesc();
    refreshSessions();
    loadWindowSlots(false, true);
    state.origin = id; state.originLabel = j.label; renderOrigin();
    status(`editing ${j.label} — Replace saves changes, Schedule makes a new copy`);
  }

  // Load establishes an "origin": the job the form was copied from. While one is
  // set, the form shows an "editing <job>" pill and a Replace button (update in
  // place); Schedule still makes a NEW job (the copy case). Clearing the origin
  // — the pill's ✕, the Clear button, or any successful submit — returns to the
  // plain create-from-scratch state, so the destructive in-place edit only ever
  // appears in the one context where it's meaningful.
  function renderOrigin() {
    const editing = !!state.origin;
    $("#origin-bar").hidden = !editing;
    $("#btn-replace").hidden = !editing;
    if (editing) $("#origin-name").textContent = state.originLabel || state.origin;
    const sch = $("#btn-schedule");
    sch.textContent = editing ? "Schedule as new" : "Schedule";
    sch.classList.toggle("btn-primary", !editing);
    sch.classList.toggle("btn-secondary", editing);
  }
  function clearOrigin() { state.origin = null; state.originLabel = ""; renderOrigin(); }

  // ---- gather + schedule/preview ----
  function gatherForm() {
    const when = ($('input[name=when]:checked') || {}).value || "once";
    return {
      tool: state.tool,
      target: ($('input[name=target]:checked') || {}).value || "continue",
      session: $("#f-session").value.trim(),
      dir: $("#f-dir").value.trim(),
      when,
      date: $("#f-date").value,
      time: $("#f-time").value.trim(),
      days: $$("#days-row .day-btn[aria-pressed=true]").map((b) => b.dataset.day),
      model: $("#f-model").value, mode: $("#f-mode").value, effort: $("#f-effort").value, terminal: $("#f-term").value,
      name: $("#f-name").value.trim(), prompt: $("#f-prompt").value.trim(),
      extra: $("#f-extra").value.trim(),
      net: $("#f-net").checked, keep: $("#f-keep").checked,
    };
  }
  async function doSchedule() {
    const r = await call("schedule", gatherForm());
    if (r && r.ok) { toast(`Scheduled: ${r.name} (${r.desc})`); status(`scheduled ${r.name}-${r.id}`); clearOrigin(); refreshPending(); }
    else toast((r && r.error) || "could not schedule", true);
  }
  async function doReplace() {
    if (!state.origin) return doSchedule();   // nothing loaded -> behave like Schedule
    const r = await call("update", state.origin, gatherForm());
    if (r && r.ok) { toast(`Updated: ${r.name} (${r.desc})`); status(`updated ${r.name}`); clearOrigin(); refreshPending(); }
    else toast((r && r.error) || "could not update", true);
  }
  async function doPreview() {
    const r = await call("preview", gatherForm());
    if (!r || !r.ok) { toast((r && r.error) || "preview failed", true); return; }
    $("#preview-pre").textContent =
      `Action : ${r.action}\nArgs   : ${r.args}\nWorkdir: ${r.workdir}\nNext   : ${r.next}\n\n${r.xml}`;
    $("#modal").classList.add("show");
  }

  // ---- 5-hour-window quick-pick ----
  // Each chip is a fire time computed from the live usage window: slot 0 is one
  // minute into the next window (or "Open now" when idle), and +5h/+10h chain
  // successive windows. Clicking one fills the one-shot date+time for you.
  function applySlot(slot, announce = true) {
    if (!slot) return;
    $("#f-time").value = slot.time;
    $("#f-date").value = slot.date;
    setWhen("once");
    if (!announce) return;
    toast(`One-shot set: ${mdy(slot.date)} ${slot.time}`);
    status(`scheduled time set to ${mdy(slot.date)} ${slot.time}`);
  }
  function renderWindowSlots(res) {
    const wrap = $("#win-slots"), chips = $("#win-chips"), note = $("#win-note");
    if (!res || !res.ok) {                 // fetch failed — keep manual entry
      chips.innerHTML = "";
      note.className = "win-note err";
      note.textContent = res && res.error ? res.error : "window unavailable";
      wrap.hidden = false;
      return null;
    }
    const today = todayStr();
    chips.innerHTML = (res.slots || []).map((s, i) => {
      const cross = s.date !== today ? ` · ${esc(md(s.date))}` : "";
      return `<button type="button" class="win-chip${i === 0 ? " primary" : ""}" data-i="${i}"`
        + ` title="${esc(s.label)} — fires ${esc(s.date)} ${esc(s.time)} (1 min into the window)">`
        + `${esc(s.label)} <span class="when">${esc(s.time)}${cross}</span></button>`;
    }).join("");
    $$(".win-chip", chips).forEach((b) => b.onclick = () => applySlot(res.slots[+b.dataset.i]));
    note.className = "win-note";
    $("#win-lead").textContent = res.label || $("#win-lead").textContent;
    note.textContent = res.active ? ""
      : "no window open — “Open now” starts a fresh 5-hour window";
    wrap.hidden = false;
    return (res.slots || [])[0] || null;
  }
  async function loadWindowSlots(applyDefault, refresh = false) {
    try {
      const res = await call("window_slots", state.tool, refresh);
      const first = renderWindowSlots(res);
      if (applyDefault && first) applySlot(first, false);  // silent: it's the default
    } catch (e) {
      renderWindowSlots({ ok: false, error: "" + e });
    }
  }

  // ---- advanced + when ----
  function openAdv(open) { $("#adv").classList.toggle("open", open); $("#adv-toggle").setAttribute("aria-expanded", String(open)); }
  function setWhen(v) {
    const r = $(`input[name=when][value=${v}]`); if (r) r.checked = true;
    $("#f-date").style.display = v === "once" ? "" : "none";
    $("#days-row").style.display = v === "weekly" ? "" : "none";
    $("#btn-mf").style.display = v === "weekly" ? "" : "none";
  }

  // ---- init ----
  async function init() {
    $$("#theme-toggle button").forEach((b) => b.onclick = () => setTheme(b.dataset.themeVal));

    const d = await call("defaults");
    state.tools = d.tools || [];
    state.tool = d.tool || "claude";
    fillOptions("#f-tool", state.tools.map((t) => t.id), state.tool);
    $$("#f-tool option").forEach((o) => {
      const t = toolDef(o.value);
      o.textContent = t.label || o.value;
    });
    // Effort is claude-only; fill it before applyToolFields, which hides/clears
    // it for codex (model/mode are filled per-tool inside applyToolFields).
    fillOptions("#f-effort", [""].concat(d.efforts || []), d.effort || "");
    applyToolFields(state.tool);
    fillOptions("#f-term", d.terminals, d.terminal);
    $("#sess-days").value = d.sessions_days;
    state.dir = d.default_dir; $("#f-dir").value = d.default_dir;
    $("#days-row").innerHTML = d.days.map((dy) => `<button type="button" class="day-btn" data-day="${dy}" aria-pressed="false">${dy}</button>`).join("");
    $$("#days-row .day-btn").forEach((b) => b.onclick = () => b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")));

    // default time = next top of hour
    const now = new Date(); now.setHours(now.getHours() + 1, 0, 0, 0);
    const p = (x) => String(x).padStart(2, "0");
    $("#f-time").value = `${p(now.getHours())}:${p(now.getMinutes())}`;
    $("#f-date").value = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;

    // wiring
    $("#sess-search").addEventListener("input", renderSessions);
    $("#sess-dir").addEventListener("change", renderSessions);
    $("#f-tool").onchange = async () => {
      state.tool = $("#f-tool").value || "claude";
      state.target = "continue"; state.session = ""; state.sessionTitle = "";
      $("#f-session").value = ""; $(`input[name=target][value=continue]`).checked = true;
      applyToolFields(state.tool); updateTargetDesc();
      await refreshSessions();
      loadWindowSlots(true, true);
    };
    $("#btn-refresh").onclick = refreshSessions;
    $("#btn-pend-refresh").onclick = refreshPending;
    $("#btn-clear").onclick = () => { state.target = "continue"; state.session = ""; state.sessionTitle = ""; state.dir = d.default_dir;
      $("#f-session").value = ""; $("#f-dir").value = d.default_dir; $(`input[name=target][value=continue]`).checked = true; clearOrigin(); renderSessions(); updateTargetDesc(); };
    $("#adv-toggle").onclick = () => openAdv(!$("#adv").classList.contains("open"));
    $$('input[name=target]').forEach((r) => r.onchange = () => { state.target = r.value; if (r.value !== "resume") { state.session = ""; $("#f-session").value = ""; } updateTargetDesc(); });
    $("#f-dir").addEventListener("input", () => { state.dir = $("#f-dir").value; updateTargetDesc(); });
    $("#f-session").addEventListener("input", () => { state.session = $("#f-session").value.trim(); updateTargetDesc(); });
    $$('input[name=when]').forEach((r) => r.onchange = () => setWhen(r.value));
    $("#btn-mf").onclick = () => $$("#days-row .day-btn").forEach((b) => b.setAttribute("aria-pressed", String(["MON", "TUE", "WED", "THU", "FRI"].includes(b.dataset.day))));
    $("#btn-browse").onclick = async () => { const r = await call("browse", $("#f-dir").value); if (r && r.path) { $("#f-dir").value = r.path; state.dir = r.path; updateTargetDesc(); } };
    $("#btn-attach").onclick = async () => {
      const r = await call("browse_file", $("#f-dir").value);
      if (!r || !r.path) return;  // cancelled, or mock/preview (no window) -> no-op, like Browse…
      appendToPrompt(r.path);
    };
    $("#btn-shot").onclick = async () => {
      const r = await call("latest_screenshot");
      if (!r || !r.path) { if (r && r.error) toast(r.error, true); return; }
      appendToPrompt(r.path);
      toast("Added latest screenshot: " + (r.name || r.path));
    };
    $("#btn-schedule").onclick = doSchedule;
    $("#btn-replace").onclick = doReplace;
    $("#origin-clear").onclick = clearOrigin;
    $("#btn-preview").onclick = doPreview;
    $("#btn-win-refresh").onclick = () => loadWindowSlots(false, true);
    $("#btn-prune").onclick = async () => { const r = await call("prune"); if (r && r.error) { toast(r.error, true); return; } toast(`Pruned ${r ? r.count : 0} job(s).`); refreshPending(); };
    $$("[data-close-modal]").forEach((b) => b.onclick = () => $("#modal").classList.remove("show"));
    $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.remove("show"); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("#modal").classList.remove("show"); });

    setWhen("once"); updateTargetDesc();
    await Promise.all([refreshSessions(), refreshPending()]);
    // Window-aware default time: fill the one-shot with "1 min into the next 5h
    // window" once the usage API answers. Non-blocking — chips + default appear
    // when ready; the top-of-hour default above stands in until then.
    loadWindowSlots(true);
  }

  // Brand assets + saved theme need no backend, so paint them on the FIRST
  // frame instead of waiting for the bridge — otherwise the shell flashes
  // logo-less (and light-themed) until Python attaches. The boot veil
  // (#overlay, shown in the markup) covers the rest of the un-populated shell.
  paintIcons();
  try { setTheme(localStorage.getItem("cat-theme") || "light"); } catch (_) { setTheme("light"); }

  // pywebview attaches the window.pywebview.api OBJECT before it attaches the
  // method functions, so we must check for an actual method, not just the
  // object, or init() fires before defaults()/scan() exist.
  function bridgeReady() {
    return !!(window.pywebview && window.pywebview.api &&
              typeof window.pywebview.api.defaults === "function");
  }
  let booted = false;
  function boot() {
    if (booted) return;
    booted = true;
    API = bridgeReady() ? window.pywebview.api : MOCK;
    window.__CAT__ = { mode: isMock() ? "mock" : "bridge" };
    if (isMock()) showMockBanner();
    init();
  }
  // A loud, persistent banner so mock/preview data is never mistaken for the
  // real backend. The sessions + pending runs shown in this mode are fake, and
  // every write refuses (see MOCK), so nothing here touches Task Scheduler.
  function showMockBanner() {
    status("PREVIEW MODE — mock data. Not connected to claude-at; scheduling is disabled.");
    document.documentElement.dataset.mock = "1";
    if ($("#mock-banner")) return;
    const b = document.createElement("div");
    b.id = "mock-banner";
    b.textContent = "PREVIEW — mock data, NOT connected to the claude-at backend. " +
      "Sessions & pending runs below are fake; scheduling does nothing. " +
      "Launch the claude-at app for the real thing.";
    b.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:99999;" +
      "background:#b00020;color:#fff;font:600 12.5px/1.5 system-ui,sans-serif;" +
      "text-align:center;padding:7px 14px;letter-spacing:.2px;" +
      "box-shadow:0 -2px 10px rgba(0,0,0,.25)";
    document.body.appendChild(b);
  }
  // Bridge detection. pywebview injects the window.pywebview OBJECT early but
  // attaches the js_api METHOD functions later; on a COLD WebView2/pythonnet
  // start (slower still on a Defender-scanned endpoint, where the
  // window can sit "Not Responding" for a bit) that lag can run well past 10 s.
  // So we discriminate on the object:
  //   * methods attached             -> real backend, boot for real;
  //   * object never appears (3 s)   -> plain browser preview -> mock data;
  //   * object present, methods not  -> we ARE in the app: keep waiting and
  //     reassure on the veil. We deliberately NEVER fall back to mock here —
  //     showing fake sessions/jobs inside the REAL app misleads (a phantom
  //     "daily @ 06:45" run once read as a real scheduled job). Honest waiting,
  //     however long, beats fake data; a late attach still boots for real.
  function setVeil(msg, sub) {
    const m = $("#overlay .msg"), s = $("#overlay .sub");
    if (m && msg != null) m.textContent = msg;
    if (s && sub != null) s.textContent = sub;
  }
  window.addEventListener("pywebviewready", () => { if (bridgeReady()) boot(); });
  (function waitBridge(t) {
    if (booted) return;
    if (bridgeReady()) return boot();          // methods attached -> real backend
    if (!window.pywebview) {                    // object never injected ...
      if (t >= 3000) return boot();             // ... after a grace -> browser -> mock
    } else {                                    // in the app; methods still coming
      if (t === 9000)
        setVeil("Starting up…",
          "Cold start can take a moment — connecting to the backend.");
      else if (t === 30000)
        setVeil("Still connecting…",
          "Taking longer than usual. If nothing appears shortly, close this " +
          "window and relaunch from the Claude At shortcut.");
    }
    setTimeout(() => waitBridge(t + 50), 50);   // poll on; a late bridge still boots
  })(0);
})();
