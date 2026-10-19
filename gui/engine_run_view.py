import json
import os
import subprocess
import sys
import time
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import theme

PROGRESS_PREFIX = "@@FOUNDRY "

# Plain symbols: Tk on Windows has no color-emoji font and drops many emoji glyphs.
OUTCOME_ICONS = {"done": "✓", "not-finished": "!", "spending-limit": "$", "stopped": "■", "error": "✕", "undone": "↶"}
OUTCOME_COLORS = {"done": theme.SUCCESS, "not-finished": theme.WARNING, "spending-limit": theme.WARNING,
                  "stopped": theme.TEXT_FAINT, "error": theme.ERROR, "undone": theme.TEXT_MUTED}

# How hard the models think (engine --thinking / FOUNDRY_THINKING): (level, label, what it means)
THINKING_LEVELS = [
    ("low", "Low", "fastest and cheapest"),
    ("medium", "Medium", "quicker, for simple work"),
    ("high", "High", "the default"),
    ("xhigh", "Extra high", "for hard problems"),
    ("max", "Max", "most careful; slowest and costs the most"),
]
THINKING_PREF = "FOUNDRY_THINKING"
DEFAULT_THINKING = "high"


def thinking_label(level):
    return next((label for value, label, _ in THINKING_LEVELS if value == level), "High")


def thinking_level_for(config_manager):
    """The saved thinking level (one setting for every run: chats, Builds, Improve rounds and the Arena)."""
    value = str(config_manager.get(THINKING_PREF, "") or "").strip().lower().replace(" ", "")
    value = "xhigh" if value == "extrahigh" else value
    return value if value in [v for v, _, _ in THINKING_LEVELS] else DEFAULT_THINKING


# Neutral secondary button, like Claude Code's chips
CHIP = {"fg_color": theme.SURFACE_HOVER, "text_color": theme.TEXT, "hover_color": theme.SELECTED}
PRIMARY = {"fg_color": theme.ACCENT, "hover_color": theme.ACCENT_HOVER, "text_color": theme.ACCENT_TEXT}


def journal_path(folder):
    return os.path.join(folder, ".foundry", "journal.json")


def load_journal(folder):
    try:
        with open(journal_path(folder), "r", encoding="utf-8") as f:
            entries = json.load(f).get("entries", [])
        return entries if isinstance(entries, list) else []
    except Exception:
        return []


def append_journal_entry(folder, entry):
    """Records a run the engine could not record itself (e.g. the user pressed Stop)."""
    entries = load_journal(folder)
    entries.append(entry)
    os.makedirs(os.path.dirname(journal_path(folder)), exist_ok=True)
    ignore = os.path.join(folder, ".foundry", ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("*\n")
    with open(journal_path(folder), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "entries": entries[-50:]}, f, indent=2)


def format_money(value):
    return f"${value:,.2f}" if value >= 0.01 or value == 0 else f"${value:.4f}"


def format_elapsed(seconds):
    seconds = int(seconds)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


