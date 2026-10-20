import os
import re
import time
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, simpledialog

import customtkinter as ctk

import theme
from engine_run_view import (CHIP, OUTCOME_COLORS, OUTCOME_ICONS, PRIMARY, THINKING_LEVELS, EngineRunView,
                             append_journal_entry, format_elapsed, format_money, load_journal, thinking_label)
from project_chat import (AGENT_NAMES, AGENT_ROLES, AGENTS, ChatProcess, chat_path, compose_build_task, load_chat,
                          missing_chat_keys, now_iso, save_chat)
from shell import Tooltip, icon_button, popup_menu
from access_rules import blocked_message, is_blocked
from selectable import SelectableText

EXAMPLES = [
    ("To-do list app", "A command-line to-do list app: add, list, complete and delete tasks, saved to a JSON file."),
    ("Photo renamer", "A Python script that renames the photos in a folder by the date they were taken."),
    ("Fix a bug", "Fix: the program crashes when the input file is empty. It should print a friendly message instead."),
]

# Mirrors the engine's workspace scan (src/engine/convergence.ts) closely enough to tell new from existing projects.
CODE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".py", ".go",
                   ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp")
IGNORED_DIRS = {".git", "node_modules", "dist", "build", "out", "target", "coverage", "__pycache__",
                ".venv", "venv", ".next", "vendor"}

NEW_TITLE = ("Start a new project",
             "Tell Claude what you want to make. Talk it through, and when it sounds right, say go: Claude starts the Build. "
             "An empty folder gets a new project; a folder with code in it gets improved.")
PROJECT_SUBTITLE = "Chat about what to add or change; Claude starts the Build when you say go. Everything here is remembered."

AGENT_COLORS = {"claude": theme.ACCENT, "gemini": ("#3E6FB0", "#8AB4F8"), "openai": theme.SUCCESS}
CHAT_AGENT_PREF = "UI_CHAT_AGENT"
MAX_COLUMN = 760     # width of the centered conversation column, as on Claude's message page
ATTACH_LIMIT = 6000  # characters of a file put into a message
COMMAND_OUTPUT_LIMIT = 4000  # characters of a command's output sent back to the model


def count_code_files(folder, limit=2000):
    if not os.path.isdir(folder):
        return 0
    count = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".foundry")]
        count += sum(1 for f in files if f.lower().endswith(CODE_EXTENSIONS))
        if count >= limit:
            break
    return count


PROJECTS_HOME = os.path.join(os.path.expanduser("~"), "FoundryProjects")
AUTO_FOLDER = re.compile(r"^new-project(-\d+)?$")
NAME_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "for", "to", "of", "in", "on", "at", "by", "with", "from", "into", "as", "that",
    "which", "this", "these", "it", "its", "is", "are", "be", "will", "should", "can", "could", "would", "my", "me",
    "i", "we", "you", "your", "our", "want", "wanted", "like", "need", "make", "build", "create", "write", "please",
    "some", "just", "so", "lets", "let", "using", "use", "very", "really", "simple", "small", "new", "hi", "hey",
}


def projects_home(config_manager=None):
    """Where new projects go: Settings › General, else ~/FoundryProjects."""
    chosen = str(config_manager.get("UI_PROJECTS_HOME", "") or "").strip() if config_manager else ""
    return os.path.normpath(os.path.expanduser(chosen)) if chosen else PROJECTS_HOME


def suggest_new_folder(home=None):
    home = home or PROJECTS_HOME
    candidate, n = os.path.join(home, "new-project"), 2
    while os.path.exists(candidate):
        candidate, n = os.path.join(home, f"new-project-{n}"), n + 1
    return candidate


def folder_name_for(text):
    """A short folder name from a project's first request: "A command-line weather app" -> "command-line-weather-app"."""
    first_line = (text.strip().splitlines() or [""])[0]
    words = [w for w in re.findall(r"[a-z0-9]+", first_line.lower()) if w not in NAME_STOPWORDS and len(w) > 1]
    return "-".join(words[:4])[:40].strip("-")


def plain_text(text):
    """Model replies are Markdown; show them as clean plain text (bullets kept, markup dropped)."""
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if re.match(r"#{1,6}\s", stripped):
            line = stripped.lstrip("#").strip()
        line = re.sub(r"^(\s*)[-*]\s+", r"\1• ", line)
        line = line.replace("**", "").replace("__", "")
        line = re.sub(r"`([^`\n]+)`", r"\1", line)
        lines.append(line)
    return "\n".join(lines).strip()


def stream_preview(text):
    """What the live reply shows while it is written: the text so far, and a calm note instead of a half-written block."""
    parts = text.split("```")
    if len(parts) % 2 == 0:  # inside an open fence
        lang = parts[-1].split("\n", 1)[0].strip().lower()
        what = {"foundry": "the job plan", "prompt": "the prompt", "build": "the build request", "run": "a command",
                "json": "the plan"}.get(lang, "a code block")
        return (plain_text("```".join(parts[:-1])).rstrip() + f"\n\n… writing {what}").strip()
    return plain_text(text)


def split_code(text):
    """[(kind, text)] around ``` fences. kind is "text", "code", "run" (a command a model proposes) or "build"
    (a Build Claude starts)."""
    segments, current, kind = [], [], "text"
    for line in text.splitlines():
        fence = line.strip()
        if fence.startswith("```"):
            if current:
                segments.append((kind, "\n".join(current)))
            current = []
            lang = fence[3:].strip().lower()
            kind = (lang if lang in ("run", "build", "prompt", "foundry") else "code") if kind == "text" else "text"
            continue
        current.append(line)
    if current:
        segments.append((kind, "\n".join(current)))
    return [(k, body.strip("\n") if k != "text" else body.strip()) for k, body in segments if body.strip()]


