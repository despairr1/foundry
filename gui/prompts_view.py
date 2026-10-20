"""+ New prompt: the prompt studio. A chat page like a project's, but the product is a prompt or a job plan, not a Build.

Two modes (the For: menu under the message box): "Foundry job" (the default) turns a big job into an ordered list of
Build requests Foundry runs well, shown as a Job plan card with Start this job; "Any model" writes a prompt for Claude,
GPT, Gemini, an image model or an agent. Each conversation is saved under <foundry>/.foundry/prompts/ and listed in the
Prompts pane on the right; the conversation is the prompt's memory when you reopen it."""
import os
from datetime import datetime

import customtkinter as ctk

import theme
from engine_run_view import CHIP, PRIMARY
from project_chat import chat_path, load_chat
from projects_view import ProjectsView
from prompt_store import (DEFAULT_MODE, STUDIO_MODES, delete_prompt, latest_block, list_prompts, load_meta, new_prompt_folder,
                          parse_job_plan, set_title, title_for, touch)
from shell import Tooltip, icon_button, popup_menu

TITLES = {
    "job": ("Plan a job for Foundry",
            "Describe the whole job. Claude knows how Foundry builds and splits it into steps that each fit one Build, with the "
            "Thinking level and spending limit for each; then run them one after another with Start this job. Gemini reviews "
            "the plan, OpenAI offers a different split. Everything said here is remembered as the plan's memory."),
    "prompt": ("Write a prompt",
               "Tell Claude what the prompt is for: which model or tool reads it, what it must produce, and for whom. Claude "
               "writes it, Gemini can critique it, and you refine it together. Everything said here is remembered as the "
               "prompt's memory."),
}
PLACEHOLDERS = {
    "job": ("Describe the whole job: what to make, for whom, in what language", "Ask for changes to the plan, or add what you forgot"),
    "prompt": ("Describe the prompt you need: what it's for, which model, what it should produce", "Ask for changes, or paste a prompt to improve"),
}
EXAMPLES = {
    "job": [
        ("Web app", "Plan: a small Flask web app with user sign-in, a to-do list per user, and a dashboard page. Python, SQLite, no front-end framework."),
        ("Port a script", "Plan: port my Python CSV cleaning script to Rust with the same behavior and command-line options, with tests."),
        ("Big refactor", "Plan: split a 2,000-line main.py into modules with tests, without changing what the program does."),
    ],
    "prompt": [
        ("Summarize notes", "A prompt for Claude that turns messy meeting notes into a short summary with the decisions and action items."),
        ("Code reviewer", "A system prompt for a coding assistant that reviews pull requests for bugs and security problems, in a friendly tone."),
        ("Image prompt", "A prompt for an image model: a logo for a weather app, flat and minimal, two colors, no text."),
    ],
}


