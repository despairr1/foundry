"""Arena: the same question to Claude, Gemini and OpenAI, their real replies and what each cost.

Runs the engine's own chat command (`node dist/index.js chat --agent all --progress`) on a throwaway conversation
file, so the replies come from the same models, keys, thinking level and per-reply spending limit as a project chat.
With --agent all the engine asks the models one after another and each later model sees the earlier replies; the page
says so. Without keys the page refuses (nothing is spent); in offline mode the engine answers with stand-ins, and
the page labels them as such."""
import json
import os
import shutil
import tempfile
import time

import customtkinter as ctk

import theme
from engine_run_view import format_elapsed, format_money, thinking_label, thinking_level_for
from pane_kit import CHIP, PAD, PRIMARY, append_text, mono_box, set_text
from project_chat import AGENT_NAMES, AGENT_ROLES, ChatProcess, missing_chat_keys

AGENT_ORDER = ("claude", "gemini", "openai")
WIDE_LAYOUT_PX = 900   # three columns side by side from this pane width; stacked below it


class PlaygroundView(ctk.CTkFrame):
    """The Arena page (right pane › Arena)."""

    def __init__(self, master, config_manager, workspace_root, **kwargs):
        super().__init__(master, corner_radius=0, fg_color="transparent", **kwargs)
        self.config_manager = config_manager
        self.workspace_root = workspace_root
        self.chat = None
        self.temp_dir = None
        self.cards = {}
        self._wide = None
        self._spin = 0
        self._thinking = None     # (agent, started_at, verb)
        self._finished = None     # the engine's finished event for the current run
        self._run_spent = 0.0
        self._replies = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_header()
        self._build_prompt()
        self._build_status()
        self._build_replies()
        self._build_footer()
        self.bind("<Configure>", self._on_resize, add="+")
        self.refresh()

    # ---------------------------------------------------------------- layout
    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(2, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Arena", font=ctk.CTkFont(size=18, weight="bold"), text_color=theme.TEXT, anchor="w").grid(
            row=0, column=0, sticky="w")
        self.subtitle = ctk.CTkLabel(
            head, text="One question to all three models, their real replies, and what each reply cost. The engine asks them in "
                       "order: Gemini sees Claude's reply and OpenAI sees both, as in a project chat set to “All three”.",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, anchor="w", justify="left")
        self.subtitle.grid(row=1, column=0, sticky="w", pady=(2, 0))

    def _build_prompt(self):
        box = ctk.CTkFrame(self, fg_color="transparent")
        box.grid(row=1, column=0, sticky="ew", padx=PAD)
        box.grid_columnconfigure(0, weight=1)
        self.prompt = ctk.CTkTextbox(box, height=84, font=ctk.CTkFont(size=13), wrap="word", fg_color=theme.SURFACE,
                                     border_width=1, border_color=theme.BORDER, corner_radius=8)
        self.prompt.grid(row=0, column=0, sticky="ew")
        self.prompt.bind("<Control-Return>", lambda e: (self._on_ask(), "break")[1])
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        row.grid_columnconfigure(1, weight=1)
        self.ask_button = ctk.CTkButton(row, text="Ask all three", width=130, height=34, font=ctk.CTkFont(size=13, weight="bold"),
                                        command=self._on_ask, **PRIMARY)
        self.ask_button.grid(row=0, column=0, sticky="w")
        self.settings_label = ctk.CTkLabel(row, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w",
                                           justify="left")
        self.settings_label.grid(row=0, column=1, sticky="w", padx=(12, 0))

    def _build_status(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(10, 6))
        self.status_glyph = ctk.CTkLabel(row, text="", width=18, font=ctk.CTkFont(size=14), text_color=theme.ACCENT)
        self.status_glyph.pack(side="left")
        self.status = ctk.CTkLabel(row, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, anchor="w", justify="left")
        self.status.pack(side="left", padx=(4, 0), fill="x", expand=True)

    def _build_replies(self):
        self.replies = ctk.CTkFrame(self, fg_color="transparent")
        self.replies.grid(row=3, column=0, sticky="nsew", padx=PAD)
        for agent in AGENT_ORDER:
            card = ctk.CTkFrame(self.replies, corner_radius=10, fg_color="transparent", border_width=1, border_color=theme.BORDER)
            card.grid_columnconfigure(0, weight=1)
            card.grid_rowconfigure(2, weight=1)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
            head.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(head, text=f"{AGENT_NAMES[agent]} · {AGENT_ROLES[agent]}", font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=theme.TEXT, anchor="w").grid(row=0, column=0, sticky="w")
            meta = ctk.CTkLabel(head, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="e")
            meta.grid(row=0, column=1, sticky="e")
            model = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w")
            model.grid(row=1, column=0, sticky="w", padx=12)
            box = mono_box(card, height=160)
            box.grid(row=2, column=0, sticky="nsew", padx=12, pady=(6, 12))
            self.cards[agent] = {"frame": card, "meta": meta, "model": model, "box": box, "attempt": 0, "text": ""}
        self._place_cards(wide=False)

    def _build_footer(self):
        self.footer = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w", justify="left")
        self.footer.grid(row=4, column=0, sticky="w", padx=PAD, pady=(4, 8))

    def _place_cards(self, wide):
        if wide == self._wide:
            return
        self._wide = wide
        for index in range(3):
            self.replies.grid_columnconfigure(index, weight=1 if wide or index == 0 else 0)
            self.replies.grid_rowconfigure(index, weight=1 if not wide or index == 0 else 0)
        for index, agent in enumerate(AGENT_ORDER):
            card = self.cards[agent]["frame"]
            card.grid_forget()
            if wide:
                card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 4, 0 if index == 2 else 4))
            else:
                card.grid(row=index, column=0, sticky="nsew", pady=(0, 8))

    def _on_resize(self, event):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = int(event.width / scaling)
        self.subtitle.configure(wraplength=max(200, width - 2 * PAD - 8))
        self.footer.configure(wraplength=max(200, width - 2 * PAD - 8))
        self.status.configure(wraplength=max(200, width - 2 * PAD - 40))
        self.settings_label.configure(wraplength=max(160, width - 2 * PAD - 160))
        self._place_cards(wide=width >= WIDE_LAYOUT_PX)

    # ----------------------------------------------------------------- state
    def offline(self):
        return str(self.config_manager.get("FOUNDRY_OFFLINE_MODE", "false")).strip().lower() == "true" or \
            os.environ.get("FOUNDRY_OFFLINE_MODE", "").strip().lower() == "true"

    def chat_limit(self):
        try:
            value = float(str(self.config_manager.get("FOUNDRY_CHAT_BUDGET_USD", "1") or "1").strip().lstrip("$"))
            return f"{value:.2f}" if value > 0 else "1.00"
        except ValueError:
            return "1.00"

    def refresh(self):
        """Re-reads the settings the next run would use (called when the pane opens)."""
        level = thinking_level_for(self.config_manager)
        text = f"Thinking: {thinking_label(level)}  ·  limit {format_money(float(self.chat_limit()))} per run (Settings › Spending limits)"
        if self.offline():
            text += "  ·  offline mode: stand-in replies, nothing is spent"
        self.settings_label.configure(text=text)
        if self.chat is None and not self._finished:
            missing = missing_chat_keys(self.config_manager, "all")
            if missing:
                self._set_status(f"Add a key for {', '.join(missing)} in Settings first. Nothing runs without it.", theme.WARNING)
            elif not os.path.exists(os.path.join(self.workspace_root, "dist", "index.js")):
                self._set_status("The engine isn't built yet: run npm install and npm run build in the Foundry folder.", theme.WARNING)
            else:
                self._set_status("Nothing asked yet. Type a question and press Ask all three (Ctrl+Enter).", theme.TEXT_FAINT)

    def _set_status(self, text, color=None, glyph=""):
        self.status.configure(text=text, text_color=color or theme.TEXT_MUTED)
        self.status_glyph.configure(text=glyph)

    # ------------------------------------------------------------------- run
    def _on_ask(self):
        if self.chat is not None:
            self.chat.stop()
            self._set_status("Stopping…", theme.TEXT_MUTED)
            return
        question = self.prompt.get("1.0", "end").strip()
        if not question:
            self.prompt.focus_set()
            return
        missing = missing_chat_keys(self.config_manager, "all")
        if missing:
            self._set_status(f"Add a key for {', '.join(missing)} in Settings first. Nothing was sent.", theme.WARNING)
            return
        entry = os.path.join(self.workspace_root, "dist", "index.js")
        if not os.path.exists(entry):
            self._set_status("The engine isn't built yet: run npm install and npm run build in the Foundry folder.", theme.WARNING)
            return
        try:
            self.temp_dir = tempfile.mkdtemp(prefix="foundry-arena-")
            conversation = os.path.join(self.temp_dir, "conversation.json")
            with open(conversation, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "messages": [{"role": "user", "text": question}]}, f, ensure_ascii=False)
        except OSError as e:
            self._set_status(f"Couldn't write the conversation file: {e}", theme.ERROR)
            return
        node = str(self.config_manager.get("NODE_BINARY_PATH", "node") or "node").strip() or "node"
        cmd = [node, entry, "chat", "--conversation", conversation, "--agent", "all", "--budget-usd", self.chat_limit(),
               "--progress", "--thinking", thinking_level_for(self.config_manager)]
        for agent in AGENT_ORDER:
            card = self.cards[agent]
            card.update(attempt=0, text="")
            set_text(card["box"], "")
            card["meta"].configure(text="waiting", text_color=theme.TEXT_FAINT)
            card["model"].configure(text="")
        self._finished = None
        self._run_spent = 0.0
        self._replies = 0
        self.footer.configure(text="")
        self.chat = ChatProcess(self, cmd, self.workspace_root, dict(os.environ, FOUNDRY_DASHBOARD="false"),
                                self._on_event, self._on_exit)
        self.chat.start()
        self.ask_button.configure(text="Stop", fg_color=theme.SURFACE_HOVER, hover_color=theme.SELECTED, text_color=theme.ERROR)
        self._show_thinking("claude", "is thinking")

    def _on_event(self, event):
        kind = event.get("type")
        agent = event.get("agent") if event.get("agent") in self.cards else "claude"
        card = self.cards[agent]
        if kind == "chat-thinking":
            card["meta"].configure(text="thinking…", text_color=theme.ACCENT)
            self._show_thinking(agent, "is thinking")
        elif kind == "chat-delta":
            attempt = int(event.get("attempt", 1) or 1)
            if attempt != card["attempt"]:
                card["attempt"], card["text"] = attempt, ""
                set_text(card["box"], "")
            piece = str(event.get("text", ""))
            card["text"] += piece
            append_text(card["box"], piece)
            card["meta"].configure(text="writing…" if attempt == 1 else f"writing… (attempt {attempt})", text_color=theme.ACCENT)
            self._show_thinking(agent, "is writing")
        elif kind == "chat-reply":
            text = str(event.get("text", ""))
            spent = float(event.get("spentUsd", 0) or 0)
            model = str(event.get("model", ""))
            card["text"] = text
            set_text(card["box"], text)
            self._run_spent += spent
            self._replies += 1
            card["meta"].configure(text=f"{format_money(spent)} · {len(text):,} chars", text_color=theme.TEXT_MUTED)
            card["model"].configure(text="Offline stand-in, not a model reply" if model == "offline" else model,
                                    text_color=theme.WARNING if model == "offline" else theme.TEXT_FAINT)
        elif kind == "chat-error":
            problem = str(event.get("problem", ""))[:600]
            set_text(card["box"], f"Couldn't answer: {problem}")
            card["meta"].configure(text="no reply", text_color=theme.ERROR)
        elif kind == "finished":
            self._finished = event

    def _on_exit(self, code):
        self.chat = None
        self._thinking = None
        self.ask_button.configure(text="Ask all three", **PRIMARY)
        if self.temp_dir:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None
        for card in self.cards.values():
            if card["meta"].cget("text") in ("waiting", "thinking…") or card["meta"].cget("text").startswith("writing"):
                card["meta"].configure(text="no reply", text_color=theme.TEXT_FAINT)
        event = self._finished or {}
        outcome = event.get("outcome")
        spent = float(event.get("spentUsd", self._run_spent) or 0)
        if outcome == "done":
            self._set_status(f"Done: {self._replies} repl{'y' if self._replies == 1 else 'ies'}, {format_money(spent)} in total.",
                             theme.SUCCESS, "✓")
        elif outcome == "spending-limit":
            self._set_status(f"Stopped at the {format_money(float(self.chat_limit()))} limit for one run after {format_money(spent)} "
                             f"(Settings › Spending limits).", theme.WARNING, "$")
        elif outcome == "error":
            problem = str(event.get("problem") or event.get("summary") or "")[:400]
            self._set_status(f"No reply. {problem}".strip(), theme.ERROR, "✕")
        elif code is None:
            self._set_status("The engine could not be started.", theme.ERROR, "✕")
        elif code != 0:
            self._set_status(f"Stopped before the models finished (exit code {code}).", theme.TEXT_MUTED, "■")
        else:
            self._set_status("The engine ended without a result.", theme.TEXT_MUTED, "■")
        self.footer.configure(text=f"This run: {format_money(spent)}. Each run is logged in .foundry.log (Terminal › Log) like a project chat.")

    # ------------------------------------------------------------ live state
    def _show_thinking(self, agent, verb):
        first = self._thinking is None
        self._thinking = (agent, time.time() if first else self._thinking[1], verb)
        if first:
            self._tick()

    def _tick(self):
        if self._thinking is None or not self.winfo_exists():
            return
        agent, started, verb = self._thinking
        self._spin = (self._spin + 1) % len(theme.SPINNER_FRAMES)
        self._set_status(f"{AGENT_NAMES.get(agent, 'Claude')} {verb}…  ({format_elapsed(time.time() - started)})", theme.ACCENT,
                         theme.SPINNER_FRAMES[self._spin])
        self.after(150, self._tick)