class ProjectsView(EngineRunView):
    """A project's page, laid out like a Claude session: the conversation with the models and the project's
    runs, oldest first, with a composer pinned at the bottom. Send asks the chosen model(s); Build hands what
    was discussed to Foundry. The sidebar picks the folder (or + New project)."""

    go_text = "Send"
    TEXT_PAD_X, TEXT_PAD_Y = 4, 6  # inside the text widget; the placeholder sits at the same spot
    examples = EXAMPLES
    chat_mode = "project"

    def __init__(self, master, workspace_root, config_manager, terminal, on_project_used=None, folder=None, new=None,
                 on_run_command=None, on_use_prompt=None, on_start_job=None, on_send_step=None, **kwargs):
        """One page per project, like one Claude session per conversation. folder: the project (default: a fresh
        folder for a new project); new: show the new-project heading (default: when the folder has no history).
        on_run_command(command, cwd, on_done, who) runs a command a model proposed, in the Terminal pane."""
        super().__init__(master, workspace_root, config_manager, terminal, **kwargs)
        self.on_run_command = on_run_command
        self.on_project_used = on_project_used
        self.on_use_prompt = on_use_prompt
        self.on_start_job = on_start_job
        self.on_send_step = on_send_step
        self.pad = {"padx": 0}  # the whole column is centered instead (see _center)
        self._side = 24
        self.limit_var = ctk.StringVar(value=self._default_limit())
        saved = self.config_manager.get(CHAT_AGENT_PREF, "claude")
        self.agent = saved if saved in [name for name, _ in AGENTS] else "claude"
        self.folder_hint = ""
        self.folder = os.path.normpath(folder or suggest_new_folder(self._projects_home()))
        self.messages = []
        self.chat = None             # the running ChatProcess
        self.chat_folder = None
        self._chat_answered = False
        self._thinking = None        # (agent, started) while a model is answering
        self._think_verb = "is thinking"
        self._stream = None          # the reply being written: {"agent", "attempt", "text", "widget", "label"}
        self._stream_job = None
        self._live = None            # {"folder", "start"}: the run shown live in this session
        self._job = None             # {"plan", "next", "paused"}: a job plan whose steps run as Builds in order
        self._item_widgets = []
        self._items_row = 0
        self._spin = 0
        self._build_ui()
        self._set_titles(new if new is not None else not self.has_history(self.current_folder()))
        self.refresh()

    # ------------------------------------------------------------------ layout
    def _build_ui(self):
        self.title_label, self.subtitle_label = self.add_title(*NEW_TITLE)

        # Empty state: example requests, like the suggestions on a new Claude session
        self.starter = ctk.CTkFrame(self.body, fg_color="transparent")
        self.starter_row = self._next_row()
        ctk.CTkLabel(self.starter, text="Try an example:", font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT).pack(side="left", padx=(0, 8))
        for name, text in self.examples:
            ctk.CTkButton(self.starter, text=name, height=26, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda t=text: self.set_task(t)).pack(side="left", padx=3)

        # The conversation and the runs, oldest first
        self.transcript = ctk.CTkFrame(self.body, fg_color="transparent", width=1, height=1)
        self.transcript.grid(row=self._next_row(), column=0, sticky="ew")
        self.transcript.grid_columnconfigure(0, weight=1)
        self.add_activity_feed(self.transcript)
        self.add_result_card(self.transcript)

        self.thinking_row = ctk.CTkFrame(self.transcript, fg_color="transparent")
        self._think_glyph = ctk.CTkLabel(self.thinking_row, text="", width=18, font=ctk.CTkFont(size=14), text_color=theme.ACCENT)
        self._think_glyph.pack(side="left")
        self._think_text = ctk.CTkLabel(self.thinking_row, text="", font=ctk.CTkFont(size=13), text_color=theme.ACCENT)
        self._think_text.pack(side="left", padx=(4, 0))

        self._build_composer()

    def _build_composer(self):
        """Claude's message box: a rounded box with + on the left and Build / send on the right, and the model
        picker and spending limit under its edges."""
        footer = self.add_footer()
        self.composer = ctk.CTkFrame(footer, corner_radius=18, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        self.composer.grid(row=0, column=0, sticky="ew", padx=self._side, pady=(8, 2))
        self.composer.grid_columnconfigure(1, weight=1)

        self.task_box = ctk.CTkTextbox(self.composer, height=54, wrap="word", font=ctk.CTkFont(size=13), corner_radius=0,
                                       border_width=0, fg_color=theme.SURFACE)
        self.task_box.grid(row=0, column=0, columnspan=4, sticky="ew", padx=12, pady=(10, 0))
        self.task_box.bind("<Return>", self._on_enter)
        self.task_box.bind("<KeyPress>", self._on_key_press)
        self.task_box.bind("<KeyRelease>", lambda e: self._update_placeholder())
        self.task_box.bind("<<Paste>>", lambda e: self.after_idle(self._update_placeholder), add="+")
        # The typed text and the placeholder share one baseline: same font, same padding, inside the same widget.
        inner = self.task_box._textbox
        inner.configure(padx=self.TEXT_PAD_X, pady=self.TEXT_PAD_Y)
        import tkinter as tk
        self.placeholder = tk.Label(inner, text="", font=inner.cget("font"), anchor="w", justify="left", borderwidth=0,
                                    highlightthickness=0, padx=0, pady=0, cursor="xterm",
                                    bg=theme.mode_color(theme.SURFACE), fg=theme.mode_color(theme.TEXT_FAINT))
        # A click anywhere in the rounded box (the placeholder, the padding, the empty bottom row) lands in the text.
        for widget in (self.placeholder, self.composer):
            widget.bind("<Button-1>", self._focus_composer, add="+")

        icon_button(self.composer, "+", "Add a file to your message", self._attach_file, size=30).grid(
            row=1, column=0, sticky="w", padx=(10, 0), pady=(2, 10))
        # One button, as in Claude: Send, or Stop while a reply or a Build is running. Claude starts Builds itself.
        self.go_button = ctk.CTkButton(self.composer, text=self.go_text, width=72, height=30, corner_radius=15,
                                       font=ctk.CTkFont(size=12, weight="bold"), command=self._on_main_button, **PRIMARY)
        self.go_button.grid(row=1, column=3, padx=(0, 10), pady=(2, 10))
        self.go_tip = Tooltip(self.go_button, "Send (Enter). When you tell Claude to go ahead, it starts the Build.")

        self.under = ctk.CTkFrame(footer, fg_color="transparent")
        self.under.grid(row=1, column=0, sticky="ew", padx=self._side + 4, pady=(0, 10))
        self.under.grid_columnconfigure(3, weight=1)  # model, thinking and folder on the left, the limit on the right
        self.agent_button = self._flat_button(self.under, "", self._agent_menu)
        self.agent_button.grid(row=0, column=0, sticky="w")
        Tooltip(self.agent_button, "Who answers when you press Send")
        self.thinking_button = self._flat_button(self.under, "", self._thinking_menu)
        self.thinking_button.grid(row=0, column=1, sticky="w", padx=(4, 0))
        Tooltip(self.thinking_button, "How hard the models think, in chats and Builds (and Improve). "
                                      "Higher is more careful, slower and costs more.")
        self.folder_button = self._flat_button(self.under, "", self._folder_menu)
        self.folder_button.grid(row=0, column=2, sticky="w", padx=(4, 0))
        self.folder_tip = Tooltip(self.folder_button, "")
        self.usage_label = ctk.CTkLabel(self.under, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT, height=24)
        self.usage_label.grid(row=0, column=3, sticky="e", padx=(8, 8))
        self.usage_tip = Tooltip(self.usage_label, "")
        self.limit_button = self._flat_button(self.under, "", self._limit_menu)
        self.limit_button.grid(row=0, column=4, sticky="e")
        Tooltip(self.limit_button, "Build spending limit: Foundry stops before it spends more than this")
        self.limit_var.trace_add("write", lambda *_: self._show_choices())
        self._show_choices()
        self._parent_canvas.bind("<Configure>", lambda e: self._center(), add="+")

    @staticmethod
    def _flat_button(master, text, command):
        return ctk.CTkButton(master, text=text, width=10, height=24, corner_radius=6, font=ctk.CTkFont(size=12),
                             fg_color="transparent", hover_color=theme.SURFACE_HOVER, text_color=theme.TEXT_MUTED,
                             command=command)

    def _center(self):
        """Keeps the conversation and the message box in one centered column, as on Claude's message page."""
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = self._parent_canvas.winfo_width() / scaling
        side = max(24, int((width - MAX_COLUMN) / 2))
        if side == self._side:
            return
        self._side = side
        scrollbar = int(self._scrollbar.winfo_width() / scaling)
        self.body.grid(padx=side)
        self.composer.grid(padx=(side, side + scrollbar))
        self.under.grid(padx=(side + 4, side + scrollbar + 4))

    def _show_choices(self):
        label = next((text for name, text in AGENTS if name == self.agent), AGENTS[0][1])
        self.agent_button.configure(text=f"{label}  ⌄")
        self.thinking_button.configure(text=f"Thinking: {thinking_label(self.thinking_level())}  ⌄")
        try:
            limit = f"${float(self.limit_var.get().strip().lstrip('$')):.2f}"
        except ValueError:
            limit = self.limit_var.get()
        self.limit_button.configure(text=f"Build limit {limit}  ⌄")

    def _menu_below(self, button, items):
        popup_menu(button, items, button.winfo_rootx(), button.winfo_rooty() + button.winfo_height() + 2)

    def _agent_menu(self):
        self._menu_below(self.agent_button, [(f"{'✓' if name == self.agent else '   '}  {label}", lambda n=name: self.set_agent(n))
                                             for name, label in AGENTS])

    def _thinking_menu(self):
        current = self.thinking_level()
        self._menu_below(self.thinking_button, [
            (f"{'✓' if level == current else '   '}  {label}  ·  {hint}", lambda l=level: self.set_thinking(l))
            for level, label, hint in THINKING_LEVELS])

    def set_thinking(self, level):
        self.set_thinking_level(level)
        self._show_choices()

    def set_agent(self, name):
        self.agent = name
        self._show_choices()
        self.remember({CHAT_AGENT_PREF: name})

    def _limit_menu(self):
        items = [(f"${amount}", lambda a=amount: self.limit_var.set(f"{a:.2f}")) for amount in (1, 5, 10, 20, 50)]
        items += [None, ("Other amount…", self._ask_limit)]
        self._menu_below(self.limit_button, items)

    def _folder_menu(self):
        items = [("Show in Explorer", lambda: self.open_folder(self.current_folder()))]
        if self.can_change_folder():
            items.insert(0, ("Choose another folder…", self.choose_folder))
        self._menu_below(self.folder_button, items)

    def _ask_limit(self):
        value = simpledialog.askstring("Build spending limit", "Foundry stops a Build before it spends more than this many dollars:",
                                       initialvalue=self.limit_var.get(), parent=self)
        if value is None:
            return
        try:
            if float(value.strip().lstrip("$")) > 0:
                self.limit_var.set(f"{float(value.strip().lstrip('$')):.2f}")
                return
        except ValueError:
            pass
        messagebox.showerror("Foundry", "The spending limit must be a positive amount, like 5 or 2.50.")

    def limit_text(self):
        return self.limit_var.get()

    def _attach_file(self):
        folder = self.current_folder()
        path = filedialog.askopenfilename(initialdir=folder if os.path.isdir(folder) else os.path.expanduser("~"),
                                          title="Add a file to your message")
        if path:
            self.attach_file(path)

    def attach_file(self, path):
        """Puts a file's text into the message box (also the Editor's "Add Context")."""
        if is_blocked(self.workspace_root, os.path.dirname(path)):
            messagebox.showinfo("Foundry", blocked_message(os.path.dirname(path)))
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(ATTACH_LIMIT)
        except OSError as e:
            messagebox.showerror("Foundry", f"Couldn't read {path}: {e}")
            return
        try:
            rel = os.path.relpath(path, self.current_folder())
        except ValueError:  # different drive on Windows
            rel = path
        if rel.startswith(".."):
            rel = path
        more = "\n[cut: the file is longer]" if len(content) >= ATTACH_LIMIT else ""
        self.add_to_composer(f"Here is {rel}:\n```\n{content}{more}\n```")

    # -------------------------------------------------------------- public API
    def set_task(self, text):
        """Puts text in the composer (examples, specs sent from the Editor, diagnostics)."""
        self.task_box.delete("1.0", "end")
        self.task_box.insert("end", text.strip())
        self.task_box.focus_set()
        self._update_placeholder()

    def add_to_composer(self, text):
        current = self.task_box.get("1.0", "end").strip()
        self.set_task(f"{current}\n\n{text}" if current else text)

    def go(self):
        self._on_go_pressed()

    def send_text(self, text):
        """Sends a message as if typed (the Launch button's "Make a launcher" uses it)."""
        self.set_task(text)
        self._on_send_pressed()

    def _projects_home(self):
        return projects_home(self.config_manager)

    def current_folder(self):
        return os.path.abspath(os.path.expanduser(self.folder)) if self.folder else ""

    def new_project(self):
        """Blank page for + New project: an unused folder under ~/FoundryProjects and an empty conversation."""
        self._set_folder(suggest_new_folder(self._projects_home()))
        self.task_box.delete("1.0", "end")
        self._set_titles(new=True)
        self.refresh()
        self.after_idle(self.task_box.focus_set)

    def open_project(self, folder):
        if os.path.normcase(os.path.abspath(folder)) != os.path.normcase(self.current_folder()):
            self.task_box.delete("1.0", "end")  # a request typed for another project doesn't carry over
        self._set_folder(folder)
        self._set_titles(new=False)
        self.refresh()

    def _set_folder(self, folder):
        self.folder = os.path.normpath(folder)

    def _set_titles(self, new):
        """A new project gets a heading, like Claude's empty session; a project's name is in the top bar instead."""
        self._is_new = new
        if new:
            self.title_label.configure(text=NEW_TITLE[0])
            self.subtitle_label.configure(text=NEW_TITLE[1])
            self.title_label.grid()
            self.subtitle_label.grid()
        else:
            self.title_label.configure(text=os.path.basename(os.path.normpath(self.folder)) or self.folder)
            self.subtitle_label.configure(text=PROJECT_SUBTITLE)
            self.title_label.grid_remove()
            self.subtitle_label.grid_remove()

    def has_history(self, folder):
        return bool(load_chat(folder) or load_journal(folder))

    # ------------------------------------------------------------------ folder
    def can_change_folder(self):
        """Only a new project that hasn't started yet can move to another folder."""
        return getattr(self, "_is_new", True) and not self.has_history(self.current_folder())

    def choose_folder(self):
        folder = self.current_folder()
        start = folder if os.path.isdir(folder) else os.path.expanduser("~")
        chosen = filedialog.askdirectory(initialdir=start, title="Choose a project folder (an empty folder starts a new project)")
        if not chosen:
            return
        if is_blocked(self.workspace_root, chosen):
            messagebox.showinfo("Foundry", blocked_message(chosen))
            return
        self._set_folder(chosen)
        if self.has_history(self.current_folder()):
            self._set_titles(new=False)
        self.refresh()
        if self.on_status:  # the top bar and the side bar follow the folder
            self.on_status(project=self.current_folder())

    def refresh(self):
        folder = self.current_folder()
        self.messages = load_chat(folder) if folder else []
        code_files = count_code_files(folder)
        if os.path.normcase(folder) == os.path.normcase(self.workspace_root):
            self.folder_hint = "⚠ This is Foundry's own folder: use Improve in the sidebar to improve Foundry itself."
        elif code_files == 0:
            self.folder_hint = "New project: Foundry creates it in this folder."
        else:
            self.folder_hint = f"Existing project: {code_files}{'+' if code_files >= 2000 else ''} code files. Foundry changes what's there."
        name = os.path.basename(folder) or folder
        self.folder_button.configure(text=f"▸ {name}  ⌄")
        self.folder_tip.text = f"{folder}\n{self.folder_hint}" + ("\nClick to pick another folder" if self.can_change_folder() else "")
        self._update_placeholder()
        self._render()
        self._scroll_to_end()
        self.refresh_usage()

    def _focus_composer(self, _event=None):
        self.task_box.focus_set()
        self.task_box.mark_set("insert", "end-1c")
        self.task_box.see("insert")
        return "break"

    def _fit_composer(self):
        """Grows the message box with its text, up to about 8 lines, then it scrolls (like Claude's composer)."""
        inner = getattr(self.task_box, "_textbox", None)
        if inner is None:
            return
        try:
            lines = int((inner.count("1.0", "end-1c", "displaylines") or (0,))[0]) + 1
        except Exception:
            lines = 1
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        try:
            import tkinter.font as tkfont
            line_h = tkfont.Font(font=inner.cget("font")).metrics("linespace") / scaling
        except Exception:
            line_h = 20
        height = int(max(54, min(8 * line_h + 14, lines * line_h + 14)))
        if abs(height - getattr(self, "_composer_height", 54)) >= 2:
            self._composer_height = height
            self.task_box.configure(height=height)

    def _on_key_press(self, event):
        if event.char and event.char.isprintable():  # hide the placeholder before the first letter shows
            self.placeholder.place_forget()

    def _update_placeholder(self):
        """Shown while the box is empty, as in Claude's composer."""
        self._fit_composer()
        if self.task_box.get("1.0", "end").strip():
            self.placeholder.place_forget()
            return
        fresh = not self.messages and not load_journal(self.current_folder())
        lead = "Describe what you want to build" if fresh else "Reply, or describe what to change"
        self.placeholder.configure(text=f"{lead}. Enter to send, Shift+Enter for a new line")
        self.placeholder.place(x=0, y=0, anchor="nw")  # place() is relative to the text area, which already has the padding

    # -------------------------------------------------------------- transcript
    def _render(self):
        """Rebuilds the conversation and the runs, oldest first. The run shown live goes where it started."""
        for widget in self._item_widgets:
            widget.destroy()
        self._item_widgets = []
        self._wrapped = [(label, inset) for label, inset in self._wrapped if label.winfo_exists()]
        folder = self.current_folder()
        live = self._live if self._live and os.path.normcase(self._live["folder"]) == os.path.normcase(folder) else None

        items = [(m.get("timestamp", ""), 0, "chat", m) for m in self.messages if m.get("role") in ("user", "assistant", "notice")]
        for entry in load_journal(folder) if folder else []:
            if live and str(entry.get("timestamp", "")) >= live["start"]:
                continue  # this session's run: shown live below, with its activity feed
            items.append((str(entry.get("timestamp", "")), 1, "run", entry))
        if live:
            items.append((live["start"], 2, "live", None))
        items.sort(key=lambda item: (item[0], item[1]))

        feed_shown = self.feed_card.winfo_manager() == "grid"
        result_shown = self.result_card.winfo_manager() == "grid"
        row = 0
        for _, _, kind, data in items:
            if kind == "live":
                self.feed_row, self.result_row = row, row + 1
                if feed_shown:
                    self.feed_card.grid(row=row, column=0, sticky="ew", padx=self.pad["padx"], pady=(6, 4))
                if result_shown:
                    self.result_card.grid(row=row + 1, column=0, sticky="ew", padx=self.pad["padx"], pady=(6, 4))
                row += 2
                continue
            self._place(self._message_widget(data) if kind == "chat" else self._run_widget(data), row)
            row += 1
        if not live:
            self.feed_card.grid_remove()
            self.result_card.grid_remove()
            self.feed_row = self.result_row = row
        self._items_row = row
        self._place_thinking()
        if items or self._thinking_here():
            self.starter.grid_remove()
        else:
            self.starter.grid(row=self.starter_row, column=0, sticky="w", pady=(0, 8), **self.pad)

    def _place(self, widget, row):
        widget.grid(row=row, column=0, sticky="ew", padx=self.pad["padx"], pady=(8, 4))
        self._item_widgets.append(widget)

    def _append(self, message):
        """Adds one new message at the end without rebuilding the transcript."""
        self.starter.grid_remove()
        self._place(self._message_widget(message), self._items_row)
        self._items_row += 1
        self._place_thinking()
        self._scroll_to_end()

    def _scroll_to_end(self, only_if_at_bottom=False):
        if only_if_at_bottom and self._parent_canvas.yview()[1] < 0.97:
            return
        def scroll():
            if self.winfo_exists():
                self.update_idletasks()  # let the new rows take their size first
                self._parent_canvas.yview_moveto(1.0)
        self.after(40, scroll)
        self.after(250, scroll)  # wrapped labels settle their height one pass later

    def _text(self, master, text, size=13, mono=False, color=None, bg=None, shrink=False, inset=60):
        """Selectable message text (drag to select, Ctrl+C, right-click menu)."""
        widget = SelectableText(master, text, size=size, mono=mono, color=color, bg=bg, shrink=shrink,
                                max_width_px=max(240, self._content_width - inset) if shrink else None,
                                on_copy_to_box=self.add_to_composer)
        if shrink:
            self._bubbles = [b for b in getattr(self, "_bubbles", []) if b[0].winfo_exists()] + [(widget, inset)]
        return widget

    # ------------------------------------------------------------ usage
    @staticmethod
    def _fmt_tokens(n):
        n = int(n or 0)
        return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.1f}k" if n >= 1000 else str(n)

    def refresh_usage(self):
        """The counter under the message box: this chat's tokens and cost, live while a reply or a Build runs."""
        if not hasattr(self, "usage_label") or not self.usage_label.winfo_exists():
            return
        replies = [m for m in self.messages if m.get("role") == "assistant"]
        tok_in = sum(int(m.get("inputTokens", 0) or 0) for m in replies)
        tok_out = sum(int(m.get("outputTokens", 0) or 0) for m in replies)
        cost = sum(float(m.get("spentUsd", 0) or 0) for m in replies)
        live = getattr(self, "_chat_usage", None)
        writing = ""
        if self.chat is not None and live is not None:
            tok_in += live["in"]
            tok_out += live["out"]
            cost += live["usd"]
            if live["stream_chars"]:
                writing = f"  ·  writing ~{self._fmt_tokens(live['stream_chars'] // 4)}"
        parts = [f"↑{self._fmt_tokens(tok_in)} ↓{self._fmt_tokens(tok_out)} tokens · ${cost:.3f}{writing}"] if (tok_in or tok_out or writing) else []
        if self.running and self.run_state.get("tokens"):
            b_in, b_out = self.run_state["tokens"]
            parts.append(f"Build ↑{self._fmt_tokens(b_in)} ↓{self._fmt_tokens(b_out)} · ${self.run_state.get('spent', 0.0):.2f}")
        self.usage_label.configure(text="   ".join(parts), text_color=theme.ACCENT if (self.chat is not None or self.running) else theme.TEXT_FAINT)
        self.usage_tip.text = ("Tokens sent (↑, the conversation and instructions) and received (↓, including thinking) by the "
                               "models in this chat, and what they cost. Counts up live while a reply is written or a Build runs.")

    def _copy_menu(self, widget, text):
        def copy():
            self.clipboard_clear()
            self.clipboard_append(text)
        widget.bind("<Button-3>", lambda e: popup_menu(widget, [("Copy", copy), ("Copy to the box below", lambda: self.add_to_composer(text))]), add="+")

    # ------------------------------------------------------------- commands
    def _command_card(self, master, message, index, command, agent):
        """A command a model proposed: it runs only when the user clicks Run."""
        state = (message.get("ran") or {}).get(str(index))
        card = ctk.CTkFrame(master, corner_radius=8, fg_color=theme.CODE_BG, border_width=1, border_color=theme.BORDER)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=f"›_  {AGENT_NAMES[agent]} wants to run this in the project folder", font=ctk.CTkFont(size=11),
                     text_color=theme.TEXT_FAINT, anchor="w").grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 0))
        label = self._text(card, command, size=12, mono=True, bg=theme.CODE_BG)
        label.grid(row=1, column=0, columnspan=3, sticky="ew", padx=12, pady=(4, 6))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 10))
        if state == "running":
            ctk.CTkLabel(buttons, text="Running in the Terminal…", font=ctk.CTkFont(size=12), text_color=theme.ACCENT).pack(side="left")
        else:
            run_text = "▶  Run" if state is None else "▶  Run again"
            style = PRIMARY if state is None else CHIP
            ctk.CTkButton(buttons, text=run_text, width=84, height=28, corner_radius=14, font=ctk.CTkFont(size=12),
                          command=lambda: self._run_proposed(message, index, command, agent), **style).pack(side="left")
            ctk.CTkButton(buttons, text="Copy", width=60, height=28, corner_radius=14, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda: (self.clipboard_clear(), self.clipboard_append(command))).pack(side="left", padx=(6, 0))
            if state is not None:
                ok = state == 0
                ctk.CTkLabel(buttons, text=f"{'✓' if ok else '✕'} ran · exit code {state}", font=ctk.CTkFont(size=11),
                             text_color=theme.SUCCESS if ok else theme.ERROR).pack(side="left", padx=(10, 0))
        return card

    def _build_card(self, master, message, index, spec):
        """A Build Claude started (or asked to start): its title, what it covers, and how it's going."""
        state = (message.get("builds") or {}).get(str(index))
        lines = [line for line in spec.strip().splitlines() if line.strip()]
        card = ctk.CTkFrame(master, corner_radius=8, fg_color=theme.SURFACE, border_width=1, border_color=theme.ACCENT)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Build", font=ctk.CTkFont(size=11), text_color=theme.ACCENT, anchor="w").grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        self.wrap(ctk.CTkLabel(card, text=lines[0] if lines else "Build", font=ctk.CTkFont(size=13, weight="bold"),
                               text_color=theme.TEXT, justify="left", anchor="w"), inset=110).grid(
            row=1, column=0, sticky="ew", padx=12)
        if len(lines) > 1:
            details = "\n".join(lines[1:7]) + ("\n…" if len(lines) > 7 else "")
            label = self.wrap(ctk.CTkLabel(card, text=plain_text(details), font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
                                           justify="left", anchor="w"), inset=110)
            label.grid(row=2, column=0, sticky="ew", padx=12, pady=(2, 0))
            self._copy_menu(label, spec)
        status = ctk.CTkFrame(card, fg_color="transparent")
        status.grid(row=3, column=0, sticky="w", padx=10, pady=(6, 10))
        if state == "started":
            ctk.CTkLabel(status, text="▶  Started. Its progress is below.", font=ctk.CTkFont(size=12),
                         text_color=theme.ACCENT).pack(side="left")
            ctk.CTkButton(status, text="Build again", width=90, height=26, corner_radius=13, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda: self._start_requested_build(message, force=True)).pack(side="left", padx=(10, 0))
        else:
            if state == "waiting":
                ctk.CTkLabel(status, text="Waiting: Foundry was busy with another run.", font=ctk.CTkFont(size=12),
                             text_color=theme.TEXT_MUTED).pack(side="left", padx=(2, 10))
            ctk.CTkButton(status, text="▶  Start build", width=104, height=28, corner_radius=14, font=ctk.CTkFont(size=12),
                          command=lambda: self._start_requested_build(message, force=True), **PRIMARY).pack(side="left")
        return card

    def _start_requested_build(self, message, force=False):
        """Claude's reply asked for a Build (a ```build block): start it, unless Foundry is busy."""
        target = self._find_message(message)
        builds = [(i, body) for i, (kind, body) in enumerate(split_code(str(target.get("text", "")))) if kind == "build"]
        if not builds:
            return
        index, spec = builds[0]
        states = target.setdefault("builds", {})
        if states.get(str(index)) == "started" and not force:
            return
        if self.running or self.terminal.process_running or self.terminal.active_process is not None:
            states[str(index)] = "waiting"
            save_chat(self.current_folder(), self.messages)
            self._render()
            return
        if self._build(spec.strip()):
            states[str(index)] = "started"
        save_chat(self.current_folder(), self.messages)
        self._render()
        self._scroll_to_end()

    def _find_message(self, message):
        """The same message in the current list (it may have been reloaded from disk since)."""
        for candidate in self.messages:
            if candidate is message or (candidate.get("timestamp") == message.get("timestamp")
                                        and candidate.get("role") == message.get("role")):
                return candidate
        return message

    def _run_proposed(self, message, index, command, agent):
        if not self.on_run_command:
            return
        folder = self.current_folder()
        if is_blocked(self.workspace_root, folder):
            messagebox.showinfo("Foundry", blocked_message(folder))
            return
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Foundry", f"Couldn't open {folder}: {e}")
            return
        target = self._find_message(message)
        target.setdefault("ran", {})[str(index)] = "running"
        save_chat(folder, self.messages)
        self._render()
        self.on_run_command(command, folder, lambda code, output: self._command_done(target, index, command, agent, code, output),
                            AGENT_NAMES[agent])

    def _command_done(self, message, index, command, agent, code, output):
        """The command's output goes back into the conversation, and the model that asked for it carries on."""
        if not self.winfo_exists():
            return
        code = -1 if code is None else code
        target = self._find_message(message)
        target.setdefault("ran", {})[str(index)] = code
        tail = output.strip()[-COMMAND_OUTPUT_LIMIT:]
        cut = "[earlier output left out]\n" if len(output.strip()) > COMMAND_OUTPUT_LIMIT else ""
        self.messages.append({
            "role": "user", "tool": True, "command": command, "exitCode": code, "output": tail, "timestamp": now_iso(),
            "text": f"I ran `{command}` in the project folder. Exit code {code}. Output:\n```\n{cut}{tail or '(no output)'}\n```",
        })
        save_chat(self.current_folder(), self.messages)
        self._render()
        self._scroll_to_end()
        if not self.chat and not self._missing_chat_keys(agent):
            self._start_chat(agent)

    def _command_result(self, m):
        frame = ctk.CTkFrame(self.transcript, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)
        code = m.get("exitCode")
        ok = code == 0
        ctk.CTkLabel(frame, text=f"{'✓' if ok else '✕'}  ran {m.get('command', '')}  ·  exit code {code}",
                     font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11), text_color=theme.SUCCESS if ok else theme.ERROR,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=(24, 0))  # lines up with the replies' text
        lines = str(m.get("output", "")).splitlines()
        shown = "\n".join(lines[-12:]) if lines else "(no output)"
        if len(lines) > 12:
            shown = f"… {len(lines) - 12} more lines (all of it is in the Terminal tab)\n" + shown
        label = self.wrap(ctk.CTkLabel(frame, text=shown, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11),
                                       text_color=theme.TEXT_MUTED, justify="left", anchor="w"), inset=40)
        label.grid(row=1, column=0, sticky="ew", padx=(42, 0), pady=(2, 0))
        self._copy_menu(label, str(m.get("output", "")))
        return frame

    def _reply_header(self, agent):
        """A model's reply starts with a colored dot and who it is (the three models share one conversation)."""
        frame = ctk.CTkFrame(self.transcript, fg_color="transparent")
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text="●", width=18, font=ctk.CTkFont(size=12), text_color=AGENT_COLORS[agent]).grid(
            row=0, column=0, sticky="nw", pady=(1, 0))
        ctk.CTkLabel(frame, text=f"{AGENT_NAMES[agent]} · {AGENT_ROLES[agent]}", font=ctk.CTkFont(size=12),
                     text_color=theme.TEXT_FAINT, anchor="w").grid(row=0, column=1, sticky="w", padx=(6, 0))
        return frame

    def _reply_frame(self, agent):
        """The live reply while it is being written: header plus one growing text label."""
        frame = self._reply_header(agent)
        label = self._text(frame, "")
        label.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))
        return frame, label

    def _prompt_card(self, master, body):
        """A prompt a model wrote (a ```prompt block): the product of the prompt studio, ready to copy."""
        card = ctk.CTkFrame(master, corner_radius=8, fg_color=theme.SURFACE, border_width=1, border_color=theme.ACCENT)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text="Prompt", font=ctk.CTkFont(size=11), text_color=theme.ACCENT, anchor="w").grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        label = self._text(card, body, size=12, mono=True, bg=theme.SURFACE)
        label.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 6))
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 10))

        def copy(button):
            self.clipboard_clear()
            self.clipboard_append(body)
            button.configure(text="Copied")
            self.after(1500, lambda: button.configure(text="Copy") if button.winfo_exists() else None)
        copy_button = ctk.CTkButton(buttons, text="Copy", width=64, height=28, corner_radius=14, font=ctk.CTkFont(size=12), **PRIMARY)
        copy_button.configure(command=lambda: copy(copy_button))
        copy_button.pack(side="left")
        if self.on_use_prompt:
            ctk.CTkButton(buttons, text="Use in a new project", width=140, height=28, corner_radius=14, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda: self.on_use_prompt(body)).pack(side="left", padx=(6, 0))
        return card

    # ------------------------------------------------------------------ jobs
    def queue_job(self, plan, start=0):
        """Runs a job plan (from the prompt studio) as Builds, one step after another. A step that doesn't finish pauses the job."""
        self._job = {"plan": plan, "next": start, "paused": False}
        self._run_next_job_step()

    def job_in_progress(self):
        return self._job is not None and not self._job.get("paused")

    def _apply_step_settings(self, step):
        if step.get("thinking"):
            self.set_thinking(step["thinking"])
        if step.get("limit"):
            self.limit_var.set(f"{float(step['limit']):.2f}")

    def _job_notice(self, text):
        folder = self.current_folder()
        try:
            os.makedirs(folder, exist_ok=True)
            self.messages.append({"role": "notice", "job": True, "text": text, "timestamp": now_iso()})
            save_chat(folder, self.messages)
            self._append(self.messages[-1])
        except OSError:
            pass

    def _run_next_job_step(self):
        job = self._job
        if job is None:
            return
        plan, index = job["plan"], job["next"]
        steps = plan["steps"]
        if index >= len(steps):
            self._job = None
            self._job_notice(f"Job “{plan['job']}” done: all {len(steps)} steps finished.")
            return
        step = steps[index]
        self._apply_step_settings(step)
        self._name_new_project(step["build"].splitlines()[0] if step["build"].strip() else step["title"])  # before the folder exists
        self._job_notice(f"Job “{plan['job']}”: step {index + 1} of {len(steps)}, {step['title']}"
                         + (f" · Thinking {thinking_label(step['thinking'])}" if step.get("thinking") else "")
                         + (f" · limit ${float(step['limit']):.2f}" if step.get("limit") else ""))
        if not self._build(step["build"]):
            job["paused"] = True
            self._job_notice("The job is paused: the step couldn't start (Foundry is busy, or something is missing). "
                             "Use Continue job when it's ready.")
            self.add_result_button("Continue job", self.continue_job, primary=True)

    def _pause_job_busy(self):
        if self._job is None:
            return
        self._job["paused"] = True
        self._job_notice("The job is paused: Foundry's terminal stayed busy. Use Continue job when it's free.")
        self.add_result_button("Continue job", self.continue_job, primary=True)

    def continue_job(self):
        """Resumes a paused job at the step that didn't finish."""
        if self._job is None or self.running:
            return
        self._job["paused"] = False
        self._run_next_job_step()

    def skip_job_step(self):
        if self._job is None or self.running:
            return
        self._job["next"] += 1
        self._job["paused"] = False
        self._run_next_job_step()

    def _after_job_build(self, outcome):
        """The step's Build ended: on success the next step starts once the terminal is free; otherwise the job pauses."""
        job = self._job
        if job is None:
            return
        plan = job["plan"]
        if outcome == "done":
            job["next"] += 1
            if job["next"] >= len(plan["steps"]):
                self._run_next_job_step()
            else:
                self.when_terminal_free(self._run_next_job_step, attempts=600, on_timeout=self._pause_job_busy)
            return
        job["paused"] = True
        index = job["next"]
        self._job_notice(f"Job “{plan['job']}” paused at step {index + 1} ({plan['steps'][index]['title']}): it didn't finish. "
                         "Tell Claude what to fix, or use Continue job to try the step again, or Skip step.")
        self.add_result_button("Continue job", self.continue_job, primary=True)
        self.add_result_button("Skip step", self.skip_job_step)

    @staticmethod
    def _looks_like_plan(segment):
        """A job plan a model fenced as ```json (or bare ```) instead of ```foundry."""
        text = segment.strip()
        if not text.startswith("{") or '"steps"' not in text:
            return False
        from prompt_store import parse_job_plan
        return parse_job_plan(text) is not None

    def _job_card(self, master, body):
        """A job plan a model wrote (a ```foundry block): the steps, each a Build request, with Start this job."""
        from prompt_store import parse_job_plan
        plan = parse_job_plan(body)
        card = ctk.CTkFrame(master, corner_radius=8, fg_color=theme.SURFACE, border_width=1, border_color=theme.ACCENT)
        card.grid_columnconfigure(0, weight=1)
        if plan is None:
            ctk.CTkLabel(card, text="Job plan (couldn't be read)", font=ctk.CTkFont(size=11), text_color=theme.WARNING, anchor="w").grid(
                row=0, column=0, sticky="w", padx=12, pady=(8, 0))
            label = self.wrap(ctk.CTkLabel(card, text=body[:1500], font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11), text_color=theme.TEXT_MUTED,
                                           justify="left", anchor="w"), inset=110)
            label.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 8))
            self._copy_menu(label, body)
            return card
        steps = plan["steps"]
        total = sum(s["limit"] for s in steps)
        head = f"Job plan · {len(steps)} step{'s' if len(steps) != 1 else ''}" + (f" · {plan['language']}" if plan["language"] else "") \
            + (f" · about ${total:.0f} in limits" if total else "") + (" · existing project" if plan["project"] == "existing" else "")
        ctk.CTkLabel(card, text=head, font=ctk.CTkFont(size=11), text_color=theme.ACCENT, anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        self.wrap(ctk.CTkLabel(card, text=plan["job"], font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.TEXT, justify="left",
                               anchor="w"), inset=110).grid(row=1, column=0, sticky="ew", padx=12)
        row = 2
        for index, step in enumerate(steps):
            line = ctk.CTkFrame(card, fg_color="transparent")
            line.grid(row=row, column=0, sticky="ew", padx=12, pady=(6, 0))
            line.grid_columnconfigure(0, weight=1)
            meta = " · ".join(p for p in (f"Thinking {thinking_label(step['thinking'])}" if step["thinking"] else "",
                                          f"${step['limit']:.0f}" if step["limit"] else "", "optional" if step["optional"] else "") if p)
            self.wrap(ctk.CTkLabel(line, text=f"{index + 1}.  {step['title']}" + (f"   {meta}" if meta else ""), font=ctk.CTkFont(size=12),
                                   text_color=theme.TEXT, justify="left", anchor="w"), inset=230).grid(row=0, column=0, sticky="ew")
            if self.on_send_step:
                ctk.CTkButton(line, text="Send to project", width=112, height=24, corner_radius=12, font=ctk.CTkFont(size=11), **CHIP,
                              command=lambda s=step: self.on_send_step(s)).grid(row=0, column=1, padx=(8, 0))
            detail = self.wrap(ctk.CTkLabel(line, text=plain_text("\n".join(step["build"].splitlines()[1:4]))[:300], font=ctk.CTkFont(size=11),
                                            text_color=theme.TEXT_FAINT, justify="left", anchor="w"), inset=130)
            detail.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(16, 0))
            self._copy_menu(detail, step["build"])
            row += 1
        if plan["notes"]:
            self.wrap(ctk.CTkLabel(card, text=plan["notes"], font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED, justify="left", anchor="w"),
                      inset=110).grid(row=row, column=0, sticky="ew", padx=12, pady=(8, 0))
            row += 1
        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=row, column=0, sticky="w", padx=10, pady=(8, 10))
        if self.on_start_job:
            ctk.CTkButton(buttons, text="▶  Start this job", width=130, height=28, corner_radius=14, font=ctk.CTkFont(size=12), **PRIMARY,
                          command=lambda: self.on_start_job(plan)).pack(side="left")
        ctk.CTkButton(buttons, text="Copy plan", width=84, height=28, corner_radius=14, font=ctk.CTkFont(size=12), **CHIP,
                      command=lambda: (self.clipboard_clear(), self.clipboard_append(body))).pack(side="left", padx=(6, 0))
        return card

    def _message_widget(self, m):
        role = m.get("role")
        text = str(m.get("text", "")).strip()
        if role == "user" and m.get("tool"):
            return self._command_result(m)
        if role == "user":
            outer = ctk.CTkFrame(self.transcript, fg_color="transparent")
            outer.grid_columnconfigure(0, weight=1)
            bubble = ctk.CTkFrame(outer, corner_radius=12, fg_color=theme.SURFACE_HOVER)
            bubble.grid(row=0, column=1, sticky="e")
            label = self._text(bubble, text, bg=theme.SURFACE_HOVER, shrink=True, inset=220)
            label.pack(padx=14, pady=9)
            return outer
        if role == "notice":
            frame = ctk.CTkFrame(self.transcript, fg_color="transparent")
            frame.grid_columnconfigure(0, weight=1)
            job = bool(m.get("job"))
            self.wrap(ctk.CTkLabel(frame, text=f"{'▸' if job else '⚠'}  {text}", font=ctk.CTkFont(size=12),
                                   text_color=theme.TEXT_MUTED if job else theme.WARNING,
                                   justify="left", anchor="w"), inset=120).grid(row=0, column=0, sticky="ew")
            if m.get("retry") and self.messages and self.messages[-1] is m and not self.chat:
                self._flat_button(frame, "↻  Try again", self._retry_chat).grid(row=0, column=1, sticky="ne", padx=(8, 0))
            return frame

        agent = m.get("agent") if m.get("agent") in AGENT_NAMES else "claude"
        frame = self._reply_header(agent)
        row = 1
        for index, (kind, segment) in enumerate(split_code(text) or [("text", text)]):
            if kind == "build":
                self._build_card(frame, m, index, segment).grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
                row += 1
                continue
            if kind == "run":
                card = self._command_card(frame, m, index, segment, agent)
                card.grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
                row += 1
                continue
            if kind == "prompt":
                self._prompt_card(frame, segment).grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
                row += 1
                continue
            if kind == "foundry" or (kind == "code" and self._looks_like_plan(segment)):
                self._job_card(frame, segment).grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
                row += 1
                continue
            if kind == "code":
                block = ctk.CTkFrame(frame, corner_radius=8, fg_color=theme.CODE_BG, border_width=1, border_color=theme.BORDER)
                block.grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=(6, 0))
                label = self._text(block, segment, size=12, mono=True, bg=theme.CODE_BG)
                label.pack(fill="x", padx=12, pady=8)
            else:
                label = self._text(frame, plain_text(segment))
                label.grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=(4, 0))
            row += 1
        meta = " · ".join(p for p in (str(m.get("model", "")), format_money(float(m.get("spentUsd", 0) or 0)) if "spentUsd" in m else "") if p)
        if meta:
            ctk.CTkLabel(frame, text=meta, font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w").grid(
                row=row, column=1, sticky="w", padx=(6, 0), pady=(2, 0))
        return frame

    def _run_widget(self, entry):
        row = ctk.CTkFrame(self.transcript, corner_radius=10, fg_color=theme.SURFACE)
        row.grid_columnconfigure(1, weight=1)
        outcome = entry.get("outcome")
        ctk.CTkLabel(row, text=OUTCOME_ICONS.get(outcome, "•"), width=22, font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=OUTCOME_COLORS.get(outcome, theme.TEXT_FAINT)).grid(row=0, column=0, rowspan=2, padx=(12, 6), pady=8, sticky="n")
        try:
            when = datetime.fromisoformat(str(entry.get("timestamp", "")).replace("Z", "+00:00")).astimezone().strftime("%b %d, %H:%M")
        except ValueError:
            when = ""
        task = " ".join(str(entry.get("task", "")).splitlines()[0].split()) if entry.get("task") else "Build"
        self.wrap(ctk.CTkLabel(row, text=f"Build: {task[:140]}{'…' if len(task) > 140 else ''}", font=ctk.CTkFont(size=13),
                               text_color=theme.TEXT, anchor="w", justify="left"), inset=80).grid(row=0, column=1, sticky="ew", pady=(8, 0), padx=(0, 12))
        details = f"{when}  ·  {entry.get('summary', '')}  ·  {format_money(float(entry.get('spentUsd', 0) or 0))}"
        self.wrap(ctk.CTkLabel(row, text=details, font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w",
                               justify="left"), inset=80).grid(row=1, column=1, sticky="ew", pady=(0, 8), padx=(0, 12))
        return row

    # -------------------------------------------------------------------- chat
    def _agent(self):
        return self.agent

    def _missing_chat_keys(self, agent):
        return missing_chat_keys(self.config_manager, agent)  # shared with the Arena page

    def _on_enter(self, event):
        if event.state & 0x0001:  # Shift+Enter: new line
            return None
        self._on_send_pressed()
        return "break"

    def _on_main_button(self):
        """Send, or Stop while a reply or a Build is running."""
        if self.chat:
            self.chat.stop()
        elif self.running:
            self.stop("Stop the Build now? Parts that are already finished are kept; the part in progress is thrown away.")
        else:
            self._on_send_pressed()

    def _sync_button(self):
        if self.chat is not None or self.running:
            self.go_button.configure(text="■  Stop", fg_color=theme.SURFACE_HOVER, hover_color=theme.SELECTED,
                                     text_color=theme.ERROR)
        else:
            self.go_button.configure(text=self.go_text, **PRIMARY)

    def _on_engine_exit(self, return_code):
        finished = self.run_state.get("finished") or {}
        super()._on_engine_exit(return_code)
        self._sync_button()  # a chat reply may still be on its way
        self.refresh_usage()
        if self._job is not None:
            self._after_job_build(finished.get("outcome") if finished else "stopped")

    def _on_send_pressed(self):
        if self.chat:  # one reply at a time; Stop is on the button
            return
        text = self.task_box.get("1.0", "end").strip()
        if not text:
            self.task_box.focus_set()
            return
        folder = self.current_folder()
        if not folder:
            messagebox.showinfo("Foundry", "Pick a project folder first.")
            return
        agent = self._agent()
        missing = self._missing_chat_keys(agent)
        if missing:
            if messagebox.askyesno("Foundry", f"Add a key for {', '.join(missing)} in Settings first, or pick another model.\n\nOpen Settings?") \
                    and self.on_open_settings:
                self.on_open_settings()
            return
        if not self.engine_entry():
            return
        self._name_new_project(text)
        folder = self.current_folder()
        if is_blocked(self.workspace_root, folder):
            messagebox.showinfo("Foundry", blocked_message(folder))
            return
        try:
            os.makedirs(folder, exist_ok=True)  # like a new Claude session, the project exists from its first message
            message = {"role": "user", "text": text, "timestamp": now_iso()}
            self.messages.append(message)
            save_chat(folder, self.messages)
        except OSError as e:
            messagebox.showerror("Foundry", f"Couldn't save the conversation in {folder}:\n{e}")
            return
        self.task_box.delete("1.0", "end")
        self._update_placeholder()
        if self._is_new:
            self._set_titles(new=False)
        if self.on_project_used:
            self.on_project_used(folder)
        if len(self.messages) == 1:
            self.refresh()  # the first message turns the new-project page into the project's page
        else:
            self._append(message)
        self._start_chat(agent)

    def _chat_command(self, agent, folder):
        """The `foundry chat` command for this page (the prompt studio overrides it)."""
        return [self.node_path(), self.engine_entry(), "chat", "--conversation", chat_path(folder), "--agent", agent, "--target", folder,
                "--budget-usd", self.chat_limit(), "--progress"] + self.thinking_args()

    def _start_chat(self, agent):
        """Asks the model(s) to answer the conversation as saved (it ends with the user's message)."""
        if not self.engine_entry():
            return
        folder = self.current_folder()
        cmd = self._chat_command(agent, folder)
        self.chat_folder = folder
        self._chat_answered = False
        self._chat_usage = {"in": 0, "out": 0, "usd": 0.0, "stream_chars": 0, "base_in": 0, "base_out": 0, "base_usd": 0.0}
        self.chat = ChatProcess(self, cmd, self.workspace_root, dict(os.environ, FOUNDRY_DASHBOARD="false"),
                                self._on_chat_event, self._on_chat_exit)
        self.chat.start()
        self._sync_button()
        self._show_thinking("claude" if agent == "all" else agent)

    def _retry_chat(self):
        """Try again after a failed reply: drops the failure notes and asks again, without a new message."""
        if self.chat:
            return
        missing = self._missing_chat_keys(self._agent())
        if missing:
            messagebox.showinfo("Foundry", f"Add a key for {', '.join(missing)} in Settings first, or pick another model.")
            return
        while self.messages and self.messages[-1].get("role") == "notice":
            self.messages.pop()
        if not self.messages or self.messages[-1].get("role") != "user":
            return
        save_chat(self.current_folder(), self.messages)
        self._render()
        self._start_chat(self._agent())

    def _name_new_project(self, text):
        """Like a Claude session getting its title: a new project's automatic folder (new-project, new-project-2, ...)
        is renamed after its first request, before anything is written, e.g. "command-line-weather-app"."""
        folder = self.current_folder()
        projects = self._projects_home()
        home = os.path.normcase(os.path.abspath(projects))
        if not self._is_new or os.path.exists(folder) or not AUTO_FOLDER.match(os.path.basename(folder)) \
                or os.path.normcase(os.path.dirname(folder)) != home:
            return
        name = folder_name_for(text)
        if not name:
            return
        candidate, n = os.path.join(projects, name), 2
        while os.path.exists(candidate):
            candidate, n = os.path.join(projects, f"{name}-{n}"), n + 1
        self._set_folder(candidate)

    def _record(self, folder, message):
        """Saves a reply in its project's conversation, even if another project is open now."""
        if os.path.normcase(folder) == os.path.normcase(self.current_folder()):
            self.messages.append(message)
            save_chat(folder, self.messages)
            self._append(message)
        else:
            save_chat(folder, load_chat(folder) + [message])
        if (message.get("role") == "assistant" or message.get("spentUsd")) and self.on_status:
            self.on_status(project=folder)  # the chrome follows: Overview's chat cost, the Context pane, the working prompt

    def _on_chat_event(self, event):
        kind = event.get("type")
        live = getattr(self, "_chat_usage", None)
        if live is not None:
            if kind == "spend":  # cumulative for this chat process
                live["in"] = int(event.get("inputTokens", 0) or 0) - live["base_in"]
                live["out"] = int(event.get("outputTokens", 0) or 0) - live["base_out"]
                live["usd"] = float(event.get("spentUsd", 0) or 0) - live["base_usd"]
                live["stream_chars"] = 0
            elif kind == "chat-delta":
                live["stream_chars"] += len(str(event.get("text", "")))
            elif kind == "chat-reply":
                # this reply is now saved with its own counts: move the live baseline past it
                live["base_in"] += int(event.get("inputTokens", 0) or 0)
                live["base_out"] += int(event.get("outputTokens", 0) or 0)
                live["base_usd"] += float(event.get("spentUsd", 0) or 0)
                live["in"] = live["out"] = 0
                live["usd"] = 0.0
                live["stream_chars"] = 0
        self.after_idle(self.refresh_usage)
        folder = self.chat_folder
        agent = event.get("agent") if event.get("agent") in AGENT_NAMES else "claude"
        if kind == "chat-thinking":
            self._show_thinking(agent)
        elif kind == "chat-delta":
            self._stream_text(agent, str(event.get("text", "")), int(event.get("attempt", 1) or 1))
        elif kind == "chat-reply":
            self._chat_answered = True
            self._end_stream()
            self._record(folder, {"role": "assistant", "agent": agent, "text": str(event.get("text", "")),
                                  "model": str(event.get("model", "")), "spentUsd": float(event.get("spentUsd", 0) or 0),
                                  "inputTokens": int(event.get("inputTokens", 0) or 0),
                                  "outputTokens": int(event.get("outputTokens", 0) or 0),
                                  "timestamp": now_iso()})
            if agent == "claude" and self.messages and self.messages[-1].get("role") == "assistant":
                self._start_requested_build(self.messages[-1])
        elif kind == "chat-error":
            self._chat_answered = True
            self._end_stream()
            self._record(folder, {"role": "notice", "retry": True, "timestamp": now_iso(),
                                  "text": f"{AGENT_NAMES[agent]} couldn't answer: {str(event.get('problem', ''))[:400]}"})
        elif kind == "finished" and event.get("outcome") == "spending-limit":
            self._record(folder, {"role": "notice", "timestamp": now_iso(),
                                  "text": f"The reply stopped at the chat's ${self.chat_limit()} limit (Settings › Spending limits)."})
        elif kind == "finished" and event.get("outcome") == "error" and not self._chat_answered:
            self._chat_answered = True
            self._record(folder, {"role": "notice", "retry": True, "timestamp": now_iso(),
                                  "text": f"No reply: {str(event.get('problem') or event.get('summary', ''))[:400]}"})

    def _on_chat_exit(self, code):
        self.chat = None
        self.after_idle(self.refresh_usage)
        self._thinking = None
        self._end_stream()
        self.thinking_row.grid_remove()
        self._sync_button()
        if not self._chat_answered:
            self._record(self.chat_folder, {"role": "notice", "retry": True, "timestamp": now_iso(),
                                            "text": "Stopped before a reply came back." if code not in (0, None)
                                            else "No reply came back. Try again."})

    def _thinking_here(self):
        return self._thinking is not None and self.chat_folder and \
            os.path.normcase(self.chat_folder) == os.path.normcase(self.current_folder())

    def _show_thinking(self, agent):
        first = self._thinking is None
        self._thinking = (agent, time.time())
        self._place_thinking()
        if first:
            self._tick_thinking()

    # ------------------------------------------------------------ live reply
    def _stream_text(self, agent, text, attempt):
        """Claude's reply as it is written. A retry (new attempt) starts the text over."""
        live = self._stream
        if live is None or live["agent"] != agent or live["attempt"] != attempt:
            self._end_stream()
            live = self._stream = {"agent": agent, "attempt": attempt, "text": "", "widget": None, "label": None}
        live["text"] += text
        if self._stream_job is None:  # repaint at most every 80 ms, however fast the text comes
            self._stream_job = self.after(80, self._flush_stream)

    def _flush_stream(self):
        self._stream_job = None
        live = self._stream
        if live is None or not self.winfo_exists():
            return
        if live["widget"] is None:
            live["widget"], live["label"] = self._reply_frame(live["agent"])
            self.starter.grid_remove()
        live["label"].set_text(stream_preview(live["text"]))
        self._place_thinking(scroll=False)
        self._scroll_to_end(only_if_at_bottom=True)

    def _end_stream(self):
        if self._stream_job is not None:
            self.after_cancel(self._stream_job)
            self._stream_job = None
        if self._stream and self._stream["widget"] is not None:
            self._stream["widget"].destroy()
        self._stream = None

    def _place_thinking(self, scroll=True):
        """The ✻ line goes last: after the live reply while one is being written."""
        streaming = self._stream is not None and self._stream["widget"] is not None
        if streaming:
            self._stream["widget"].grid(row=self._items_row, column=0, sticky="ew", padx=self.pad["padx"], pady=(8, 4))
        if self._thinking_here():
            self._think_verb = "is writing" if streaming else "is thinking"
            self.thinking_row.grid(row=self._items_row + (1 if streaming else 0), column=0, sticky="w",
                                   padx=self.pad["padx"], pady=(10, 4))
            if scroll:
                self._scroll_to_end()
        else:
            self.thinking_row.grid_remove()

    def _tick_thinking(self):
        if self._thinking is None or not self.winfo_exists():
            return
        agent, started = self._thinking
        self._spin = (self._spin + 1) % len(theme.SPINNER_FRAMES)
        self._think_glyph.configure(text=theme.SPINNER_FRAMES[self._spin])
        self._think_text.configure(text=f"{AGENT_NAMES.get(agent, 'Claude')} {self._think_verb}…  ({format_elapsed(time.time() - started)})")
        self.after(150, self._tick_thinking)

    # --------------------------------------------------------------------- run
    def _on_go_pressed(self):
        """Older entry point (command palette): build what was discussed since the last Build."""
        self.build_discussed()

    def build_discussed(self):
        """Builds what was discussed since the last Build, without waiting for Claude to start it."""
        if self.running:
            return
        typed = self.task_box.get("1.0", "end").strip()
        pending = self.messages + ([{"role": "user", "text": typed}] if typed else [])
        task = compose_build_task(pending)
        if not task:
            messagebox.showinfo("Foundry", "First tell Foundry what you want: type it in the box at the bottom and press Send.")
            self.task_box.focus_set()
            return
        if typed:
            self.messages.append({"role": "user", "text": typed, "timestamp": now_iso()})
            self.task_box.delete("1.0", "end")
            self._update_placeholder()
        self._build(task)

    def _build(self, task):
        """Starts a Build of `task` (Claude's build request, or the conversation). True if it started."""
        if self.running:
            return False
        folder = self.current_folder()
        if not folder:
            messagebox.showinfo("Foundry", "Pick a project folder first.")
            return False
        if is_blocked(self.workspace_root, folder):
            messagebox.showinfo("Foundry", blocked_message(folder))
            return False
        if self.terminal.process_running or self.terminal.active_process is not None:
            messagebox.showinfo("Foundry", "Foundry is already working on another Build or an Improve round. One runs at a "
                                           "time: wait for it to finish (the spinning ✻ in the sidebar), or stop it first.")
            return False
        limit = self.read_limit()
        if limit is None:
            return False
        missing = self.missing_keys()
        if missing:
            self.result_row = self._items_row
            self.show_keys_needed(missing)
            return False
        headline = task.splitlines()[0]
        self._name_new_project(headline)
        folder = self.current_folder()
        self.remember({"UI_LAST_PROJECT": folder, "MAX_BUDGET_USD": f"{limit:.2f}"})
        start = now_iso()
        try:
            os.makedirs(folder, exist_ok=True)
            self.messages.append({"role": "build", "text": headline[:200], "timestamp": start})
            save_chat(folder, self.messages)
        except OSError as e:
            messagebox.showerror("Foundry", f"Couldn't save the conversation in {folder}:\n{e}")
            return False
        if self._is_new:
            self._set_titles(new=False)
        self._live = {"folder": folder, "start": start}
        self.result_card.grid_remove()
        self._render()
        if self.launch(["go", "--task", task, "--target", folder, "--budget-usd", f"{limit:.2f}"] + self.thinking_args(),
                       f"Foundry: {headline[:40]}", {"task": task, "folder": folder, "limit": limit, "module_count": 1, "mode": None}):
            self.feed.prompt(" ".join(headline.split())[:300])
            self._scroll_to_end()
            if self.on_project_used:  # it now shows up in the sidebar's project list
                self.on_project_used(folder)
            return True
        # nothing started (engine not built, or another run is going): undo the Build marker
        self.messages = [m for m in self.messages if not (m.get("role") == "build" and m.get("timestamp") == start)]
        save_chat(folder, self.messages)
        self._live = None
        self.refresh()
        return False

    def _part(self, event):
        count = event.get("moduleCount") or self.run_state.get("module_count", 1)
        index = event.get("moduleIndex")
        return f"Part {index}/{count} · " if count and count > 1 and index else ""

    def handle_event(self, event):
        kind = event.get("type")
        if kind == "start":
            self.run_state["mode"] = event.get("mode")
            remembered = event.get("historyEntries", 0)
            what = "new project" if event.get("mode") == "build" else "existing project"
            self.detail(f"{what} · {format_money(event.get('limitUsd', 0) or 0)} limit"
                        f"{f' · remembers {remembered} earlier run' + ('s' if remembered != 1 else '') if remembered else ''}")
        elif kind == "plan":
            modules = [m.replace("_", " ") for m in event.get("modules", [])]
            self.run_state["module_count"] = max(1, len(modules))
            if modules:
                self.detail(f"{len(modules)} part{'s' if len(modules) != 1 else ''}: {', '.join(modules)}")
        elif kind == "phase":
            phase = event.get("phase")
            prefix = self._part(event)
            if phase == "planning":
                self.step("Planning the work")
            elif phase == "designing":
                self.step(f"{prefix}Designing {event.get('module', '').replace('_', ' ')}")
            elif phase == "testing":
                self.step(f"{prefix}Writing tests the code must pass")
            elif phase == "coding":
                attempt = event.get("attempt", 1)
                self.step(f"{prefix}Writing the code" + ("" if attempt <= 1 else f" (attempt {attempt})"))
                if attempt > 1:
                    self.detail("learning from the last attempt's errors")
            elif phase == "checking":
                self.step(f"{prefix}Checking that it works")
        elif kind == "module-done":
            part = self._part(event).rstrip(" ·")
            lead = f"{part} " if part else ""
            if event.get("success"):
                self.feed.finish("done")
                self.detail(f"{lead}done: tests and mutation checks passed", "success")
            else:
                self.feed.finish("failed")
                self.detail(f"{lead}could not get it to pass", "error")

    def show_result(self, event):
        outcome = event.get("outcome")
        summary = event.get("summary", "")
        spent = format_money(float(event.get("spentUsd", 0) or 0))
        problem = (event.get("problem") or "").strip()
        files = event.get("filesChanged") or []
        file_text = ""
        if files:
            file_text = "Files: " + ", ".join(files[:8]) + (f" and {len(files) - 8} more" if len(files) > 8 else "") + ". "
        open_button = [("Open project folder", lambda: self.open_folder(self.run_state.get("folder", "")))]

        if outcome == "done":
            self.show_card(f"Done. {summary}", f"{file_text}Cost: {spent}. Tell Claude what to change next.",
                           open_button)
        elif outcome == "spending-limit":
            self.show_card("Stopped at your spending limit.",
                           f"{summary} Cost: {spent}. Raise the Build limit below and tell Claude to continue; finished parts are kept.", open_button)
        elif outcome == "not-finished":
            self.show_card("Foundry couldn't finish this time.",
                           f"{summary} {file_text}Cost: {spent}. Tell Claude to try again; Foundry remembers what it "
                           f"already tried.\n\nWhat went wrong: {problem[:400]}", open_button if files else ())
        elif problem.startswith("missing-keys"):
            self.show_keys_needed([problem.split(":", 1)[-1].strip()])
        elif problem.startswith("access-blocked"):
            self.show_card("AI access to this folder is blocked.",
                           "Allow it in the Files pane (right-click the folder) or in Settings › AI access, then try again.")
        else:
            self.show_card("Foundry stopped because of an error.", f"{summary} {problem[:400]}")

    def on_stopped(self):
        state = self.run_state
        try:
            append_journal_entry(state["folder"], {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "task": state["task"],
                "mode": state.get("mode") or "build",
                "outcome": "stopped",
                "summary": "Stopped by you before it finished.",
                "filesChanged": [],
                "modules": [],
                "spentUsd": state.get("spent", 0.0),
                "limitUsd": state.get("limit", 0.0),
                "commits": []
            })
        except Exception:
            pass
        self.show_card("Stopped.", f"Spent {format_money(state.get('spent', 0.0))}. Anything that was already finished is "
                                   "still in the folder. Tell Claude to continue when you want.",
                       [("Open project folder", lambda: self.open_folder(state.get("folder", "")))])