class ActivityFeed(ctk.CTkFrame):
    """Claude Code-style transcript of what the engine is doing: one ● line per step (orange while running,
    green when done, red when it failed), ⎿ result lines under it, and a live ✻ spinner line at the bottom."""

    def __init__(self, master, wrap):
        super().__init__(master, fg_color="transparent", width=1, height=1)
        self.grid_columnconfigure(1, weight=1)
        self._wrap = wrap
        self._row = 0
        self._current = None          # bullet label of the running step
        self._spinner_job = None
        self._spinning = False
        self._frame = 0
        self._started = 0.0
        self._activity = ""
        self._verb_index = 0
        self.current_step = ""  # text of the step in progress
        self._suffix = ""
        self._mono = ctk.CTkFont(family=theme.MONO_FAMILY, size=12)
        self._text_font = ctk.CTkFont(size=13)
        self.spinner = ctk.CTkFrame(self, fg_color="transparent")
        self._spin_glyph = ctk.CTkLabel(self.spinner, text="", text_color=theme.ACCENT, font=ctk.CTkFont(size=14), width=18)
        self._spin_glyph.pack(side="left")
        self._spin_text = ctk.CTkLabel(self.spinner, text="", text_color=theme.ACCENT, font=self._text_font)
        self._spin_text.pack(side="left", padx=(4, 0))
        self._spin_meta = ctk.CTkLabel(self.spinner, text="", text_color=theme.TEXT_FAINT, font=self._text_font)
        self._spin_meta.pack(side="left", padx=(6, 0))

    def clear(self):
        self.stop_spinner()
        for child in self.winfo_children():
            if child is not self.spinner:
                child.destroy()
        self._row = 0
        self._current = None

    def prompt(self, text):
        """The request that started the run, shown like a user message in Claude Code."""
        label = self._wrap(ctk.CTkLabel(self, text=f">  {text}", font=self._mono, text_color=theme.TEXT_MUTED,
                                        anchor="w", justify="left"), inset=40)
        label.grid(row=self._row, column=0, columnspan=2, sticky="ew", pady=(4, 6))
        self._row += 1
        self._place_spinner()

    def step(self, text, state="running"):
        """Adds a ● line; the previous running step is marked done. Returns nothing; use detail() for results."""
        self.finish("done")
        bullet = ctk.CTkLabel(self, text="●", width=18, font=ctk.CTkFont(size=12),
                              text_color=theme.ACCENT if state == "running" else self._state_color(state))
        bullet.grid(row=self._row, column=0, sticky="nw", pady=(7, 0))
        label = self._wrap(ctk.CTkLabel(self, text=text, font=self._text_font, text_color=theme.TEXT,
                                        anchor="w", justify="left"), inset=56)
        label.grid(row=self._row, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
        self._row += 1
        if state == "running":
            self._current = bullet
            self.current_step = text
            self._activity = theme.SPINNER_VERBS[self._verb_index % len(theme.SPINNER_VERBS)]
            self._verb_index += 1
        self._place_spinner()

    def detail(self, text, tone="muted"):
        """Adds a ⎿ result line under the latest step."""
        color = {"muted": theme.TEXT_MUTED, "success": theme.SUCCESS, "error": theme.ERROR,
                 "warning": theme.WARNING}.get(tone, theme.TEXT_MUTED)
        label = self._wrap(ctk.CTkLabel(self, text=f"⎿  {text}", font=self._mono, text_color=color,
                                        anchor="w", justify="left"), inset=80)
        label.grid(row=self._row, column=1, sticky="ew", padx=(12, 0), pady=(1, 0))
        self._row += 1
        self._place_spinner()

    def finish(self, state="done"):
        if self._current is not None:
            self._current.configure(text_color=self._state_color(state))
            self._current = None

    def start_spinner(self, activity="Working"):
        self._started = time.time()
        self._activity = activity
        self._spinning = True
        self._place_spinner()
        if self._spinner_job is None:
            self._tick()

    def set_meta(self, text):
        self._suffix = text

    def stop_spinner(self):
        self._spinning = False
        if self._spinner_job is not None:
            self.after_cancel(self._spinner_job)
            self._spinner_job = None
        self.spinner.grid_forget()

    def _place_spinner(self):
        if self._spinning:  # keep the spinner as the last line of the feed
            self.spinner.grid(row=self._row, column=0, columnspan=2, sticky="w", pady=(10, 2))

    def _tick(self):
        self._frame = (self._frame + 1) % len(theme.SPINNER_FRAMES)
        self._spin_glyph.configure(text=theme.SPINNER_FRAMES[self._frame])
        self._spin_text.configure(text=f"{self._activity}…")
        meta = format_elapsed(time.time() - self._started)
        self._spin_meta.configure(text=f"({meta}{' · ' + self._suffix if self._suffix else ''})")
        if self._current is not None:  # the running step's dot pulses
            self._current.configure(text_color=theme.ACCENT if self._frame % 4 < 2 else theme.TEXT_FAINT)
        self._spinner_job = self.after(120, self._tick)

    @staticmethod
    def _state_color(state):
        return {"done": theme.SUCCESS, "failed": theme.ERROR, "warning": theme.WARNING,
                "info": theme.TEXT_FAINT, "running": theme.ACCENT}.get(state, theme.TEXT_FAINT)


class EngineRunView(ctk.CTkScrollableFrame):
    """Shared machinery for views that run the engine with --progress: spending limit, Go/Stop, a live
    Claude Code-style activity feed, a result card, and the folder's run history (.foundry/journal.json)."""

    go_text = "Go"
    limit_pref_key = "MAX_BUDGET_USD"

    def __init__(self, master, workspace_root, config_manager, terminal, on_open_settings=None,
                 on_show_details=None, on_status=None, **kwargs):
        super().__init__(master, corner_radius=0, fg_color=theme.BG, **kwargs)
        self.workspace_root = workspace_root
        self.config_manager = config_manager
        self.terminal = terminal
        self.on_open_settings = on_open_settings
        self.on_show_details = on_show_details
        self.on_status = on_status

        self.running = False
        self.stopping = False
        self.run_state = {}

        self.grid_columnconfigure(0, weight=1)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew", pady=(26, 24))
        self.body.grid_columnconfigure(0, weight=1)
        self._row = 0
        self.pad = {"padx": 32}
        self._wrapped = []  # (label, extra inset) pairs that re-wrap to the column width
        self._content_width = 600
        self.body.bind("<Configure>", self._on_resize)

    # ------------------------------------------------------------- layout kit
    def _next_row(self):
        row = self._row
        self._row += 1
        return row

    def add_title(self, title, subtitle):
        """Returns the (title, subtitle) labels so a view can retitle itself."""
        title_label = ctk.CTkLabel(self.body, text=title, font=ctk.CTkFont(size=22, weight="bold"), text_color=theme.TEXT,
                                   anchor="w")
        title_label.grid(row=self._next_row(), column=0, sticky="w", **self.pad)
        subtitle_label = self.wrap(ctk.CTkLabel(self.body, text=subtitle, font=ctk.CTkFont(size=13),
                                                text_color=theme.TEXT_MUTED, justify="left", anchor="w"))
        subtitle_label.grid(row=self._next_row(), column=0, sticky="w", pady=(4, 18), **self.pad)
        return title_label, subtitle_label

    def add_section(self, text, top=18):
        ctk.CTkLabel(self.body, text=text, font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.TEXT).grid(
            row=self._next_row(), column=0, sticky="w", pady=(top, 0), **self.pad)

    def add_hint(self, text=""):
        label = self.wrap(ctk.CTkLabel(self.body, text=text, font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT,
                                       justify="left", anchor="w"))
        label.grid(row=self._next_row(), column=0, sticky="w", pady=(2, 0), **self.pad)
        return label

    def add_limit_row(self, title, hint):
        self.add_section(title)
        limit_row = ctk.CTkFrame(self.body, fg_color="transparent")
        limit_row.grid(row=self._next_row(), column=0, sticky="w", pady=(6, 2), **self.pad)
        ctk.CTkLabel(limit_row, text="$", font=ctk.CTkFont(size=15), text_color=theme.TEXT_MUTED).pack(side="left", padx=(0, 6))
        self.limit_entry = ctk.CTkEntry(limit_row, width=90, height=34, font=ctk.CTkFont(size=13))
        self.limit_entry.insert(0, self._default_limit())
        self.limit_entry.pack(side="left", padx=(0, 12))
        for amount in ("1", "5", "20"):
            ctk.CTkButton(limit_row, text=f"${amount}", width=52, height=30, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda a=amount: self._set_limit(a)).pack(side="left", padx=3)
        self.add_hint(hint)

    def add_go_button(self):
        self.go_button = ctk.CTkButton(self.body, text=self.go_text, width=200, height=40, corner_radius=8,
                                       font=ctk.CTkFont(size=14, weight="bold"), command=self._on_go_pressed, **PRIMARY)
        self.go_button.grid(row=self._next_row(), column=0, sticky="w", pady=(22, 8), **self.pad)

    def add_footer(self):
        """A frame pinned under the scrolling area (it doesn't scroll), like a chat composer."""
        # CTkScrollableFrame grids its canvas and scrollbar in row 1 of its outer frame; row 2 is free.
        footer = ctk.CTkFrame(self._parent_frame, corner_radius=0, fg_color=theme.BG)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        return footer

    def add_activity_feed(self, master=None):
        self.feed_card = ctk.CTkFrame(master or self.body, corner_radius=10, fg_color=theme.SURFACE, border_width=1,
                                      border_color=theme.BORDER)
        self.feed_card.grid_columnconfigure(0, weight=1)
        self.feed = ActivityFeed(self.feed_card, self.wrap)
        self.feed.grid(row=0, column=0, sticky="ew", padx=18, pady=(10, 14))
        self.feed_row = self._next_row()

    # Older name kept for subclasses written against the progress card.
    add_progress_card = add_activity_feed

    def add_result_card(self, master=None):
        self.result_card = ctk.CTkFrame(master or self.body, corner_radius=10, fg_color=theme.SURFACE, border_width=1,
                                        border_color=theme.BORDER)
        self.result_card.grid_columnconfigure(0, weight=1)
        self.result_title = self.wrap(ctk.CTkLabel(self.result_card, text="", font=ctk.CTkFont(size=15, weight="bold"),
                                                   text_color=theme.TEXT, anchor="w", justify="left"), inset=40)
        self.result_title.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 4))
        self.result_body = self.wrap(ctk.CTkLabel(self.result_card, text="", font=ctk.CTkFont(size=12),
                                                  text_color=theme.TEXT_MUTED, anchor="w", justify="left"), inset=40)
        self.result_body.grid(row=1, column=0, sticky="ew", padx=18)
        self.result_buttons = ctk.CTkFrame(self.result_card, fg_color="transparent")
        self.result_buttons.grid(row=2, column=0, sticky="w", padx=18, pady=(12, 14))
        self.result_row = self._next_row()

    def add_history(self, title, subtitle=None):
        header = ctk.CTkFrame(self.body, fg_color="transparent")
        header.grid(row=self._next_row(), column=0, sticky="ew", pady=(26, 2), **self.pad)
        ctk.CTkLabel(header, text=title, font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.TEXT).pack(side="left")
        self.history_header = header
        if subtitle:
            self.add_hint(subtitle)
        self.history_frame = ctk.CTkFrame(self.body, fg_color="transparent")
        self.history_frame.grid(row=self._next_row(), column=0, sticky="ew", pady=(8, 0), **self.pad)
        self.history_frame.grid_columnconfigure(0, weight=1)

    def render_history(self, entries, empty_text):
        for child in self.history_frame.winfo_children():
            child.destroy()
        if not entries:
            ctk.CTkLabel(self.history_frame, text=empty_text, font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT).grid(
                row=0, column=0, sticky="w")
            return
        for idx, entry in enumerate(reversed(entries[-20:])):
            row = ctk.CTkFrame(self.history_frame, corner_radius=8, fg_color=theme.SURFACE)
            row.grid(row=idx, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(1, weight=1)
            outcome = entry.get("outcome")
            ctk.CTkLabel(row, text=OUTCOME_ICONS.get(outcome, "•"), width=22, font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=OUTCOME_COLORS.get(outcome, theme.TEXT_FAINT)).grid(
                row=0, column=0, rowspan=2, padx=(12, 6), pady=8, sticky="n")
            try:
                when = datetime.fromisoformat(entry.get("timestamp", "").replace("Z", "+00:00")).astimezone().strftime("%b %d, %H:%M")
            except ValueError:
                when = ""
            task = " ".join(str(entry.get("task", "")).split())
            self.wrap(ctk.CTkLabel(row, text=task[:120] + ("…" if len(task) > 120 else ""), font=ctk.CTkFont(size=13),
                                   text_color=theme.TEXT, anchor="w", justify="left"), inset=60).grid(
                row=0, column=1, sticky="ew", pady=(8, 0), padx=(0, 12))
            details = f"{when}  ·  {entry.get('summary', '')}  ·  {format_money(float(entry.get('spentUsd', 0) or 0))}"
            self.wrap(ctk.CTkLabel(row, text=details, font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w",
                                   justify="left"), inset=60).grid(row=1, column=1, sticky="ew", pady=(0, 8), padx=(0, 12))

    # ------------------------------------------------------- responsive text
    def wrap(self, label, inset=0):
        """Makes a label wrap at the current column width (minus `inset` for padding inside cards)."""
        self._wrapped.append((label, inset))
        label.configure(wraplength=max(240, self._content_width - inset))
        return label

    def _on_resize(self, event):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = int(event.width / scaling) - 2 * self.pad["padx"] - 8
        if abs(width - self._content_width) < 8:
            return
        self._content_width = width
        alive = []
        for label, inset in self._wrapped:
            if label.winfo_exists():
                label.configure(wraplength=max(240, width - inset))
                alive.append((label, inset))
        self._wrapped = alive

    # --------------------------------------------------------------- helpers
    def _default_limit(self):
        try:
            return f"{float(self.config_manager.get(self.limit_pref_key, '') or self.config_manager.get('MAX_BUDGET_USD', '5')):.2f}"
        except ValueError:
            return "5.00"

    def _set_limit(self, amount):
        self.limit_entry.delete(0, "end")
        self.limit_entry.insert(0, f"{float(amount):.2f}")

    def limit_text(self):
        return self.limit_entry.get()

    def read_limit(self):
        try:
            limit = float(self.limit_text().strip().lstrip("$"))
            if limit > 0:
                return limit
        except ValueError:
            pass
        messagebox.showerror("Foundry", "The spending limit must be a positive amount, like 5 or 2.50.")
        return None

    def thinking_level(self):
        """One setting for every run: chats, Builds and Improve rounds."""
        return thinking_level_for(self.config_manager)

    def thinking_args(self):
        # Passed explicitly: this process's environment may hold an older value than the saved setting.
        return ["--thinking", self.thinking_level()]

    def set_thinking_level(self, level):
        self.remember({THINKING_PREF: level})

    def node_path(self):
        """The Node.js program the engine runs with (Settings › Advanced)."""
        return str(self.config_manager.get("NODE_BINARY_PATH", "node") or "node").strip() or "node"

    def chat_limit(self):
        """The most one chat reply may cost (Settings › Spending limits), as "1.00"."""
        try:
            value = float(str(self.config_manager.get("FOUNDRY_CHAT_BUDGET_USD", "1") or "1").strip().lstrip("$"))
            return f"{value:.2f}" if value > 0 else "1.00"
        except ValueError:
            return "1.00"

    def confirm_stop(self):
        return str(self.config_manager.get("UI_CONFIRM_STOP", "true") or "true").strip().lower() in ("true", "1", "yes")

    def remember(self, updates):
        try:
            if any(self.config_manager.get(k, "") != v for k, v in updates.items()):
                self.config_manager.save(updates)
        except Exception:
            pass  # Remembering preferences is a convenience; never block a run on it.

    def missing_keys(self):
        if str(self.config_manager.get("FOUNDRY_OFFLINE_MODE", "false")).lower() == "true":
            return []
        get = lambda k: str(self.config_manager.get(k, "") or os.environ.get(k, "")).strip()
        missing = []
        if not get("ANTHROPIC_API_KEY"):
            missing.append("Anthropic (Claude)")
        if not get("GEMINI_API_KEY"):
            missing.append("Google (Gemini)")
        if not get("ASTRA_API_KEY") and not get("OPENAI_API_KEY") and not get("ANTHROPIC_API_KEY"):
            missing.append("OpenAI")
        return missing

    def engine_entry(self):
        dist_index = os.path.join(self.workspace_root, "dist", "index.js")
        if not os.path.exists(dist_index):
            messagebox.showerror("Foundry", "The Foundry engine isn't built yet.\n\nIn the Foundry folder, run:\n"
                                            "npm install\nnpm run build")
            return None
        return dist_index

    def open_folder(self, folder):
        if not os.path.isdir(folder):
            messagebox.showinfo("Foundry", f"The folder doesn't exist yet:\n{folder}")
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Foundry", f"Couldn't open the folder: {e}")

    # ------------------------------------------------------------ run control
    def launch(self, args, label, state, status_text="Starting", keep_feed=False):
        """Runs `node dist/index.js <args> --progress`; returns False if nothing was started.
        keep_feed continues the current transcript instead of starting a new one (e.g. rollback after Stop)."""
        dist_index = self.engine_entry()
        if not dist_index:
            return False
        cmd = [self.node_path(), dist_index] + list(args) + ["--progress"]
        env = dict(os.environ, FOUNDRY_DASHBOARD="false")
        started = self.terminal.run_command(cmd, label, env=env, on_line=self._on_engine_line, on_exit=self._on_engine_exit)
        if not started:
            return False
        self.running = True
        self.stopping = False
        self.run_state = {"spent": 0.0, "finished": None, "limit": 0.0, **state}
        self.go_button.configure(text="■  Stop", fg_color=theme.SURFACE_HOVER, hover_color=theme.SELECTED,
                                 text_color=theme.ERROR)
        self.result_card.grid_forget()
        if not keep_feed:
            self.feed.clear()
        self.feed_card.grid(row=self.feed_row, column=0, sticky="ew", padx=self.pad["padx"], pady=(6, 4))
        self.feed.start_spinner(status_text)
        self.update_spend(0.0)
        return True

    def stop(self, question):
        if not self.running:
            return
        if self.confirm_stop() and not messagebox.askyesno("Stop Foundry?", question):
            return
        self.stopping = True
        self.feed.finish("failed")
        self.step("Stopping", state="warning")
        self.terminal.stop_process()

    def step(self, text, state="running"):
        self.feed.step(text, state)
        if self.on_status:
            self.on_status(state=text)

    def detail(self, text, tone="muted"):
        self.feed.detail(text, tone)

    def set_status(self, text, progress=None):
        """Compatibility shim: a status change is a new step in the feed."""
        self.step(text)

    def update_spend(self, spent):
        limit = self.run_state.get("limit", 0)
        self.run_state["spent"] = spent
        if not limit:  # maintenance runs (undo, recovery) spend nothing and have no limit
            self.feed.set_meta("")
            return
        self.feed.set_meta(f"{format_money(spent)} of {format_money(limit)}")
        if self.on_status:
            self.on_status(spent=spent, limit=limit)

    def when_terminal_free(self, callback, attempts=100, on_timeout=None):
        """The exit callback fires before the terminal marks its process finished; wait for it before launching again.
        attempts × 50 ms is the longest wait; on_timeout (if given) runs when the terminal never came free."""
        if not self.winfo_exists():
            return
        if not self.terminal.process_running and self.terminal.active_process is None:
            callback()
        elif attempts > 0:
            self.after(50, lambda: self.when_terminal_free(callback, attempts - 1, on_timeout))
        elif on_timeout:
            on_timeout()

    def _on_engine_line(self, line):
        if not line.startswith(PROGRESS_PREFIX):
            return
        try:
            event = json.loads(line[len(PROGRESS_PREFIX):])
        except ValueError:
            return
        if event.get("type") == "spend":
            self.update_spend(float(event.get("spentUsd", 0) or 0))
        elif event.get("type") == "finished":
            self.run_state["finished"] = event
            if event.get("limitUsd"):
                self.update_spend(float(event.get("spentUsd", self.run_state.get("spent", 0)) or 0))
        else:
            self.handle_event(event)

    def _on_engine_exit(self, return_code):
        finished = self.run_state.get("finished")
        self.running = False
        self.go_button.configure(text=self.go_text, **PRIMARY)
        self.feed.stop_spinner()
        if finished:
            ok = finished.get("outcome") == "done"
            self.feed.finish("done" if ok else "warning" if finished.get("outcome") in ("not-finished", "spending-limit") else "failed")
            self.show_result(finished)
        elif self.stopping:
            self.feed.finish("warning")
            self.on_stopped()
        else:
            self.feed.finish("failed")
            self.show_card("Something went wrong before Foundry could finish.",
                           f"The engine exited unexpectedly (code {return_code}). Click “Show details” to see what happened.")
        self.refresh()
        if self.on_status:
            self.on_status(state="Ready")

    def show_card(self, title, body, buttons=()):
        """buttons: (text, command) pairs; the first is the primary action. "Show details" is always added."""
        self.result_title.configure(text=title)
        self.result_body.configure(text=body)
        for child in self.result_buttons.winfo_children():
            child.destroy()
        for idx, (text, command) in enumerate(buttons):
            style = PRIMARY if idx == 0 else CHIP
            ctk.CTkButton(self.result_buttons, text=text, height=32, command=command, **style).pack(side="left", padx=(0, 8))
        if self.on_show_details:
            ctk.CTkButton(self.result_buttons, text="Show details", height=32, command=self.on_show_details,
                          **CHIP).pack(side="left")
        self.result_card.grid(row=self.result_row, column=0, sticky="ew", padx=self.pad["padx"], pady=(6, 4))

    def add_result_button(self, text, command, primary=False):
        """Adds a button to the result card on screen (e.g. Continue job after a paused step)."""
        if self.result_buttons.winfo_exists():
            ctk.CTkButton(self.result_buttons, text=text, height=32, command=command, **(PRIMARY if primary else CHIP)).pack(side="left", padx=(0, 8))

    def show_keys_needed(self, missing):
        buttons = [("Open Settings", self.on_open_settings)] if self.on_open_settings else []
        self.show_card("Foundry needs your AI keys first.", f"Add these in Settings, then try again: {', '.join(missing)}.", buttons)

    # ------------------------------------------------------- subclass hooks
    def _on_go_pressed(self):
        raise NotImplementedError

    def handle_event(self, event):
        pass

    def show_result(self, event):
        raise NotImplementedError

    def on_stopped(self):
        self.show_card("Stopped.", f"Spent {format_money(self.run_state.get('spent', 0.0))}.")

    def refresh(self):
        pass
