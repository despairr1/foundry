"""The Context pane: what the models will see on the page you're on, like Claude Code's context view.

For a project or prompt chat: how much of the conversation window is used, what is in it (instructions, project
notes, the conversation, attached files), what the next Build gets, and the settings that apply. For Improve:
the evidence a round starts from."""
import json
import os

import customtkinter as ctk

import theme
from engine_run_view import format_money, load_journal, thinking_label
from project_chat import AGENTS, chat_spend, since_last_build
from skills import prompt_size

TRANSCRIPT_WINDOW = 60000   # src/swarm/chat.ts MAX_TRANSCRIPT_CHARS
BUILD_WINDOW = 12000        # project_chat.MAX_TASK_TRANSCRIPT
HISTORY_IN_PROMPT = 10      # src/engine/journal.ts PROMPT_ENTRIES
# Rough size of the fixed instructions each reply gets (src/swarm/chat.ts), by chat mode
INSTRUCTIONS_CHARS = {"project": 2300, "prompt": 1500}
CHARS_PER_TOKEN = 4


def transcript_size(messages):
    """(characters the engine would send, messages kept, messages left out), as formatTranscript trims."""
    lines = [f"{'User' if m.get('role') == 'user' else 'Model'}: {str(m.get('text', '')).strip()}" for m in messages
             if m.get("role") in ("user", "assistant")]
    total, kept = 0, 0
    for line in reversed(lines):
        total += len(line) + 2
        if total > TRANSCRIPT_WINDOW and kept > 0:
            total -= len(line) + 2
            break
        kept += 1
    return total, kept, len(lines) - kept


def history_size(entries):
    recent = entries[-HISTORY_IN_PROMPT:]
    return sum(len(str(e.get("task", ""))[:300]) + len(str(e.get("summary", ""))) + len(str(e.get("problem", ""))[:300]) + 60 for e in recent), len(recent)


