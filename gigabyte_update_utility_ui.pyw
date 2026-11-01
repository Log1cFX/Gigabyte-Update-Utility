"""Window for gigabyte_update_utility: one view for updates and one for downloads, each explained in plain language."""

import argparse
import ctypes
import logging
import os
import queue
import sys
import threading
import tkinter as tk
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

import gigabyte_update_utility as core
import software_guide as guide

TITLE = "Gigabyte Update Utility"
# PyInstaller unpacks bundled files to _MEIPASS
ICON = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "icon.ico"
# Keeps the taskbar on this window's icon instead of Python's
APP_ID = "GigabyteUpdateUtility"
POLL_MS = 100
TICKED, UNTICKED = "☑", "☐"
SW_SHOWNORMAL = 1
SHELL_EXECUTE_OK = 32
UPDATES, DOWNLOADS = "updates", "downloads"
BG, SIDEBAR, CARD, CARD_HOVER, TEXT, MUTED, ACCENT, ACCENT_ACTIVE, SELECTED = (
    "#16181c", "#101114", "#1f2228", "#262a31", "#e8eaed", "#9aa0a6", "#f26522", "#ff7a3d", "#3a3f48",
)
GOOD, BAD, BLUE = "#7bc47f", "#ef6a6a", "#7fb2f0"
COLORS = {"accent": ACCENT, "good": GOOD, "bad": BAD, "muted": MUTED, "blue": BLUE, "text": TEXT}
STATUS_COLOR = {"UPDATE": "accent", "NEW": "accent", guide.RECOMMENDED: "blue", "OPTIONAL": "muted", "ok": "good",
                "unknown": "muted"}
VIEW_TEXT = {
    UPDATES: ("Updates", "Software already on this PC. Update All installs everything marked UPDATE."),
    DOWNLOADS: ("Downloads", "Software you can add to this PC, sorted by whether you really need it."),
}
WRAP_MARGIN = 470
MIN_WRAP = 280

log = logging.getLogger("gigabyte_update_utility_ui")


def size_text(size):
    if not size:
        return "N/A"
    return f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{size / 1e3:.0f} KB"


def split_views(items):
    """Installed software can be updated; anything else can be downloaded."""
    updates = [i for i in items if i.installed != core.NOT_INSTALLED]
    downloads = [i for i in items if i.installed == core.NOT_INSTALLED]
    return updates, downloads


def split_modules(items):
    """Control Center and everything that runs inside it get a section of their own."""
    return [i for i in items if i.kind == "module"], [i for i in items if i.kind != "module"]


def grouped_modules(items, entries):
    """[(theme, items)]: Control Center leads, then themes in order, with updates and recommendations first."""
    groups = {}
    for item in items:
        groups.setdefault(entries[item.num].category, []).append(item)
    order = sorted(groups, key=lambda c: (c != guide.CONTROL_CENTER, category_rank(c)))
    return [(theme, sorted(groups[theme], key=lambda i: (i.num != core.HOST_MODULE, not i.offered,
                                                         entries[i.num].tier != guide.RECOMMENDED, i.name.lower())))
            for theme in order]


def row_status(item, entry):
    """The word on a row. In Downloads the importance is what the person needs to see."""
    status = core.status_of(item)
    if status == "OPTIONAL" and entry.tier == guide.RECOMMENDED:
        return guide.RECOMMENDED
    return status


def category_rank(category):
    return guide.CATEGORY_ORDER.index(category)


def grouped_updates(items, entries):
    """[(category, items)]: categories with an update come first, and updates lead inside each category."""
    groups = {}
    for item in items:
        groups.setdefault(entries[item.num].category, []).append(item)
    order = sorted(groups, key=lambda c: (not any(i.offered for i in groups[c]), category_rank(c)))
    return [(category, sorted(groups[category], key=lambda i: (not i.offered, i.name.lower()))) for category in order]