class PromptsView(ProjectsView):
    """A prompt conversation: the project chat page in prompt or job mode (no folder, no Build of its own, no commands)."""

    examples = EXAMPLES["job"]
    chat_mode = "prompt"

    def __init__(self, master, workspace_root, config_manager, terminal, folder=None, on_prompt_used=None, on_use_prompt=None,
                 on_start_job=None, on_send_step=None, **kwargs):
        folder = folder or new_prompt_folder(workspace_root)
        saved = load_meta(folder).get("mode")
        self.studio_mode = saved if saved in [m for m, _, _ in STUDIO_MODES] else DEFAULT_MODE
        self.examples = EXAMPLES[self.studio_mode]
        super().__init__(master, workspace_root, config_manager, terminal, folder=folder, new=not load_chat(folder),
                         on_project_used=on_prompt_used, on_use_prompt=on_use_prompt, on_start_job=on_start_job,
                         on_send_step=on_send_step, **kwargs)

    # ---------------------------------------------------------------- looks
    def _build_composer(self):
        super()._build_composer()
        self.folder_button.grid_remove()
        self.limit_button.grid_remove()
        self.mode_button = self._flat_button(self.under, "", self._mode_menu)
        self.mode_button.grid(row=0, column=2, sticky="w", padx=(4, 0))
        Tooltip(self.mode_button, "What the studio makes: a job plan for Foundry, or a prompt for any model")
        self.go_tip.text = "Send (Enter). Claude writes the plan or prompt; ask for changes until it is right."
        self._show_mode()

    def _show_mode(self):
        label = next((text for mode, text, _ in STUDIO_MODES if mode == self.studio_mode), STUDIO_MODES[0][1])
        self.mode_button.configure(text=f"For: {label}  ⌄")

    def _mode_menu(self):
        self._menu_below(self.mode_button, [(f"{'✓' if mode == self.studio_mode else '   '}  {label}  ·  {hint}",
                                             lambda m=mode: self.set_mode(m)) for mode, label, hint in STUDIO_MODES])

    def set_mode(self, mode):
        if mode not in [m for m, _, _ in STUDIO_MODES]:
            return
        self.studio_mode = mode
        self.examples = EXAMPLES[mode]
        self._show_mode()
        self._set_titles(self._is_new)
        self._render_starter()
        self._update_placeholder()
        if os.path.isdir(self.current_folder()):
            touch(self.current_folder(), mode=mode)

    def _render_starter(self):
        for child in list(self.starter.winfo_children())[1:]:
            child.destroy()
        for name, text in self.examples:
            ctk.CTkButton(self.starter, text=name, height=26, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda t=text: self.set_task(t)).pack(side="left", padx=3)

    def _set_titles(self, new):
        self._is_new = new
        title, subtitle = TITLES[self.studio_mode]
        if new:
            self.title_label.configure(text=title)
            self.subtitle_label.configure(text=subtitle)
            self.title_label.grid()
            self.subtitle_label.grid()
        else:
            self.title_label.grid_remove()
            self.subtitle_label.grid_remove()

    def _update_placeholder(self):
        self._fit_composer()
        if self.task_box.get("1.0", "end").strip():
            self.placeholder.place_forget()
            return
        first, later = PLACEHOLDERS[self.studio_mode]
        self.placeholder.configure(text=f"{first if not self.messages else later}. Enter to send, Shift+Enter for a new line")
        self.placeholder.place(x=17, y=13)

    def refresh(self):
        super().refresh()
        self.folder_hint = ""

    def title(self):
        return load_meta(self.current_folder()).get("title") or ("New prompt" if self._is_new else "Untitled prompt")

    # ----------------------------------------------------------- behaviour
    def has_history(self, folder):
        return bool(load_chat(folder))

    def can_change_folder(self):
        return False

    def _chat_command(self, agent, folder):
        return [self.node_path(), self.engine_entry(), "chat", "--conversation", chat_path(folder), "--agent", agent,
                "--mode", self.studio_mode, "--budget-usd", self.chat_limit(), "--progress"] + self.thinking_args()

    def _start_requested_build(self, message, force=False):
        return  # the studio never builds by itself; Start this job runs the plan in a project

    def build_discussed(self):
        return

    def _name_new_project(self, text):
        """The first message names the conversation, like a Claude session's title."""
        if not self.messages:
            touch(self.current_folder(), title=title_for(text), mode=self.studio_mode)

    def _record(self, folder, message):
        super()._record(folder, message)
        try:
            touch(folder)
        except OSError:
            pass

    def working_prompt(self):
        return latest_block(self.messages, ("prompt",))[1]

    def working_block(self):
        """(kind, body): the newest prompt or job plan a model wrote in this conversation."""
        return latest_block(self.messages, ("prompt", "foundry"))