class ContextPanel(ctk.CTkScrollableFrame):
    def __init__(self, master, config_manager):
        super().__init__(master, corner_radius=0, fg_color=theme.BG)
        self.config_manager = config_manager
        self.grid_columnconfigure(0, weight=1)
        self._small = ctk.CTkFont(size=12)
        self._tiny = ctk.CTkFont(size=11)
        self._rows = []

        self.usage_card, usage = self._card(0, "Conversation window")
        self.usage_headline = ctk.CTkLabel(usage, text="", anchor="w", font=self._small, text_color=theme.TEXT)
        self.usage_headline.grid(row=0, column=0, sticky="w")
        self.usage_amount = ctk.CTkLabel(usage, text="", anchor="e", font=self._tiny, text_color=theme.TEXT_FAINT)
        self.usage_amount.grid(row=0, column=1, sticky="e")
        self.usage_bar = ctk.CTkProgressBar(usage, height=6)
        self.usage_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 2))
        self.usage_note = ctk.CTkLabel(usage, text="", anchor="w", justify="left", font=self._tiny, text_color=theme.TEXT_FAINT, wraplength=240)
        self.usage_note.grid(row=2, column=0, columnspan=2, sticky="ew")

        self.parts_card, self.parts = self._card(1, "What the models see")
        self.build_card, self.build = self._card(2, "What the next Build gets")
        self.settings_card, self.settings = self._card(3, "Settings that apply")
        self.bind("<Configure>", self._on_resize, add="+")

    def _card(self, row, title):
        card = ctk.CTkFrame(self, corner_radius=10, fg_color=theme.BG, border_width=1, border_color=theme.BORDER)
        card.grid(row=row, column=0, sticky="ew", padx=(8, 10), pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        label = ctk.CTkLabel(card, text=title, anchor="w", font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.TEXT)
        label.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        card.title_label = label
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        return card, body

    def _on_resize(self, event):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        wrap = max(160, int(event.width / scaling) - 70)
        self.usage_note.configure(wraplength=wrap)
        for label in self._rows:
            if label.winfo_exists():
                label.configure(wraplength=wrap - 90)

    @staticmethod
    def _clear(frame):
        for child in frame.winfo_children():
            child.destroy()

    def _fill(self, frame, rows):
        """rows: (label, value) or (label, value, tone)."""
        self._clear(frame)
        frame.grid_columnconfigure(1, weight=1)
        for idx, row in enumerate(rows):
            key, value = row[0], row[1]
            tone = row[2] if len(row) > 2 else None
            color = {"warn": theme.WARNING, "ok": theme.SUCCESS, "error": theme.ERROR}.get(tone, theme.TEXT_MUTED)
            ctk.CTkLabel(frame, text=key, anchor="w", font=self._tiny, text_color=theme.TEXT_MUTED).grid(row=idx, column=0, sticky="nw", pady=(2, 0))
            label = ctk.CTkLabel(frame, text=value, anchor="e", justify="right", font=self._tiny, text_color=color, wraplength=170)
            label.grid(row=idx, column=1, sticky="e", pady=(2, 0))
            self._rows.append(label)
        self._rows = [l for l in self._rows if l.winfo_exists()]

    # ----------------------------------------------------------------- update
    def show_chat(self, view, mode="project"):
        """A project page (mode project) or a prompt conversation (mode prompt)."""
        messages = list(getattr(view, "messages", []) or [])
        folder = view.current_folder()
        chars, kept, dropped = transcript_size(messages)
        instructions = INSTRUCTIONS_CHARS.get(mode, 2000)
        entries = load_journal(folder) if mode == "project" and folder else []
        notes_chars, notes_entries = history_size(entries) if mode == "project" else (0, 0)
        if mode == "project":
            notes_chars += 80  # the "new / existing project" line
        ratio = min(1.0, chars / TRANSCRIPT_WINDOW)
        self.usage_card.title_label.configure(text="Conversation window")
        self.usage_headline.configure(text=f"{round(ratio * 100)}% of the window used" if messages else "Nothing sent yet")
        self.usage_amount.configure(text=f"{chars:,} / {TRANSCRIPT_WINDOW:,} chars" if messages else "")
        self.usage_bar.set(ratio)
        self.usage_bar.configure(progress_color=theme.ACCENT if ratio < 0.75 else theme.WARNING if ratio < 1 else theme.ERROR)
        total_tokens = (chars + instructions + notes_chars + prompt_size(self.config_manager.workspace_root)[1]) // CHARS_PER_TOKEN
        self.usage_note.configure(text=(f"About {total_tokens:,} tokens go to the model with each reply: the conversation, the fixed "
                                        f"instructions and the project notes. The oldest messages drop out once the window is full."
                                        if messages else "Each reply sends the whole conversation, so the models remember everything said here."))

        attached = sum(1 for m in messages if m.get("role") == "user" and not m.get("tool") and "Here is " in str(m.get("text", ""))[:60])
        commands = sum(1 for m in messages if m.get("tool"))
        rows = [("Instructions", f"≈ {instructions:,} chars · {('project chat: roles, Run and Build' if mode == 'project' else 'prompt studio: roles')}")]
        skill_count, skill_chars = prompt_size(self.config_manager.workspace_root)
        rows.append(("Skills", f"{skill_count} enabled · {skill_chars:,} chars" if skill_count else "none enabled"))
        if mode == "project":
            rows.append(("Project notes", f"{notes_chars:,} chars · {'new or existing, plus ' if True else ''}{notes_entries} of {len(entries)} earlier runs"
                         if entries else "new or existing project; no runs yet"))
        conversation = f"{kept} messages · {chars:,} chars" if messages else "none yet"
        rows.append(("Conversation", conversation + (f" · {dropped} oldest left out" if dropped else ""), "warn" if dropped else None))
        if attached:
            rows.append(("Attached files", f"{attached} in your messages"))
        if commands:
            rows.append(("Command output", f"{commands} result{'s' if commands != 1 else ''} (chat only, not Builds)"))
        self._fill(self.parts, rows)

        if mode == "project":
            self.build_card.grid()
            pending = [m for m in since_last_build(messages) if not m.get("tool")]
            size = sum(len(str(m.get("text", ""))) + 12 for m in pending)
            build_rows = [("Since the last Build", f"{len(pending)} messages · {size:,} chars" if pending else "nothing yet"),
                          ("Cap", f"{BUILD_WINDOW:,} chars; the newest are kept", "warn" if size > BUILD_WINDOW else None)]
            self._fill(self.build, build_rows)
        else:
            self.build_card.grid_remove()

        agent = next((label for name, label in AGENTS if name == getattr(view, "agent", "claude")), "Claude")
        get = self.config_manager.get
        caching = str(get("FOUNDRY_PROMPT_CACHING", "true") or "true").strip().lower() in ("true", "1", "yes")
        settings = [("Who answers", agent), ("Thinking", thinking_label(view.thinking_level())),
                    ("Prompt caching", "on · the instructions are a cached prefix" if caching else "off (Settings › Builds)"),
                    ("Chat reply limit", format_money(float(get("FOUNDRY_CHAT_BUDGET_USD", "1") or 1))),
                    ("Chat cost so far", format_money(chat_spend(messages)))]
        if mode == "project":
            settings.append(("Build limit", view.limit_text() if hasattr(view, "limit_text") else ""))
            settings.append(("Models", f"{get('ANTHROPIC_MODEL', '')} · {get('GEMINI_MODEL', '')} · {get('ASTRA_MODEL', '') or get('OPENAI_MODEL', '')}"))
        self._fill(self.settings, settings)

    def show_improve(self, view):
        """The Improve page: the evidence a round starts from."""
        root = view.workspace_root
        entries = load_journal(root)
        health = {}
        try:
            with open(os.path.join(root, ".foundry", "health.json"), "r", encoding="utf-8") as f:
                health = json.load(f)
        except Exception:
            pass
        problems = health.get("problems") or []
        notes_chars, notes_entries = history_size(entries)
        self.usage_card.title_label.configure(text="Evidence for the next round")
        self.usage_headline.configure(text="Scouting first" if not view.focus_entry.get().strip() else "Focus given")
        self.usage_amount.configure(text="")
        self.usage_bar.set(0)
        self.usage_note.configure(text="With no focus, Claude proposes a few small improvements from this evidence and Gemini picks one. "
                                       "With a focus, the round works on that.")
        self._fill(self.parts, [
            ("Type-check", "not checked yet" if not health else "passing" if health.get("typecheck", {}).get("ok") else "failing",
             None if not health else "ok" if health.get("typecheck", {}).get("ok") else "error"),
            ("Test suite", "not checked yet" if not health else "passing" if health.get("tests", {}).get("ok") else "failing",
             None if not health else "ok" if health.get("tests", {}).get("ok") else "error"),
            ("Log problems", f"{len(problems)} recent warnings/errors", "warn" if problems else None),
            ("History", f"{notes_entries} of {len(entries)} rounds · {notes_chars:,} chars"),
            ("Source map", "every src/ file with its size"),
        ])
        self.build_card.grid_remove()
        self._fill(self.settings, [("Thinking", thinking_label(view.thinking_level())), ("Limit", f"${view.limit_text()}"),
                                   ("Rounds", view.rounds_picker.get())])