def grouped_downloads(items, entries):
    """[(tier, items)], in tier order and sorted by category inside each tier."""
    groups = {}
    for item in items:
        groups.setdefault(entries[item.num].tier, []).append(item)
    return [(tier, sorted(groups[tier], key=lambda i: (category_rank(entries[i.num].category), i.name.lower())))
            for tier in guide.TIER_ORDER if tier in groups]


class Selection:
    """The default action takes UPDATE rows in Updates and NEW rows in Downloads. Any tick replaces it."""

    def __init__(self, view, items=(), entries=None):
        self.view = view
        self.items = list(items)
        self.entries = entries or {}
        self.ticked = set()

    @property
    def available(self):
        wanted_status = "UPDATE" if self.view == UPDATES else "NEW"
        return [i for i in self.items if core.status_of(i) == wanted_status]

    def toggle(self, num):
        if any(i.num == num for i in self.items):
            self.ticked.symmetric_difference_update({num})

    def toggle_all(self):
        """Select all never takes OPTIONAL downloads: those are only ticked one at a time."""
        everything = {i.num for i in self.items
                      if self.view == UPDATES or self.entries[i.num].tier != guide.OPTIONAL}
        self.ticked = set() if self.ticked == everything else everything

    def targets(self):
        return [i for i in self.items if i.num in self.ticked] if self.ticked else self.available

    def button_label(self):
        if self.ticked:
            return f"Install Selected ({len(self.ticked)})"
        if self.view == UPDATES:
            return f"Update All ({len(self.available)})"
        return f"Install Needed ({len(self.available)})"


@dataclass
class Outcome:
    installed: int = 0
    failed: int = 0
    cancelled: int = 0
    restart: bool = False
    restore_warning: str = ""


def run_updates(items, emit, cancel, backend=core):
    """Worker thread. emit(kind, *values) reports to the window; one failed item doesn't stop the rest."""
    outcome = Outcome()
    emit("status", "Creating restore point...")
    created, detail = backend.create_restore_point()
    if not created:
        outcome.restore_warning = detail
        log.warning("Restore point: %s", detail)
    stopped = []
    if backend.has_modules(items):
        emit("status", "Closing Control Center and stopping its services...")
        stopped = backend.prepare_modules()
    try:
        install_items(items, emit, cancel, backend, outcome)
    finally:
        backend.restore_modules(stopped)
    return outcome


def install_items(items, emit, cancel, backend, outcome):
    for index, item in enumerate(items):
        if cancel.is_set():
            emit("row", item.num, "Cancelled", "muted")
            outcome.cancelled += 1
            continue
        emit("status", f"Item {index + 1} of {len(items)}: {item.name}")
        emit("progress", 0)
        try:
            def on_progress(_name, done, total, num=item.num):
                emit("row", num, f"Downloading {done * 100 // max(total, 1)}%", "accent")
                emit("progress", done * 100 / max(total, 1))

            emit("row", item.num, "Downloading...", "accent")
            path = backend.download(item, progress=on_progress, cancelled=cancel.is_set, say=log.info)
            emit("progress", 100)
            emit("row", item.num, "Installing...", "accent")
            code = backend.run_installer(path, item.args)
        except core.Cancelled:
            emit("row", item.num, "Cancelled", "muted")
            outcome.cancelled += 1
            continue
        except (core.ToolError, urllib.error.URLError, OSError) as error:
            log.error("%s: %s", item.name, error)
            label = "Rejected" if "Rejected" in str(error) else "Failed"
            emit("row", item.num, label, "bad")
            emit("detail", item.num, str(error))
            outcome.failed += 1
            continue
        log.info("%s: installer exit code %s", item.name, code)
        if code == core.RESTART_REQUIRED:
            outcome.restart = True
            outcome.installed += 1
            emit("row", item.num, "Done, restart needed", "good")
        elif code == 0:
            outcome.installed += 1
            emit("row", item.num, "Done", "good")
        else:
            outcome.failed += 1
            emit("row", item.num, f"Failed (code {code})", "bad")


