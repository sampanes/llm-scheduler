"""Legacy tkinter GUI for claude-at.

Preserved verbatim from the original monolith during the Phase 1 refactor so
the tool keeps a working GUI while the pywebview web GUI is built.
Reachable via `claude-at.cmd gui --tk`. All backend logic comes from catcore.
"""

from datetime import datetime, timedelta
from pathlib import Path
from subprocess import list2cmdline

from catcore import (
    scan_sessions, load_jobs, find_job, make_job, build_task_xml, build_action,
    register_job, delete_job, task_run, prune_jobs, task_query_all, next_fire,
    describe_target, describe_schedule, sanitize_name, task_name_for,
    UUID_RE, PERMISSION_MODES, MODELS, TERMINALS, DAY_ORDER, default_terminal,
)


def run_gui(settings, smoke=False):
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    root = tk.Tk()
    root.title("claude-at — schedule Claude session resumes")
    root.geometry("1080x800")
    if smoke:
        root.withdraw()

    status_var = tk.StringVar(value="ready")

    def set_status(msg):
        status_var.set(msg)
        root.update_idletasks()

    def with_loading_curtain(work, message="Loading…"):
        """Paint a full-window 'loading' overlay, run blocking `work`, remove it.

        scan_sessions() reads every ~/.claude/projects/*.jsonl and takes ~20s,
        during which the single-threaded UI is frozen. The curtain is painted
        (root.update) BEFORE the blocking call, so the user sees a clear
        loading message + a reminder that the window is intentionally
        unresponsive until it finishes — rather than a blank/ghosted window.
        """
        curtain = tk.Frame(root, background="#1a5276")
        curtain.place(relx=0, rely=0, relwidth=1, relheight=1)
        box = tk.Frame(curtain, background="#1a5276")
        box.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(box, text=message, font=("Segoe UI", 28, "bold"),
                 foreground="white", background="#1a5276").pack(pady=(0, 16))
        tk.Label(box, justify="center", font=("Segoe UI", 11),
                 foreground="white", background="#1a5276", text=(
                     "Reading your Claude sessions — this takes about 20 seconds.\n\n"
                     "The window is FROZEN until it finishes.\n"
                     "Don't drag, resize, or click it — Windows may flash\n"
                     "\"Not Responding,\" but it's working and will catch up\n"
                     "the instant loading completes.")).pack()
        root.update()  # map + paint the curtain before the blocking scan
        try:
            work()
        finally:
            curtain.destroy()

    # ---------------- sessions pane ----------------
    sess_frame = ttk.LabelFrame(root, text="Sessions  (double-click to target; * = active in last 10 min)")
    sess_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))

    filter_row = ttk.Frame(sess_frame)
    filter_row.pack(fill="x", padx=4, pady=4)
    ttk.Label(filter_row, text="Directory:").pack(side="left")
    dir_filter_var = tk.StringVar(value="(all)")
    dir_combo = ttk.Combobox(filter_row, textvariable=dir_filter_var, width=52)
    dir_combo.pack(side="left", padx=4)
    ttk.Label(filter_row, text="Search:").pack(side="left", padx=(12, 0))
    search_var = tk.StringVar()
    search_entry = ttk.Entry(filter_row, textvariable=search_var, width=20)
    search_entry.pack(side="left", padx=4)
    ttk.Label(filter_row, text="Days:").pack(side="left", padx=(12, 0))
    days_var = tk.StringVar(value=str(settings["sessions_days"]))
    ttk.Spinbox(filter_row, textvariable=days_var, from_=0, to=365, width=5
                ).pack(side="left", padx=4)
    ttk.Label(filter_row, text="(0 = all)").pack(side="left")

    cols = ("when", "dir", "title", "id")
    sess_tree = ttk.Treeview(sess_frame, columns=cols, show="headings", height=9)
    widths = {"when": 130, "dir": 280, "title": 420, "id": 250}
    for c in cols:
        sess_tree.heading(c, text=c.title())
        sess_tree.column(c, width=widths[c], anchor="w")
    sess_scroll = ttk.Scrollbar(sess_frame, orient="vertical",
                                command=sess_tree.yview)
    sess_tree.configure(yscrollcommand=sess_scroll.set)
    sess_tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
    sess_scroll.pack(side="left", fill="y", pady=4)

    sessions_cache = []

    def apply_filter(*_):
        """Repopulate the tree from cache — instant, no disk/schtasks."""
        want = dir_filter_var.get()
        needle = search_var.get().strip().lower()
        sess_tree.delete(*sess_tree.get_children())
        for s in sessions_cache:
            if want not in ("", "(all)") and s["dir"].lower() != want.lower():
                continue
            if needle and needle not in \
                    f"{s['id']} {s['dir']} {s['title']}".lower():
                continue
            mark = "* " if s["active"] else ""
            sess_tree.insert("", "end", iid=s["id"], values=(
                f"{mark}{s['mtime']:%Y-%m-%d %H:%M}", s["dir"],
                s["title"], s["id"]))
        set_status(f"{len(sess_tree.get_children())} sessions shown")

    def refresh_sessions():
        set_status("scanning sessions…")
        try:
            days = int(days_var.get() or 0)
        except ValueError:
            days = 0
        nonlocal sessions_cache
        sessions_cache = scan_sessions(days=days)
        dirs = sorted({s["dir"] for s in sessions_cache}, key=str.lower)
        dir_combo["values"] = ["(all)"] + dirs
        apply_filter()

    search_entry.bind("<KeyRelease>", apply_filter)
    dir_combo.bind("<<ComboboxSelected>>", apply_filter)
    ttk.Button(filter_row, text="Refresh",
               command=lambda: with_loading_curtain(refresh_sessions)
               ).pack(side="left", padx=12)

    # ---------------- schedule form ----------------
    form = ttk.LabelFrame(root, text="Schedule a run")
    form.pack(fill="x", padx=10, pady=6)
    PADY = 6

    # state vars (shared by simple + advanced)
    target_var = tk.StringVar(value="continue")
    session_var = tk.StringVar()
    dir_var = tk.StringVar(value=settings.get("default_dir") or str(Path.home()))
    name_var = tk.StringVar()
    target_desc = tk.StringVar()
    session_title = {"text": ""}

    def update_target_desc(*_):
        d = dir_var.get()
        if target_var.get() == "resume" and session_var.get():
            t = session_title["text"] or session_var.get()[:8] + "…"
            target_desc.set(f'Resume "{t[:60]}"  in  {d}')
        elif target_var.get() == "new":
            target_desc.set(f"New session in  {d}")
        else:
            target_desc.set(f"Continue latest chat in  {d}")

    # --- simple rows ---
    srow = ttk.Frame(form); srow.pack(fill="x", padx=10, pady=PADY)
    ttk.Label(srow, text="Target:").pack(side="left")
    ttk.Label(srow, textvariable=target_desc, foreground="#1a5276"
              ).pack(side="left", padx=8)

    def clear_pick():
        target_var.set("continue")
        session_var.set("")
        session_title["text"] = ""
        dir_var.set(settings.get("default_dir") or str(Path.home()))
        update_target_desc()
    ttk.Button(srow, text="Clear pick", command=clear_pick).pack(side="right")

    trow2 = ttk.Frame(form); trow2.pack(fill="x", padx=10, pady=PADY)
    ttk.Label(trow2, text="Time:").pack(side="left")
    default_dt = (datetime.now() + timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0)
    date_var = tk.StringVar(value=default_dt.strftime("%Y-%m-%d"))
    time_var = tk.StringVar(value=default_dt.strftime("%H:%M"))
    ttk.Entry(trow2, textvariable=time_var, width=7).pack(side="left", padx=8)
    ttk.Label(trow2, text="(next occurrence — today if still ahead, else tomorrow)",
              foreground="gray40").pack(side="left", padx=4)

    prow = ttk.Frame(form); prow.pack(fill="x", padx=10, pady=PADY)
    ttk.Label(prow, text="Prompt:").pack(side="left", anchor="n")
    prompt_text = tk.Text(prow, height=3, width=70)
    prompt_text.pack(side="left", padx=8, fill="x", expand=True)

    def on_session_pick(_event):
        sel = sess_tree.selection()
        if not sel:
            return
        sid = sel[0]
        target_var.set("resume")
        session_var.set(sid)
        s = next((x for x in sessions_cache if x["id"] == sid), None)
        if s:
            dir_var.set(s["dir"])
            session_title["text"] = s["title"]
            if s["title"]:
                name_var.set(sanitize_name(s["title"][:40]))
        update_target_desc()
        set_status(f"targeting session {sid[:8]}…")
    sess_tree.bind("<Double-1>", on_session_pick)

    # --- advanced frame (hidden until toggled) ---
    adv = ttk.Frame(form)
    adv_visible = tk.BooleanVar(value=False)

    arow1 = ttk.Frame(adv); arow1.pack(fill="x", padx=10, pady=PADY)
    ttk.Radiobutton(arow1, text="Resume session:", value="resume",
                    variable=target_var, command=update_target_desc
                    ).pack(side="left")
    ttk.Entry(arow1, textvariable=session_var, width=38).pack(side="left", padx=6)
    ttk.Radiobutton(arow1, text="Continue latest in dir", value="continue",
                    variable=target_var, command=update_target_desc
                    ).pack(side="left", padx=(18, 0))
    ttk.Radiobutton(arow1, text="New session", value="new",
                    variable=target_var, command=update_target_desc
                    ).pack(side="left", padx=(18, 0))

    arow2 = ttk.Frame(adv); arow2.pack(fill="x", padx=10, pady=PADY)
    ttk.Label(arow2, text="Directory:").pack(side="left")
    dent = ttk.Entry(arow2, textvariable=dir_var, width=70)
    dent.pack(side="left", padx=6)
    dent.bind("<FocusOut>", update_target_desc)

    def browse():
        d = filedialog.askdirectory(initialdir=dir_var.get() or str(Path.home()))
        if d:
            dir_var.set(str(Path(d)))
            update_target_desc()
    ttk.Button(arow2, text="Browse…", command=browse).pack(side="left")

    arow3 = ttk.Frame(adv); arow3.pack(fill="x", padx=10, pady=PADY)
    when_var = tk.StringVar(value="once")
    ttk.Radiobutton(arow3, text="Once", value="once", variable=when_var
                    ).pack(side="left")
    ttk.Label(arow3, text="Date:").pack(side="left", padx=(8, 0))
    ttk.Entry(arow3, textvariable=date_var, width=11).pack(side="left", padx=3)
    ttk.Radiobutton(arow3, text="Daily", value="daily", variable=when_var
                    ).pack(side="left", padx=(20, 0))
    ttk.Radiobutton(arow3, text="Weekly:", value="weekly", variable=when_var
                    ).pack(side="left", padx=(20, 6))
    day_vars = {}
    for d in DAY_ORDER:
        v = tk.BooleanVar(value=False)
        day_vars[d] = v
        ttk.Checkbutton(arow3, text=d, variable=v).pack(side="left", padx=1)

    def set_mf():
        when_var.set("weekly")
        for d, v in day_vars.items():
            v.set(d in ("MON", "TUE", "WED", "THU", "FRI"))
    ttk.Button(arow3, text="M–F", width=5, command=set_mf
               ).pack(side="left", padx=8)

    arow4 = ttk.Frame(adv); arow4.pack(fill="x", padx=10, pady=PADY)
    ttk.Label(arow4, text="Model:").pack(side="left")
    model_var = tk.StringVar(value=settings["model"])
    ttk.Combobox(arow4, textvariable=model_var, values=MODELS, width=10
                 ).pack(side="left", padx=6)
    ttk.Label(arow4, text="Permissions:").pack(side="left", padx=(14, 0))
    mode_var = tk.StringVar(value=settings["permission_mode"])
    ttk.Combobox(arow4, textvariable=mode_var, values=PERMISSION_MODES,
                 width=16, state="readonly").pack(side="left", padx=6)
    ttk.Label(arow4, text="Terminal:").pack(side="left", padx=(14, 0))
    term_var = tk.StringVar(value=settings["terminal"] or default_terminal(settings))
    ttk.Combobox(arow4, textvariable=term_var, values=TERMINALS, width=9,
                 state="readonly").pack(side="left", padx=6)
    ttk.Label(arow4, text="Name:").pack(side="left", padx=(14, 0))
    ttk.Entry(arow4, textvariable=name_var, width=22).pack(side="left", padx=6)

    arow5 = ttk.Frame(adv); arow5.pack(fill="x", padx=10, pady=PADY)
    net_var = tk.BooleanVar(value=settings["require_network"])
    ttk.Checkbutton(arow5, text="Require network", variable=net_var
                    ).pack(side="left")
    keep_var = tk.BooleanVar(value=not settings["delete_after_run"])
    ttk.Checkbutton(arow5, text="Keep one-shot task after run", variable=keep_var
                    ).pack(side="left", padx=(16, 0))
    ttk.Label(arow5, text="Extra claude args:").pack(side="left", padx=(20, 0))
    extra_var = tk.StringVar()
    ttk.Entry(arow5, textvariable=extra_var, width=40).pack(side="left", padx=6)

    def open_args_builder():
        top = tk.Toplevel(root)
        top.title("Extra args builder")
        top.resizable(False, False)
        pad = {"padx": 12, "pady": 6}
        r1 = ttk.Frame(top); r1.pack(fill="x", **pad)
        ttk.Label(r1, text="Effort:").pack(side="left")
        eff_var = tk.StringVar(value="")
        ttk.Combobox(r1, textvariable=eff_var, width=10, state="readonly",
                     values=["", "low", "medium", "high", "xhigh", "max"]
                     ).pack(side="left", padx=8)
        ttk.Label(r1, text="(how hard the model thinks)",
                  foreground="gray40").pack(side="left")
        r2 = ttk.Frame(top); r2.pack(fill="x", **pad)
        fork_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r2, text="--fork-session  (resume into a copy; "
                        "original chat untouched)", variable=fork_var
                        ).pack(side="left")
        r3 = ttk.Frame(top); r3.pack(fill="x", **pad)
        ttk.Label(r3, text="--add-dir:").pack(side="left")
        adddir_var = tk.StringVar()
        ttk.Entry(r3, textvariable=adddir_var, width=48).pack(side="left", padx=8)
        ttk.Label(r3, text="(extra allowed directory)",
                  foreground="gray40").pack(side="left")
        r4 = ttk.Frame(top); r4.pack(fill="x", **pad)
        ttk.Label(r4, text="Anything else:").pack(side="left")
        free_var = tk.StringVar(value=extra_var.get())
        ttk.Entry(r4, textvariable=free_var, width=48).pack(side="left", padx=8)

        def apply_args():
            parts = []
            if eff_var.get():
                parts += ["--effort", eff_var.get()]
            if fork_var.get():
                parts.append("--fork-session")
            if adddir_var.get().strip():
                parts.append(list2cmdline(["--add-dir", adddir_var.get().strip()]))
            if free_var.get().strip():
                parts.append(free_var.get().strip())
            extra_var.set(" ".join(parts))
            top.destroy()
        r5 = ttk.Frame(top); r5.pack(fill="x", **pad)
        ttk.Button(r5, text="Apply", command=apply_args).pack(side="left")
        ttk.Button(r5, text="Cancel", command=top.destroy
                   ).pack(side="left", padx=8)
    ttk.Button(arow5, text="Builder…", command=open_args_builder
               ).pack(side="left", padx=4)

    def gather_job():
        adv_on = adv_visible.get()
        mode = target_var.get()
        sid = session_var.get().strip()
        if mode == "resume" and not UUID_RE.match(sid):
            raise ValueError("Resume needs a session UUID (double-click one above).")
        d = dir_var.get().strip()
        if not d or not Path(d).is_dir():
            raise ValueError(f"Directory does not exist: {d!r}")
        t = time_var.get().strip()
        datetime.strptime(t, "%H:%M")
        if not adv_on or when_var.get() == "once":
            if adv_on:
                dt = datetime.fromisoformat(f"{date_var.get().strip()}T{t}")
                if dt <= datetime.now():
                    raise ValueError("That time is in the past.")
            else:  # simple: next occurrence of the time
                hh, mm = (int(x) for x in t.split(":"))
                dt = datetime.now().replace(hour=hh, minute=mm, second=0,
                                            microsecond=0)
                if dt <= datetime.now():
                    dt += timedelta(days=1)
            schedule = {"type": "once", "datetime": dt.isoformat(timespec="minutes")}
        elif when_var.get() == "daily":
            schedule = {"type": "daily", "time": t}
        else:
            days = [d_ for d_ in DAY_ORDER if day_vars[d_].get()]
            if not days:
                raise ValueError("Pick at least one weekday (or hit M–F).")
            schedule = {"type": "weekly", "days": days, "time": t}
        name = name_var.get().strip() or f"{mode}-{Path(d).name}"
        return make_job(name, d, mode, sid, schedule, model_var.get().strip(),
                        mode_var.get(), term_var.get(),
                        prompt_text.get("1.0", "end").strip(),
                        extra_var.get().strip(), net_var.get(),
                        not keep_var.get())

    def do_schedule():
        try:
            job = gather_job()
            register_job(job, settings)
        except (ValueError, RuntimeError) as e:
            messagebox.showerror("claude-at", str(e))
            return
        set_status(f"scheduled {job['name']}-{job['id']} "
                   f"({describe_schedule(job)})")
        refresh_pending()

    def do_preview():
        try:
            job = gather_job()
            xml = build_task_xml(job, settings)
            cmd, argstr, wd = build_action(job, settings)
        except (ValueError, RuntimeError) as e:
            messagebox.showerror("claude-at", str(e))
            return
        top = tk.Toplevel(root)
        top.title("Preview — nothing registered")
        txt = tk.Text(top, width=110, height=32)
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", f"Action : {cmd}\nArgs   : {argstr}\n"
                          f"Workdir: {wd}\nNext   : {next_fire(job)}\n\n{xml}")
        txt.configure(state="disabled")

    brow = ttk.Frame(form); brow.pack(fill="x", padx=10, pady=8)
    ttk.Button(brow, text="Schedule", command=do_schedule).pack(side="left")

    def set_advanced(show):
        adv_visible.set(show)
        if show:
            adv.pack(fill="x", before=brow)
        else:
            adv.pack_forget()
        adv_btn.config(text="Advanced ▴" if show else "Advanced ▾")
    adv_btn = ttk.Button(brow, text="Advanced ▾",
                         command=lambda: set_advanced(not adv_visible.get()))
    adv_btn.pack(side="left", padx=10)
    ttk.Button(brow, text="Preview command + XML", command=do_preview
               ).pack(side="left", padx=4)
    update_target_desc()

    # ---------------- pending runs pane ----------------
    pend_frame = ttk.LabelFrame(root, text="Pending runs (soonest first)")
    pend_frame.pack(fill="both", expand=True, padx=8, pady=(4, 4))

    pcols = ("del", "next", "name", "schedule", "target", "model", "mode", "term", "tstat")
    pend_tree = ttk.Treeview(pend_frame, columns=pcols, show="headings", height=7)
    pwidths = {"del": 28, "next": 130, "name": 170, "schedule": 150, "target": 270,
               "model": 70, "mode": 110, "term": 70, "tstat": 90}
    pheads = {"del": "", "next": "Next run", "name": "Job", "schedule": "Schedule",
              "target": "Target", "model": "Model", "mode": "Permissions",
              "term": "Terminal", "tstat": "Task status"}
    for c in pcols:
        pend_tree.heading(c, text=pheads[c])
        pend_tree.column(c, width=pwidths[c], anchor="w",
                         stretch=(c not in ("del",)))
    pscroll = ttk.Scrollbar(pend_frame, orient="vertical",
                            command=pend_tree.yview)
    pend_tree.configure(yscrollcommand=pscroll.set)
    pend_tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
    pscroll.pack(side="left", fill="y", pady=4)

    def refresh_pending():
        pend_tree.delete(*pend_tree.get_children())
        jobs = sorted(load_jobs(), key=lambda j: (next_fire(j) or datetime.max))
        qall = task_query_all()
        for j in jobs:
            nf = next_fire(j)
            q = qall.get(j.get("task_name", task_name_for(j)))
            tstat = q["status"] if q else "MISSING"
            pend_tree.insert("", "end", iid=j["id"], values=(
                "✕", f"{nf:%Y-%m-%d %H:%M}" if nf else "expired",
                f"{j['name']}-{j['id']}", describe_schedule(j),
                describe_target(j), j["model"], j["permission_mode"],
                j["terminal"], tstat))
        set_status(f"{len(pend_tree.get_children())} pending jobs")

    def on_pending_click(event):
        # clicking the ✕ cell deletes that job (with confirm)
        if pend_tree.identify_region(event.x, event.y) != "cell":
            return
        if pend_tree.identify_column(event.x) != "#1":  # the "del" column
            return
        iid = pend_tree.identify_row(event.y)
        if not iid:
            return
        j = find_job(load_jobs(), iid)
        if j and messagebox.askyesno(
                "claude-at", f"Delete upcoming run:\n{j['name']}-{j['id']}\n"
                f"({describe_schedule(j)})?"):
            delete_job(j)
            refresh_pending()
    pend_tree.bind("<Button-1>", on_pending_click)

    def selected_job():
        sel = pend_tree.selection()
        if not sel:
            messagebox.showinfo("claude-at", "Select a pending run first.")
            return None
        return find_job(load_jobs(), sel[0])

    def do_run_now():
        j = selected_job()
        if not j:
            return
        try:
            task_run(j.get("task_name", task_name_for(j)))
            set_status(f"fired {j['name']}-{j['id']}")
        except RuntimeError as e:
            messagebox.showerror("claude-at", str(e))

    def do_delete():
        j = selected_job()
        if not j:
            return
        if messagebox.askyesno("claude-at",
                               f"Delete job and task:\n{j['name']}-{j['id']}?"):
            delete_job(j)
            refresh_pending()

    def do_load():
        j = selected_job()
        if not j:
            return
        target_var.set(j["target"]["mode"])
        session_var.set(j["target"].get("session_id", ""))
        dir_var.set(j["dir"])
        s = j["schedule"]
        when_var.set(s["type"])
        if s["type"] == "once":
            d, t = s["datetime"].split("T")
            date_var.set(d); time_var.set(t[:5])
        else:
            time_var.set(s["time"])
        for d_, v in day_vars.items():
            v.set(s["type"] == "weekly" and d_ in s.get("days", []))
        model_var.set(j["model"]); mode_var.set(j["permission_mode"])
        term_var.set(j["terminal"]); name_var.set(j["name"])
        net_var.set(j.get("require_network", True))
        keep_var.set(not j.get("delete_after_run", True))
        prompt_text.delete("1.0", "end")
        prompt_text.insert("1.0", j.get("prompt", ""))
        extra_var.set(j.get("extra_args", ""))
        session_title["text"] = ""
        set_advanced(True)
        update_target_desc()
        set_status(f"loaded {j['name']}-{j['id']} into form "
                   "(Schedule creates a new job; delete the old one if replacing)")

    def do_prune():
        dropped = prune_jobs(verbose=False)
        refresh_pending()
        set_status(f"pruned {len(dropped)} job(s)")

    pbrow = ttk.Frame(pend_frame); pbrow.pack(side="left", fill="y", padx=6, pady=4)
    ttk.Button(pbrow, text="Refresh", command=refresh_pending).pack(fill="x", pady=2)
    ttk.Button(pbrow, text="Run now", command=do_run_now).pack(fill="x", pady=2)
    ttk.Button(pbrow, text="Load into form", command=do_load).pack(fill="x", pady=2)
    ttk.Button(pbrow, text="Delete", command=do_delete).pack(fill="x", pady=2)
    ttk.Button(pbrow, text="Prune done", command=do_prune).pack(fill="x", pady=2)

    ttk.Label(root, textvariable=status_var, relief="sunken", anchor="w"
              ).pack(fill="x", side="bottom")

    with_loading_curtain(lambda: (refresh_sessions(), refresh_pending()))

    if smoke:
        root.after(200, root.destroy)
    root.mainloop()