class PromptListPanel(ctk.CTkScrollableFrame):
    """The right pane's Prompts page: the working prompt or plan of the open conversation, and every saved conversation."""

    def __init__(self, master, workspace_root, on_open, on_new, on_rename, on_delete, on_use_prompt, on_start_job=None):
        super().__init__(master, corner_radius=0, fg_color=theme.BG)
        self.workspace_root = workspace_root
        self.on_open, self.on_new, self.on_rename, self.on_delete = on_open, on_new, on_rename, on_delete
        self.on_use_prompt, self.on_start_job = on_use_prompt, on_start_job
        self.grid_columnconfigure(0, weight=1)
        self._small = ctk.CTkFont(size=12)
        self._tiny = ctk.CTkFont(size=11)
        self._selected = None

        self.working_card = ctk.CTkFrame(self, corner_radius=10, fg_color=theme.BG, border_width=1, border_color=theme.BORDER)
        self.working_card.grid(row=0, column=0, sticky="ew", padx=(8, 10), pady=(0, 10))
        self.working_card.grid_columnconfigure(0, weight=1)
        self.working_title = ctk.CTkLabel(self.working_card, text="Working prompt", anchor="w", font=ctk.CTkFont(size=12, weight="bold"),
                                          text_color=theme.TEXT)
        self.working_title.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        self.working_text = ctk.CTkLabel(self.working_card, text="", anchor="w", justify="left", font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11),
                                         text_color=theme.TEXT_MUTED, wraplength=240)
        self.working_text.grid(row=1, column=0, sticky="ew", padx=14)
        self.working_buttons = ctk.CTkFrame(self.working_card, fg_color="transparent", width=1, height=1)
        self.working_buttons.grid(row=2, column=0, sticky="w", padx=12, pady=(8, 12))
        self._working = ""
        self._kind = ""
        self._signature = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=1, column=0, sticky="ew", padx=(14, 10), pady=(4, 2))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Saved prompts", font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.TEXT, anchor="w").grid(row=0, column=0, sticky="w")
        icon_button(header, "+", "New prompt", self.on_new, size=24).grid(row=0, column=1)
        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.list_frame.grid(row=2, column=0, sticky="ew", padx=(8, 10))
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.bind("<Configure>", self._on_resize, add="+")
        self.set_working("", "")

    def _on_resize(self, event):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        self.working_text.configure(wraplength=max(160, int(event.width / scaling) - 70))

    def _copy(self, button):
        self.clipboard_clear()
        self.clipboard_append(self._working)
        button.configure(text="Copied")
        self.after(1500, lambda: button.configure(text="Copy") if button.winfo_exists() else None)

    def set_working(self, kind, text):
        """kind: "prompt", "foundry" (a job plan) or ""."""
        self._working, self._kind = text or "", kind
        for child in self.working_buttons.winfo_children():
            child.destroy()
        if kind == "foundry" and self._working:
            plan = parse_job_plan(self._working)
            self.working_title.configure(text="Working job plan")
            if plan:
                lines = [f"{plan['job']}"] + [f"{i + 1}. {s['title']}" for i, s in enumerate(plan["steps"])]
                self.working_text.configure(text="\n".join(lines)[:600], text_color=theme.TEXT)
                if self.on_start_job:
                    ctk.CTkButton(self.working_buttons, text="▶  Start this job", width=120, height=26, corner_radius=13, font=self._tiny,
                                  **PRIMARY, command=lambda p=plan: self.on_start_job(p)).pack(side="left")
            else:
                self.working_text.configure(text="The plan couldn't be read; ask Claude to rewrite it.", text_color=theme.WARNING)
            copy = ctk.CTkButton(self.working_buttons, text="Copy", width=64, height=26, corner_radius=13, font=self._tiny, **CHIP)
            copy.configure(command=lambda b=copy: self._copy(b))
            copy.pack(side="left", padx=(6, 0))
        elif self._working:
            self.working_title.configure(text="Working prompt")
            shown = self._working if len(self._working) <= 600 else self._working[:600].rstrip() + "…"
            self.working_text.configure(text=shown, text_color=theme.TEXT)
            copy = ctk.CTkButton(self.working_buttons, text="Copy", width=64, height=26, corner_radius=13, font=self._tiny, **PRIMARY)
            copy.configure(command=lambda b=copy: self._copy(b))
            copy.pack(side="left")
            ctk.CTkButton(self.working_buttons, text="Use in a new project", width=120, height=26, corner_radius=13, font=self._tiny, **CHIP,
                          command=lambda: self.on_use_prompt(self._working)).pack(side="left", padx=(6, 0))
        else:
            self.working_title.configure(text="Working prompt")
            self.working_text.configure(text="Claude's latest prompt or job plan shows here, ready to copy or start.", text_color=theme.TEXT_FAINT)

    def refresh(self, selected_folder=None):
        self._selected = os.path.normcase(os.path.abspath(selected_folder)) if selected_folder else None
        items = list_prompts(self.workspace_root)
        signature = (self._selected, tuple((i["folder"], i["title"], i["updatedAt"], i["messages"], i.get("mode")) for i in items))
        if signature == self._signature:
            return  # nothing changed: leave the rows alone (a click handler may be running on one)
        self._signature = signature
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not items:
            ctk.CTkLabel(self.list_frame, text="No saved prompts yet. Each conversation you start\nwith + New prompt shows up here.",
                         justify="left", anchor="w", font=self._tiny, text_color=theme.TEXT_FAINT).grid(row=0, column=0, sticky="w", padx=8, pady=6)
            return
        for idx, item in enumerate(items):
            selected = self._selected == os.path.normcase(os.path.abspath(item["folder"]))
            row = ctk.CTkFrame(self.list_frame, corner_radius=8, fg_color=theme.SELECTED if selected else "transparent")
            row.grid(row=idx, column=0, sticky="ew", pady=1)
            row.grid_columnconfigure(0, weight=1)
            try:
                when = datetime.fromisoformat(item["updatedAt"].replace("Z", "+00:00")).astimezone().strftime("%b %d, %H:%M")
            except ValueError:
                when = ""
            title = ctk.CTkLabel(row, text=item["title"], anchor="w", font=self._small, text_color=theme.TEXT if selected else theme.TEXT_MUTED)
            title.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 0))
            count = item["messages"]
            mode = "job" if item.get("mode") == "job" else "prompt"
            sub = ctk.CTkLabel(row, text=f"{mode}  ·  {count} message{'s' if count != 1 else ''}  ·  {when}", anchor="w", font=self._tiny,
                               text_color=theme.TEXT_FAINT)
            sub.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
            for widget in (row, title, sub):
                widget.bind("<Button-1>", lambda e, f=item["folder"]: self.on_open(f), add="+")
                widget.bind("<Button-3>", lambda e, it=item: self._menu(it), add="+")
                widget.bind("<Enter>", lambda e, r=row, s=selected: r.configure(fg_color=theme.SELECTED if s else theme.SURFACE_HOVER), add="+")
                widget.bind("<Leave>", lambda e, r=row, s=selected: r.configure(fg_color=theme.SELECTED if s else "transparent"), add="+")

    def _menu(self, item):
        popup_menu(self, [("Open", lambda: self.on_open(item["folder"])),
                          ("Rename…", lambda: self.on_rename(item["folder"], item["title"])),
                          None,
                          ("Delete", lambda: self.on_delete(item["folder"], item["title"]))])


def rename_prompt(folder, title):
    return set_title(folder, title)


def remove_prompt(folder):
    delete_prompt(folder)