def summary_text(outcome):
    parts = [f"Finished: {outcome.installed}"]
    if outcome.failed:
        parts.append(f"failed: {outcome.failed}")
    if outcome.cancelled:
        parts.append(f"cancelled: {outcome.cancelled}")
    lines = [", ".join(parts) + "."]
    if outcome.restart:
        lines.append("Restart Windows to complete the update.")
    if outcome.restore_warning:
        lines.append(f"No new restore point was confirmed: {outcome.restore_warning}")
    return "\n\n".join(lines)


def scan_worker(events, backend):
    """Module level like update_worker: Tcl aborts if a thread is left holding the last reference to the window."""
    try:
        events.put(("scanned", backend.scan()))
    except Exception as error:
        log.exception("Scan failed")
        events.put(("scan_failed", str(error)))


def update_worker(events, cancel, backend, targets):
    def emit(*event):
        events.put(event)

    try:
        outcome = run_updates(targets, emit, cancel, backend)
    except Exception as error:
        log.exception("Update failed")
        outcome = Outcome(failed=len(targets))
        emit("status", f"Error: {error}")
    emit("finished", outcome)


class Row:
    """One piece of software: tick box, name, hint, versions, size and status."""

    def __init__(self, parent, item, entry, status, on_toggle, show_category):
        self.item = item
        self.hint_text = entry.hint
        self.frame = tk.Frame(parent, bg=CARD, padx=12, pady=10)
        self.frame.pack(fill="x", pady=(0, 6))
        self.frame.columnconfigure(1, weight=1)

        self.tick = tk.Label(self.frame, text=UNTICKED, bg=CARD, fg=TEXT, font=("Segoe UI Symbol", 15), cursor="hand2",
                             takefocus=1)
        self.tick.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 12))
        self.tick.bind("<Button-1>", lambda _e: on_toggle(item.num))
        self.tick.bind("<space>", lambda _e: on_toggle(item.num))

        title = item.name + (f"   ·   {entry.category}" if show_category else "")
        self.name = tk.Label(self.frame, text=title, bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 10), anchor="w",
                             justify="left")
        self.name.grid(row=0, column=1, sticky="ew")
        self.hint = tk.Label(self.frame, text=entry.hint, bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="w",
                             justify="left", wraplength=620)
        self.hint.grid(row=1, column=1, sticky="ew", pady=(2, 0))

        versions = f"{item.installed}  →  {item.latest}" if item.offered and item.installed != core.NOT_INSTALLED \
            else item.installed if item.installed != core.NOT_INSTALLED else f"Version {item.latest}"
        self.version = tk.Label(self.frame, text=versions, bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="e")
        self.version.grid(row=0, column=2, sticky="e", padx=(16, 0))
        self.size = tk.Label(self.frame, text=size_text(item.size), bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="e")
        self.size.grid(row=1, column=2, sticky="ne", padx=(16, 0), pady=(2, 0))
        self.status = tk.Label(self.frame, text=status, bg=CARD, fg=COLORS[STATUS_COLOR.get(status, "text")],
                               font=("Segoe UI Semibold", 9), width=18, anchor="e")
        self.status.grid(row=0, column=3, rowspan=2, sticky="e", padx=(12, 0))

    def set_ticked(self, ticked):
        self.tick.configure(text=TICKED if ticked else UNTICKED, fg=ACCENT if ticked else TEXT)

    def set_status(self, text, tag):
        self.status.configure(text=text, fg=COLORS[tag])

    def set_error(self, text):
        self.hint.configure(text=text, fg=BAD)

    def set_wrap(self, pixels):
        self.hint.configure(wraplength=pixels)


class ScrollList(tk.Frame):
    """A vertically scrolling list of widgets."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.body = tk.Frame(self.canvas, bg=BG)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.on_resize)
        self.on_width = None

    def on_resize(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        if self.on_width:
            self.on_width(event.width)

    def scroll(self, event):
        if self.body.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-1 * event.delta // 120, "units")

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.canvas.yview_moveto(0)


class App(tk.Tk):
    def __init__(self, backend=core, can_install=True, confirm=messagebox.askyesno, notify=messagebox.showinfo):
        super().__init__()
        self.backend = backend
        self.can_install = can_install
        self.confirm = confirm
        self.notify = notify
        self.selections = {UPDATES: Selection(UPDATES), DOWNLOADS: Selection(DOWNLOADS)}
        self.view = UPDATES
        self.rows = {}
        self.all_items = []
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.worker = None
        self.title(TITLE)
        self.geometry("1180x700")
        self.minsize(980, 520)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self.on_exit)
        self.set_icon()
        self.build_style()
        self.build_layout()
        self.show_view(UPDATES)
        self.poll_id = self.after(POLL_MS, self.poll)
        self.refresh()

    def set_icon(self):
        try:
            self.iconbitmap(default=str(ICON))
        except tk.TclError:
            log.warning("Couldn't load %s", ICON)

    def destroy(self):
        self.after_cancel(self.poll_id)
        self.progress.stop()
        self.unbind_all("<MouseWheel>")
        super().destroy()

    def build_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Vertical.TScrollbar", background=CARD, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED,
                        lightcolor=CARD, darkcolor=CARD, gripcount=0)
        style.map("Vertical.TScrollbar", background=[("active", SELECTED), ("disabled", BG)],
                  lightcolor=[("disabled", BG)], darkcolor=[("disabled", BG)], arrowcolor=[("disabled", BG)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=CARD, bordercolor=BG, thickness=6,
                        lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure("Primary.TButton", background=ACCENT, foreground="#ffffff", borderwidth=0, padding=(22, 10),
                        font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("disabled", CARD), ("active", ACCENT_ACTIVE)],
                  foreground=[("disabled", MUTED)])
        style.configure("Secondary.TButton", background=CARD, foreground=TEXT, borderwidth=0, padding=(18, 10))
        style.map("Secondary.TButton", background=[("disabled", BG), ("active", SELECTED)],
                  foreground=[("disabled", MUTED)])

    def build_layout(self):
        sidebar = tk.Frame(self, bg=SIDEBAR, width=210)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        tk.Label(sidebar, text="Gigabyte\nUpdate Utility", bg=SIDEBAR, fg=TEXT, font=("Segoe UI Semibold", 14),
                 justify="left", anchor="w").pack(fill="x", padx=20, pady=(22, 24))
        self.nav = {}
        for view in (UPDATES, DOWNLOADS):
            button = tk.Label(sidebar, text=VIEW_TEXT[view][0], bg=SIDEBAR, fg=MUTED, font=("Segoe UI Semibold", 11),
                              anchor="w", padx=20, pady=11, cursor="hand2")
            button.pack(fill="x")
            button.bind("<Button-1>", lambda _e, v=view: self.show_view(v))
            self.nav[view] = button
        self.board_label = tk.Label(sidebar, text="", bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 9), justify="left",
                                    anchor="w", wraplength=175)
        self.board_label.pack(side="bottom", fill="x", padx=20, pady=18)

        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)
        header = tk.Frame(main, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 10))
        self.heading = tk.Label(header, text="", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 18), anchor="w")
        self.heading.pack(fill="x")
        self.subheading = tk.Label(header, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10), anchor="w")
        self.subheading.pack(fill="x", pady=(2, 0))
        tools = tk.Frame(header, bg=BG)
        tools.pack(fill="x", pady=(8, 0))
        self.counts = tk.Label(tools, text="", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 10), anchor="w")
        self.counts.pack(side="left")
        self.select_all = tk.Label(tools, text="Select all", bg=BG, fg=BLUE, font=("Segoe UI", 10), cursor="hand2")
        self.select_all.pack(side="right")
        self.select_all.bind("<Button-1>", lambda _e: self.toggle_all())

        lists = tk.Frame(main, bg=BG)
        lists.pack(fill="both", expand=True, padx=(24, 12))
        self.lists = {UPDATES: ScrollList(lists), DOWNLOADS: ScrollList(lists)}
        for scroll_list in self.lists.values():
            scroll_list.on_width = self.on_list_width
        self.bind_all("<MouseWheel>", lambda e: self.lists[self.view].scroll(e))

        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=24, pady=(10, 0))
        footer = tk.Frame(main, bg=BG)
        footer.pack(fill="x", padx=24, pady=(10, 16))
        self.status = tk.Label(footer, text="", bg=BG, fg=MUTED, font=("Segoe UI", 10), anchor="w", justify="left",
                               wraplength=430)
        self.status.pack(side="left", fill="x", expand=True)
        self.exit_button = ttk.Button(footer, text="Exit", style="Secondary.TButton", command=self.on_exit)
        self.exit_button.pack(side="right", padx=(8, 0))
        self.primary = ttk.Button(footer, text="Update All (0)", style="Primary.TButton", command=self.start_updates)
        self.primary.pack(side="right", padx=(8, 0))
        self.cancel_button = ttk.Button(footer, text="Cancel", style="Secondary.TButton", command=self.on_cancel)
        self.cancel_button.pack(side="right", padx=(8, 0))
        self.refresh_button = ttk.Button(footer, text="Check Again", style="Secondary.TButton", command=self.refresh)
        self.refresh_button.pack(side="right", padx=(8, 0))

    @property
    def selection(self):
        return self.selections[self.view]

    def on_list_width(self, width):
        wrap = max(width - WRAP_MARGIN, MIN_WRAP)
        for row in self.rows.values():
            row.set_wrap(wrap)

    def show_view(self, view):
        self.view = view
        for name, scroll_list in self.lists.items():
            if name == view:
                scroll_list.pack(fill="both", expand=True)
            else:
                scroll_list.pack_forget()
        for name, button in self.nav.items():
            active = name == view
            button.configure(bg=BG if active else SIDEBAR, fg=ACCENT if active else MUTED)
        self.heading.configure(text=VIEW_TEXT[view][0])
        self.subheading.configure(text=VIEW_TEXT[view][1])
        self.update_counts()
        self.update_controls()

    def update_counts(self):
        updates, downloads = self.selections[UPDATES], self.selections[DOWNLOADS]
        self.nav[UPDATES].configure(text=f"Updates ({len(updates.available)})")
        self.nav[DOWNLOADS].configure(text=f"Downloads ({len(downloads.items)})")
        if self.view == UPDATES:
            installed = len(updates.items)
            self.counts.configure(text=f"{len(updates.available)} of {installed} have an update" if installed else "")
            return
        tiers = {tier: sum(1 for i in downloads.items if downloads.entries[i.num].tier == tier) for tier in guide.TIER_ORDER}
        words = {guide.NEEDED: "needed", guide.RECOMMENDED: "recommended", guide.OPTIONAL: "optional"}
        self.counts.configure(text="   ·   ".join(f"{tiers[t]} {words[t]}" for t in guide.TIER_ORDER)
                              if downloads.items else "")

    def update_controls(self):
        self.primary.configure(text=self.selection.button_label())
        ready = not self.busy and self.can_install and bool(self.selection.targets())
        self.primary.configure(state="normal" if ready else "disabled")
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        self.cancel_button.configure(state="normal" if self.busy else "disabled")
        self.select_all.configure(text="Clear selection" if self.selection.ticked else "Select all")

    def set_status(self, text):
        self.status.configure(text=text)

    def start_worker(self, target, *args):
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        for scroll_list in self.lists.values():
            scroll_list.clear()
        self.rows = {}
        self.selections = {UPDATES: Selection(UPDATES), DOWNLOADS: Selection(DOWNLOADS)}
        self.update_counts()
        self.set_status("Checking this PC and Gigabyte's update data...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(15)
        self.update_controls()
        self.cancel_button.configure(state="disabled")
        self.start_worker(scan_worker, self.events, self.backend)

    def add_header(self, parent, title, text=""):
        tk.Label(parent, text=title, bg=BG, fg=TEXT, font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x", pady=(12, 0))
        if text:
            tk.Label(parent, text=text, bg=BG, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left").pack(fill="x")
        tk.Frame(parent, bg=BG, height=6).pack(fill="x")

    def add_modules_section(self, parent, modules, entries, host_installed):
        if not modules:
            return
        self.add_header(parent, guide.MODULES_TITLE, guide.MODULES_NOTE)
        if not host_installed:
            tk.Label(parent, text=guide.MODULES_WARNING, bg=BG, fg=ACCENT, font=("Segoe UI Semibold", 9),
                     anchor="w", justify="left").pack(fill="x", pady=(0, 6))
        for theme, group in grouped_modules(modules, entries):
            tk.Label(parent, text=theme, bg=BG, fg=MUTED, font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x", pady=(6, 4))
            for item in group:
                self.add_row(parent, item, entries, False)

    def add_row(self, parent, item, entries, show_category):
        status = row_status(item, entries[item.num])
        self.rows[item.num] = Row(parent, item, entries[item.num], status, self.toggle_row, show_category)

    def add_empty(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED, font=("Segoe UI", 11), anchor="w").pack(fill="x", pady=24)

    def show_scan(self, result):
        items = self.backend.window_items(result.items)
        entries = {i.num: guide.guide_for(i, result.hardware, result.system.laptop) for i in items}
        updates, downloads = split_views(items)
        self.all_items = items
        self.selections = {UPDATES: Selection(UPDATES, updates, entries), DOWNLOADS: Selection(DOWNLOADS, downloads, entries)}

        host_installed = self.backend.control_center_installed(items)

        body = self.lists[UPDATES].body
        modules, others = split_modules(updates)
        modules_first = any(i.offered for i in modules) and not any(i.offered for i in others)
        if modules_first:
            self.add_modules_section(body, modules, entries, host_installed)
        for category, group in grouped_updates(others, entries):
            self.add_header(body, category)
            for item in group:
                self.add_row(body, item, entries, False)
        if not modules_first:
            self.add_modules_section(body, modules, entries, host_installed)
        if not updates:
            self.add_empty(body, "Nothing from this list is installed yet. Look in Downloads.")

        body = self.lists[DOWNLOADS].body
        modules, others = split_modules(downloads)
        for tier, group in grouped_downloads(others, entries):
            self.add_header(body, *guide.TIER_TEXT[tier])
            for item in group:
                self.add_row(body, item, entries, True)
        self.add_modules_section(body, modules, entries, host_installed)
        if not downloads:
            self.add_empty(body, "Everything available for this PC is already installed.")

        self.board_label.configure(text=f"{result.system.model}\nData {result.folder}")
        width = self.lists[self.view].canvas.winfo_width()
        if width > 1:
            self.on_list_width(width)
        self.update_counts()
        count = len(self.selections[UPDATES].available)
        needed = len(self.selections[DOWNLOADS].available)
        if not self.can_install:
            self.set_status("Showing results only: restart the app and approve the administrator prompt to install.")
        elif needed:
            self.set_status(f"{needed} needed driver{'s' if needed != 1 else ''} in Downloads. Install those first.")
        elif count:
            self.set_status(f"{count} update{'s' if count != 1 else ''} available.")
        else:
            self.set_status("Everything installed on this PC is up to date.")
        self.update_controls()

    def toggle_row(self, num):
        if self.busy:
            return
        selection = next((s for s in self.selections.values() if any(i.num == num for i in s.items)), None)
        if selection:
            selection.toggle(num)
            self.draw_ticks()

    def toggle_all(self):
        if self.busy:
            return
        self.selection.toggle_all()
        self.draw_ticks()

    def draw_ticks(self):
        for selection in self.selections.values():
            for item in selection.items:
                self.rows[item.num].set_ticked(item.num in selection.ticked)
        self.update_controls()

    def start_updates(self):
        targets = self.selection.targets()
        if self.busy or not targets or not self.can_install:
            return
        older = [i for i in targets if self.backend.would_downgrade(i)]
        if older:
            names = "\n".join(f"{i.name}: installed {i.installed}, this file {i.latest}" for i in older)
            if not self.confirm(TITLE, f"This would replace a newer installed version:\n\n{names}\n\nContinue?"):
                return
        planned = self.backend.install_order(targets)
        if self.backend.has_modules(planned):
            stranded = self.backend.modules_missing_host(self.all_items, planned)
            if stranded:
                text = f"{self.backend.MISSING_HOST_NOTE}:\n\n" + "\n".join(i.name for i in stranded)
            else:
                text = self.backend.MODULE_NOTE
            if not self.confirm(TITLE, f"{text}\n\nContinue?"):
                return
        self.busy = True
        self.cancel.clear()
        self.progress.configure(mode="determinate", value=0)
        self.update_controls()
        self.start_worker(update_worker, self.events, self.cancel, self.backend, planned)

    def on_cancel(self):
        if self.busy:
            self.cancel.set()
            self.cancel_button.configure(state="disabled")
            self.set_status("Cancelling. A running installer can't be stopped, so it will finish first.")

    def on_exit(self):
        if self.busy and not self.confirm(TITLE, "Work is still running. Exit anyway?"):
            return
        self.destroy()

    def poll(self):
        try:
            while True:
                self.handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.poll_id = self.after(POLL_MS, self.poll)

    def stop_scan_animation(self):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)

    def handle(self, event):
        kind = event[0]
        if kind == "scanned":
            self.busy = False
            self.stop_scan_animation()
            self.show_scan(event[1])
        elif kind == "scan_failed":
            self.busy = False
            self.stop_scan_animation()
            self.set_status(f"Couldn't check for updates: {event[1]}")
            self.update_controls()
        elif kind == "status":
            if not self.cancel.is_set():
                self.set_status(event[1])
        elif kind == "row":
            if event[1] in self.rows:
                self.rows[event[1]].set_status(event[2], event[3])
            if event[3] == "good":
                self.mark_installed(event[1])
        elif kind == "progress":
            self.progress.configure(value=event[1])
        elif kind == "detail":
            if event[1] in self.rows:
                self.rows[event[1]].set_error(event[2])
        elif kind == "finished":
            self.finish(event[1])

    def mark_installed(self, num):
        """A later Update All must not run a finished item again."""
        for selection in self.selections.values():
            for item in selection.items:
                if item.num == num:
                    item.offered = False
            selection.ticked.discard(num)

    def finish(self, outcome):
        self.busy = False
        text = summary_text(outcome)
        log.info(text)
        self.notify(TITLE, text)
        if outcome.failed or outcome.cancelled:
            for selection in self.selections.values():
                selection.ticked = set()
            self.draw_ticks()
            self.update_counts()
            self.set_status(text.split("\n")[0])
        else:
            self.refresh()


def relaunch_as_admin(script, shell_execute=None):
    """True when an elevated copy started. Declining the prompt leaves the app in read-only mode."""
    shell_execute = shell_execute or ctypes.windll.shell32.ShellExecuteW
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        result = shell_execute(None, "runas", str(exe), None, str(exe.parent), SW_SHOWNORMAL)
        return result > SHELL_EXECUTE_OK
    windowless = Path(sys.executable).with_name("pythonw.exe")
    exe = windowless if windowless.exists() else Path(sys.executable)
    result = shell_execute(None, "runas", str(exe), f'"{script}"', str(Path(script).parent), SW_SHOWNORMAL)
    return result > SHELL_EXECUTE_OK


def main():
    parser = argparse.ArgumentParser(description=TITLE)
    parser.add_argument("--no-elevate", action="store_true", help="stay read-only instead of asking for administrator rights")
    args = parser.parse_args()
    admin = core.is_admin()
    if not admin and not args.no_elevate and relaunch_as_admin(os.path.abspath(__file__)):
        return 0
    core.HOME.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=core.HOME / "ui.log", level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    App(can_install=admin).mainloop()
    # Skips interpreter teardown, which can crash if a worker thread is still running
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
