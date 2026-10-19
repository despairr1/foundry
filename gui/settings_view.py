"""Settings, laid out like the Claude desktop app: a list of pages on the left, the page on the right, and every
change saved to .env the moment it is made (no Save button to forget). Only settings the engine or the GUI
actually reads are shown."""
import json
import os
import subprocess
import sys
import threading
import urllib.request
from tkinter import filedialog, messagebox

import customtkinter as ctk

import theme
from access_rules import load_rules, set_rule
from engine_run_view import CHIP, PRIMARY, THINKING_LEVELS, THINKING_PREF, DEFAULT_THINKING
from providers import (OPENAI_ENDPOINT, PRESETS, SYNTHESIZER_PREF, add_provider, current_synthesizer, load_providers,
                       remove_provider, synthesizer_settings, test_provider, update_provider)
from shell import Tooltip
import skills as skill_store

PAGES = [
    ("general", "General"),
    ("models", "AI models"),
    ("keys", "API keys & providers"),
    ("spending", "Spending limits"),
    ("builds", "Builds"),
    ("improve", "Improve"),
    ("access", "AI access"),
    ("skills", "Skills"),
    ("terminal", "Terminal"),
    ("shortcuts", "Keyboard shortcuts"),
    ("advanced", "Advanced"),
    ("about", "About"),
]

CLAUDE_MODELS = ["claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5", "claude-sonnet-5"]
GEMINI_MODELS = ["gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.5-flash"]
OPENAI_MODELS = ["gpt-5.5", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "o4-mini", "o3"]

SHORTCUTS = [
    ("Ctrl N", "New project"),
    ("Ctrl K  /  Ctrl P", "Search commands (the command palette)"),
    ("Ctrl B", "Hide or show the sidebar"),
    ("F1", "Help"),
    ("Enter", "Send the message (in a project or prompt chat)"),
    ("Shift Enter", "New line in the message box"),
    ("Ctrl S", "Save the file (Editor)"),
    ("Ctrl C", "Terminal: copy the selection, or interrupt the program when nothing is selected"),
    ("Ctrl V", "Terminal: paste"),
    ("Middle-click a tab", "Terminal: close that tab"),
    ("Right-click", "Files: allow or block AI access to a folder · a message: copy it · a project in the sidebar: its menu"),
]


def _bool(value):
    return str(value or "").strip().lower() in ("true", "1", "yes", "on")


class SettingsView(ctk.CTkFrame):
    def __init__(self, master, config_manager, workspace_root=None, on_theme_change=None, on_run_check=None,
                 on_restart=None, on_rules_changed=None, on_open_help=None, on_open_file=None, **kwargs):
        super().__init__(master, corner_radius=0, fg_color=theme.BG, **kwargs)
        self.config_manager = config_manager
        self.root = workspace_root or config_manager.workspace_root
        self.on_theme_change = on_theme_change
        self.on_run_check = on_run_check
        self.on_restart = on_restart
        self.on_rules_changed = on_rules_changed
        self.on_open_help = on_open_help
        self.on_open_file = on_open_file
        self.current = None
        self._flash_job = None
        self._model_boxes = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(22, 8))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Settings", font=ctk.CTkFont(size=22, weight="bold"), text_color=theme.TEXT, anchor="w").grid(
            row=0, column=0, sticky="w")
        self.flash_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=12), text_color=theme.SUCCESS)
        self.flash_label.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(header, text="Changes are saved as you make them, to Foundry's .env file.", font=ctk.CTkFont(size=12),
                     text_color=theme.TEXT_FAINT, anchor="w").grid(row=1, column=0, sticky="w")

        self.nav = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color="transparent")
        self.nav.grid(row=1, column=0, sticky="nsw", padx=(20, 0), pady=(4, 16))
        self.nav.grid_propagate(False)
        self.nav_buttons = {}
        for idx, (pid, label) in enumerate(PAGES):
            btn = ctk.CTkButton(self.nav, text=label, anchor="w", height=32, corner_radius=8, font=ctk.CTkFont(size=13),
                                fg_color="transparent", hover_color=theme.SURFACE_HOVER, text_color=theme.TEXT_MUTED,
                                command=lambda p=pid: self.show_page(p))
            btn.grid(row=idx, column=0, sticky="ew", padx=(0, 8), pady=1)
            self.nav_buttons[pid] = btn
        self.nav.grid_columnconfigure(0, weight=1)

        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=(0, 12))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.page = None
        self.show_page("general")

    # ================================================================ pages
    def show_page(self, pid):
        """Pages are rebuilt each time they are shown, so they always reflect what is saved."""
        for other, btn in self.nav_buttons.items():
            active = other == pid
            btn.configure(fg_color=theme.SELECTED if active else "transparent", text_color=theme.TEXT if active else theme.TEXT_MUTED)
        if self.page is not None:
            self.page.destroy()
        self.page = ctk.CTkScrollableFrame(self.content, corner_radius=0, fg_color="transparent")
        self.page.grid(row=0, column=0, sticky="nsew")
        self.page.grid_columnconfigure(0, weight=1)
        self._next = 0
        self.current = pid
        getattr(self, f"_page_{pid}")()

    def refresh(self):
        if self.current:
            self.show_page(self.current)

    def open_page(self, pid):
        if pid in self.nav_buttons:
            self.show_page(pid)

    # ------------------------------------------------------------- row kit
    def _grid(self, widget, **kw):
        widget.grid(row=self._next, column=0, sticky="ew", **kw)
        self._next += 1
        return widget

    def _title(self, text, hint=None):
        self._grid(ctk.CTkLabel(self.page, text=text, font=ctk.CTkFont(size=17, weight="bold"), text_color=theme.TEXT, anchor="w"),
                   padx=12, pady=(10, 2))
        if hint:
            self._grid(ctk.CTkLabel(self.page, text=hint, font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, anchor="w",
                                    justify="left", wraplength=640), padx=12, pady=(0, 6))

    def _section(self, text):
        self._grid(ctk.CTkLabel(self.page, text=text, font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.TEXT, anchor="w"),
                   padx=12, pady=(18, 4))

    def _row(self, title, hint=None, stacked=False):
        """A settings row: title and hint on the left, the control in the returned frame on the right (or below)."""
        row = ctk.CTkFrame(self.page, corner_radius=8, fg_color=theme.SURFACE)
        self._grid(row, padx=8, pady=2)
        row.grid_columnconfigure(0, weight=1)
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.grid(row=0, column=0, sticky="ew", padx=14, pady=(9, 9 if not hint else 2))
        ctk.CTkLabel(text, text=title, font=ctk.CTkFont(size=13), text_color=theme.TEXT, anchor="w").pack(anchor="w")
        if hint:
            ctk.CTkLabel(text, text=hint, font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w", justify="left",
                         wraplength=440 if not stacked else 620).pack(anchor="w", pady=(0, 7))
        control = ctk.CTkFrame(row, fg_color="transparent")
        if stacked:
            control.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
            control.grid_columnconfigure(0, weight=1)
        else:
            control.grid(row=0, column=1, sticky="e", padx=(8, 14), pady=6)
        return control

    def _switch(self, key, title, hint=None, default="false", on_change=None):
        control = self._row(title, hint)
        switch = ctk.CTkSwitch(control, text="", width=46)
        if _bool(self.config_manager.get(key, default)):
            switch.select()

        def changed():
            value = "true" if switch.get() else "false"
            self._save({key: value})
            if on_change:
                on_change(value)
        switch.configure(command=changed)
        switch.pack()
        return switch

    def _choice(self, key, title, options, hint=None, default="", on_change=None, width=190):
        """options: [(value, label)]."""
        control = self._row(title, hint)
        labels = {value: label for value, label in options}
        current = str(self.config_manager.get(key, default) or default)
        menu = ctk.CTkOptionMenu(control, values=[label for _, label in options], width=width, height=30, font=ctk.CTkFont(size=12))
        menu.set(labels.get(current, labels.get(default, options[0][1])))

        def changed(label):
            value = next((v for v, l in options if l == label), default)
            self._save({key: value})
            if on_change:
                on_change(value)
        menu.configure(command=changed)
        menu.pack()
        return menu

    def _entry(self, key, title, hint=None, default="", width=120, kind="text", placeholder="", on_change=None, browse=None):
        """kind: text, int, float, money, path. Saved when the field loses focus or Enter is pressed."""
        control = self._row(title, hint)
        entry = ctk.CTkEntry(control, width=width, height=30, font=ctk.CTkFont(size=12), placeholder_text=placeholder)
        current = str(self.config_manager.get(key, default) or "")
        entry.insert(0, current)
        state = {"saved": current}

        def commit(_event=None):
            raw = entry.get().strip()
            if raw == state["saved"]:
                return
            value = self._validate(kind, raw, default)
            if value is None:
                self._flash("That value isn't valid; the setting was left as it was.", ok=False)
                entry.delete(0, "end")
                entry.insert(0, state["saved"])
                return
            state["saved"] = value
            if value != raw:
                entry.delete(0, "end")
                entry.insert(0, value)
            self._save({key: value})
            if on_change:
                on_change(value)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Return>", lambda e: (commit(), self.focus_set()))
        entry.pack(side="left")
        if browse:
            ctk.CTkButton(control, text="Browse…", width=80, height=30, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda: self._browse(entry, browse, commit)).pack(side="left", padx=(6, 0))
        return entry

    def _browse(self, entry, what, commit):
        if what == "folder":
            chosen = filedialog.askdirectory(initialdir=entry.get() or os.path.expanduser("~"))
        else:
            chosen = filedialog.askopenfilename(initialdir=os.path.dirname(entry.get()) or os.path.expanduser("~"))
        if chosen:
            entry.delete(0, "end")
            entry.insert(0, os.path.normpath(chosen))
            commit()

    @staticmethod
    def _validate(kind, raw, default):
        if kind == "text" or kind == "path":
            return raw
        if raw == "" and kind in ("optional-money", "optional-int"):
            return ""
        try:
            if kind == "int" or kind == "optional-int":
                value = int(raw)
                return str(value) if value > 0 else None
            if kind in ("float", "money", "optional-money"):
                value = float(raw.lstrip("$"))
                if value <= 0:
                    return None
                return f"{value:.2f}" if "money" in kind else f"{value:g}"
            if kind == "ratio":
                value = float(raw)
                return f"{value:g}" if 0 <= value <= 1 else None
        except ValueError:
            return None
        return raw

    def _secret(self, key, title, hint, placeholder, provider, models_target=None):
        """A masked key field with Show, and Save & Test, which also fetches the provider's model list."""
        control = self._row(title, hint, stacked=True)
        line = ctk.CTkFrame(control, fg_color="transparent")
        line.grid(row=0, column=0, sticky="ew")
        line.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(line, show="•", height=30, font=ctk.CTkFont(size=12), placeholder_text=placeholder)
        entry.insert(0, str(self.config_manager.get(key, "") or ""))
        entry.grid(row=0, column=0, sticky="ew")
        shown = {"on": False}

        def toggle():
            shown["on"] = not shown["on"]
            entry.configure(show="" if shown["on"] else "•")
            show_btn.configure(text="Hide" if shown["on"] else "Show")
        show_btn = ctk.CTkButton(line, text="Show", width=56, height=30, font=ctk.CTkFont(size=12), **CHIP, command=toggle)
        show_btn.grid(row=0, column=1, padx=(6, 0))
        status = ctk.CTkLabel(control, text=self._key_status(key), font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w")
        status.grid(row=1, column=0, sticky="w", pady=(4, 0))
        test_btn = ctk.CTkButton(line, text="Save & Test", width=96, height=30, font=ctk.CTkFont(size=12), **PRIMARY,
                                 command=lambda: self._save_and_test(key, entry, provider, test_btn, status, models_target))
        test_btn.grid(row=0, column=2, padx=(6, 0))
        entry.bind("<Return>", lambda e: test_btn.invoke())
        return entry

    def _key_status(self, key):
        return "A key is saved." if str(self.config_manager.get(key, "") or "").strip() else "No key yet."

    def _save_and_test(self, key, entry, provider, button, status, models_target):
        value = entry.get().strip()
        self._save({key: value}, note="Key saved")
        if not value:
            status.configure(text="No key yet.", text_color=theme.TEXT_FAINT)
            return
        button.configure(text="Testing…", state="disabled")
        status.configure(text="Connecting…", text_color=theme.TEXT_MUTED)

        def work():
            ok, msg = self.config_manager.test_connection(provider, custom_key=value)
            models = self._list_models(provider, value) if ok else []

            def done():
                if not self.winfo_exists():
                    return
                button.configure(text="Save & Test", state="normal")
                status.configure(text=("✓ " if ok else "✕ ") + msg, text_color=theme.SUCCESS if ok else theme.ERROR)
                if ok and models and models_target in self._model_boxes:
                    self._model_boxes[models_target].configure(values=models)
            self.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _list_models(provider, key):
        try:
            if provider == "anthropic":
                req = urllib.request.Request("https://api.anthropic.com/v1/models",
                                             headers={"x-api-key": key, "anthropic-version": "2023-06-01", "User-Agent": "Foundry"})
            elif provider == "openai":
                req = urllib.request.Request("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}", "User-Agent": "Foundry"})
            elif provider == "gemini":
                req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models",
                                             headers={"x-goog-api-key": key, "User-Agent": "Foundry"})
            else:
                return []
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []
        if provider == "gemini":
            return sorted(m.get("name", "").split("/", 1)[-1] for m in data.get("models", []) if "generateContent" in (m.get("supportedGenerationMethods") or []))
        return sorted(m.get("id") for m in data.get("data", []) if m.get("id"))

    def _action(self, title, hint, button, command, primary=False):
        control = self._row(title, hint)
        ctk.CTkButton(control, text=button, height=30, font=ctk.CTkFont(size=12), command=command, **(PRIMARY if primary else CHIP)).pack()

    def _info(self, title, value, hint=None):
        control = self._row(title, hint)
        ctk.CTkLabel(control, text=value, font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, anchor="e", justify="right",
                     wraplength=360).pack()

    def _note(self, text):
        self._grid(ctk.CTkLabel(self.page, text=text, font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w",
                                justify="left", wraplength=640), padx=14, pady=(4, 0))

    # ------------------------------------------------------------- saving
    def _save(self, updates, note="Saved"):
        ok, msg = self.config_manager.save(updates)
        self._flash(note if ok else f"Couldn't save: {msg}", ok)
        return ok

    def _flash(self, text, ok=True):
        self.flash_label.configure(text=("✓ " if ok else "") + text, text_color=theme.SUCCESS if ok else theme.ERROR)
        if self._flash_job:
            self.after_cancel(self._flash_job)
        self._flash_job = self.after(2500 if ok else 6000, lambda: self.flash_label.configure(text=""))

    # ============================================================== General
    def _page_general(self):
        self._title("General", "How Foundry looks and behaves.")
        self._section("Appearance")
        self._choice("UI_THEME_MODE", "Theme", [("Dark", "Dark"), ("Light", "Light"), ("System", "Match Windows")],
                     "Dark is the Claude Code look.", default="Dark", on_change=self._apply_theme)
        self._section("Starting up")
        self._choice("UI_START_PAGE", "Open on start", [("improve", "Improve"), ("last", "The last project"), ("new", "A new project")],
                     "Which page Foundry shows when it opens.", default="improve")
        self._entry("UI_PROJECTS_HOME", "New projects go in", "A new project gets its own folder here, named after your first message.",
                    default=os.path.join(os.path.expanduser("~"), "FoundryProjects"), width=300, kind="path", browse="folder")
        self._section("While it works")
        self._switch("SOUND_NOTIFICATIONS", "Sound when a run finishes", "A short chime when a Build or Improve round ends.", default="true")
        self._switch("UI_CONFIRM_STOP", "Ask before stopping a run", "Stop asks for confirmation, since the part in progress is thrown away.", default="true")
        self._switch("UI_OPEN_OVERVIEW", "Open the Overview pane on start", "The right-hand pane with spending, models and the last run.", default="true")

    def _apply_theme(self, choice):
        ctk.set_appearance_mode(choice)
        if self.on_theme_change:
            self.on_theme_change(choice)

    # ============================================================ AI models
    def _page_models(self):
        self._title("AI models", "Who does what. Claude designs (the architect), Gemini checks (the auditor), and the code writer "
                                 "(the synthesizer) writes the code: OpenAI, or any provider you add under API keys & providers.")
        self._section("Thinking")
        self._choice(THINKING_PREF, "How hard the models think", [(v, f"{label} · {hint}") for v, label, hint in THINKING_LEVELS],
                     "The same setting as under a chat's message box and on Improve. Higher is more careful, slower and costs more.",
                     default=DEFAULT_THINKING, width=260)
        self._section("Architect · Claude")
        self._model_row("ANTHROPIC_MODEL", "Model", CLAUDE_MODELS, "claude-fable-5-1", "anthropic")
        self._entry("ANTHROPIC_MAX_TOKENS", "Longest reply (tokens)", "Replies cut off at this length fail the request. Claude also writes code "
                    "when OpenAI is unavailable, so 65536 is the safe default; 16000 truncated module code.", default="65536", kind="int")
        self._switch("ANTHROPIC_REFUSAL_FALLBACKS", "Server-side fallback on a refusal",
                     "When Claude declines, the API may retry with its default routing. Only on api.anthropic.com.", default="true")
        self._section("Auditor · Gemini")
        self._model_row("GEMINI_MODEL", "Model", GEMINI_MODELS, "gemini-3.1-pro-preview", "gemini")
        self._entry("GEMINI_MAX_OUTPUT_TOKENS", "Longest reply (tokens)", "Leave empty for the model's full window (65,536 on thinking models).",
                    default="", kind="optional-int", placeholder="automatic")
        self._entry("GEMINI_EMBEDDING_MODEL", "Memory embeddings", "The Gemini model that indexes Foundry's memory of past fixes.",
                    default="gemini-embedding-2", width=200)
        self._section("Code writer · the synthesizer")
        choices = [("openai", "OpenAI")] + [(p["id"], p["name"]) for p in load_providers(self.root)]
        self._choice(SYNTHESIZER_PREF, "Who writes the code", choices,
                     "OpenAI with your OpenAI key, or a provider you added. Without any key here, Claude writes the code too.",
                     default="openai", on_change=self._set_synthesizer, width=220)
        choice, provider = current_synthesizer(self.root, self.config_manager)
        if provider:
            self._model_row(None, f"Model at {provider['name']}", [provider.get("model", "")], provider.get("model", ""), provider["id"],
                            provider=provider)
        else:
            self._model_row("OPENAI_MODEL", "Model", OPENAI_MODELS, "gpt-5", "openai")
        self._entry("ASTRA_MAX_TOKENS", "Longest reply (tokens)", "Leave empty for the model's full window.", default="", kind="optional-int",
                    placeholder="automatic")

    def _model_row(self, key, title, models, default, box_id, provider=None):
        control = self._row(title, "Pick one, or type any model name the provider accepts.")
        current = provider.get("model", "") if provider else str(self.config_manager.get(key, default) or default)
        box = ctk.CTkComboBox(control, values=[m for m in models if m] or [current], width=260, height=30, font=ctk.CTkFont(size=12))
        box.set(current)
        self._model_boxes[box_id] = box

        def commit(_event=None):
            value = box.get().strip()
            if not value or value == (provider.get("model", "") if provider else self.config_manager.get(key, "")):
                return
            if provider:
                update_provider(self.root, provider["id"], model=value)
                provider["model"] = value
                self._save(synthesizer_settings(self.root, self.config_manager, provider["id"]))
            else:
                updates = {key: value}
                if key == "OPENAI_MODEL" and current_synthesizer(self.root, self.config_manager)[0] == "openai":
                    updates["ASTRA_MODEL"] = ""  # the engine falls back to OPENAI_MODEL
                self._save(updates)
        box.configure(command=lambda _choice: commit())
        box.bind("<FocusOut>", commit)
        box.bind("<Return>", commit)
        box.pack()
        return box

    def _set_synthesizer(self, choice):
        updates = synthesizer_settings(self.root, self.config_manager, choice)
        if updates is None:
            self._flash("That provider no longer exists.", ok=False)
            return
        self._save(updates, note="Code writer changed")
        self.refresh()

    # ===================================================== API keys & providers
    def _page_keys(self):
        self._title("API keys & providers", "Keys are stored in Foundry's .env file on this computer and sent only to their provider. "
                                            "Save & Test checks the key and fetches the provider's model list for the AI models page.")
        self._section("The three built-in models")
        self._secret("ANTHROPIC_API_KEY", "Anthropic (Claude, the architect)", "console.anthropic.com › API keys", "sk-ant-…",
                     "anthropic", models_target="anthropic")
        self._secret("GEMINI_API_KEY", "Google (Gemini, the auditor)", "aistudio.google.com › Get API key", "AIza…", "gemini",
                     models_target="gemini")
        self._secret("OPENAI_API_KEY", "OpenAI (the code writer)", "platform.openai.com › API keys. Optional: without it Claude writes the code.",
                     "sk-…", "openai", models_target="openai")
        self._section("Other providers")
        self._note("Any OpenAI-compatible service or local server can be the code writer: DeepSeek, OpenRouter, Groq, Mistral, xAI, "
                   "Together, Fireworks, Ollama, LM Studio, or your own. Pick one as the code writer on the AI models page.")
        providers = load_providers(self.root)
        chosen, _ = current_synthesizer(self.root, self.config_manager)
        for provider in providers:
            self._provider_card(provider, chosen == provider["id"])
        control = self._row("Add a provider", "Name it, give its endpoint and model, paste its key, and test it.")
        ctk.CTkButton(control, text="+ Add a provider", height=30, font=ctk.CTkFont(size=12), **PRIMARY, command=self._add_provider_dialog).pack()

    def _provider_card(self, provider, is_writer):
        control = self._row(f"{provider['name']}{'  ·  writes the code' if is_writer else ''}",
                            f"{provider['baseUrl']}  ·  model {provider.get('model') or '(none yet)'}"
                            + (f"  ·  ${provider['priceIn']:g} in / ${provider['priceOut']:g} out per million tokens" if "priceIn" in provider else ""),
                            stacked=True)
        line = ctk.CTkFrame(control, fg_color="transparent")
        line.grid(row=0, column=0, sticky="ew")
        line.grid_columnconfigure(0, weight=1)
        key_var = provider["keyVar"]
        entry = ctk.CTkEntry(line, show="•", height=30, font=ctk.CTkFont(size=12), placeholder_text="API key (local servers need none)")
        entry.insert(0, str(self.config_manager.get(key_var, "") or ""))
        entry.grid(row=0, column=0, sticky="ew")
        status = ctk.CTkLabel(control, text=self._key_status(key_var), font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w")
        status.grid(row=1, column=0, sticky="w", pady=(4, 0))

        def save_test():
            value = entry.get().strip()
            self._save({key_var: value}, note="Key saved")
            if is_writer:  # the engine reads the key through ASTRA_API_KEY
                self._save(synthesizer_settings(self.root, self.config_manager, provider["id"]))
            test_btn.configure(text="Testing…", state="disabled")
            status.configure(text="Connecting…", text_color=theme.TEXT_MUTED)

            def work():
                ok, msg, models = test_provider(provider["baseUrl"], value)

                def done():
                    if not self.winfo_exists():
                        return
                    test_btn.configure(text="Save & Test", state="normal")
                    status.configure(text=("✓ " if ok else "✕ ") + msg, text_color=theme.SUCCESS if ok else theme.ERROR)
                    if ok and models and provider["id"] in self._model_boxes:
                        self._model_boxes[provider["id"]].configure(values=models)
                self.after(0, done)
            threading.Thread(target=work, daemon=True).start()
        test_btn = ctk.CTkButton(line, text="Save & Test", width=96, height=30, font=ctk.CTkFont(size=12), **PRIMARY, command=save_test)
        test_btn.grid(row=0, column=1, padx=(6, 0))
        if not is_writer:
            ctk.CTkButton(line, text="Use as code writer", width=130, height=30, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda: self._set_synthesizer(provider["id"])).grid(row=0, column=2, padx=(6, 0))
        ctk.CTkButton(line, text="Remove", width=70, height=30, font=ctk.CTkFont(size=12), **CHIP,
                      command=lambda: self._remove_provider(provider, is_writer)).grid(row=0, column=3, padx=(6, 0))

    def _remove_provider(self, provider, is_writer):
        if not messagebox.askyesno("Remove provider?", f"Remove {provider['name']}? Its key is removed from .env too."):
            return
        remove_provider(self.root, provider["id"])
        updates = {provider["keyVar"]: ""}
        if is_writer:
            updates.update(synthesizer_settings(self.root, self.config_manager, "openai"))
        self.config_manager.config.pop(provider["keyVar"], None)
        self._save(updates, note=f"{provider['name']} removed")
        self.refresh()

    def _add_provider_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add a provider")
        dialog.geometry("520x470")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.grid_columnconfigure(1, weight=1)
        fields = {}

        def label(row, text):
            ctk.CTkLabel(dialog, text=text, font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(20, 10), pady=6)

        label(0, "Service")
        preset_menu = ctk.CTkOptionMenu(dialog, values=[p[1] for p in PRESETS], width=280, height=30, font=ctk.CTkFont(size=12))
        preset_menu.grid(row=0, column=1, sticky="w", padx=(0, 20), pady=(16, 6))
        for row, (name, key, placeholder) in enumerate([("Name", "name", "e.g. DeepSeek"), ("Endpoint (base URL)", "url", "https://…/v1"),
                                                         ("Model", "model", "the model name"), ("API key", "key", "paste the key")], start=1):
            label(row, name)
            entry = ctk.CTkEntry(dialog, height=30, font=ctk.CTkFont(size=12), placeholder_text=placeholder, show="•" if key == "key" else "")
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 20), pady=6)
            fields[key] = entry
        label(5, "Price per million tokens")
        prices = ctk.CTkFrame(dialog, fg_color="transparent")
        prices.grid(row=5, column=1, sticky="w", padx=(0, 20), pady=6)
        fields["in"] = ctk.CTkEntry(prices, width=80, height=30, font=ctk.CTkFont(size=12), placeholder_text="$ in")
        fields["in"].pack(side="left")
        fields["out"] = ctk.CTkEntry(prices, width=80, height=30, font=ctk.CTkFont(size=12), placeholder_text="$ out")
        fields["out"].pack(side="left", padx=(6, 0))
        ctk.CTkLabel(prices, text="optional; used for the spending limit", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT).pack(side="left", padx=(8, 0))
        status = ctk.CTkLabel(dialog, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w", wraplength=460, justify="left")
        status.grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=(4, 0))

        def fill(choice):
            preset = next(p for p in PRESETS if p[1] == choice)
            for key, value in (("name", preset[1] if preset[0] != "custom" else ""), ("url", preset[2]), ("model", preset[3][0] if preset[3] else "")):
                fields[key].delete(0, "end")
                fields[key].insert(0, value)
            for key, value in (("in", preset[5][0] if preset[5] else ""), ("out", preset[5][1] if preset[5] else "")):
                fields[key].delete(0, "end")
                if value != "":
                    fields[key].insert(0, f"{value:g}")
            fields["key"].configure(placeholder_text=preset[4] or "paste the key")
        preset_menu.configure(command=fill)
        preset_menu.set(PRESETS[0][1])
        fill(PRESETS[0][1])

        def values():
            return {k: fields[k].get().strip() for k in ("name", "url", "model", "key", "in", "out")}

        def test():
            v = values()
            if not v["url"]:
                status.configure(text="Enter the endpoint first.", text_color=theme.ERROR)
                return
            status.configure(text="Connecting…", text_color=theme.TEXT_MUTED)

            def work():
                ok, msg, models = test_provider(v["url"], v["key"])
                extra = f" Models include: {', '.join(models[:6])}" if models else ""
                dialog.after(0, lambda: status.configure(text=("✓ " if ok else "✕ ") + msg + extra, text_color=theme.SUCCESS if ok else theme.ERROR)
                             if dialog.winfo_exists() else None)
            threading.Thread(target=work, daemon=True).start()

        def add():
            v = values()
            if not v["name"] or not v["url"]:
                status.configure(text="A name and an endpoint are needed.", text_color=theme.ERROR)
                return
            preset = next(p for p in PRESETS if p[1] == preset_menu.get())
            try:
                price_in = float(v["in"]) if v["in"] else None
                price_out = float(v["out"]) if v["out"] else None
            except ValueError:
                status.configure(text="Prices must be numbers, like 0.27.", text_color=theme.ERROR)
                return
            provider = add_provider(self.root, v["name"], v["url"], v["model"], preset=preset[0], price_in=price_in, price_out=price_out)
            self._save({provider["keyVar"]: v["key"]}, note=f"{provider['name']} added")
            dialog.destroy()
            self.refresh()
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", padx=20, pady=(14, 16))
        ctk.CTkButton(buttons, text="Test", width=80, height=30, font=ctk.CTkFont(size=12), **CHIP, command=test).pack(side="left", padx=(0, 6))
        ctk.CTkButton(buttons, text="Cancel", width=80, height=30, font=ctk.CTkFont(size=12), **CHIP, command=dialog.destroy).pack(side="left", padx=(0, 6))
        ctk.CTkButton(buttons, text="Add", width=90, height=30, font=ctk.CTkFont(size=12), **PRIMARY, command=add).pack(side="left")

    # ============================================================= Spending
    def _page_spending(self):
        self._title("Spending limits", "Foundry stops before it spends more than these on AI usage. Each page shows its own limit too; "
                                       "these are the defaults.")
        self._entry("MAX_BUDGET_USD", "Build limit", "The default spending limit for a Build (under a project's message box).", default="5.00", kind="money")
        self._entry("UI_SELF_IMPROVE_LIMIT", "Improve limit", "The default limit for an Improve session (all its rounds together).", default="5.00", kind="money")
        self._entry("FOUNDRY_CHAT_BUDGET_USD", "Chat reply limit", "The most one reply in a project or prompt chat may cost.", default="1.00", kind="money")
        self._entry("MAX_MODULE_BUDGET_USD", "Per-part cap", "Optional: the most one part of a Build may use, so one hard part can't eat the whole limit.",
                    default="", kind="optional-money", placeholder="none")
        self._note("Spend so far, per run and per model, is under Engine internals › Spend (◎ at the top right).")

    # ================================================================ Builds
    def _page_builds(self):
        self._title("Builds", "How Foundry writes, tests and keeps code.")
        self._section("Trying and testing")
        self._entry("MAX_CONVERGENCE_ITERATIONS", "Attempts per part", "How many times the code writer may retry a part before giving up on it.",
                    default="8", kind="int")
        self._entry("MIN_MUTATION_KILL_RATIO", "Test strength (0 to 1)", "A part passes only when its tests catch at least this share of "
                    "deliberately broken copies of the code. 0.85 is strict; lower is faster.", default="0.85", kind="ratio")
        self._switch("AUTO_GIT_COMMIT", "Commit each finished part to git", "Every finished part becomes a git commit in the project (a repository "
                     "is created if there is none).", default="true")
        self._section("Where tests run")
        self._switch("FOUNDRY_FORCE_LOCAL_SANDBOX", "Always run tests on this computer", "Off: Foundry uses the Docker sandbox below when Docker is "
                     "running, and this computer otherwise.", default="false")
        self._entry("FOUNDRY_SANDBOX_IMAGE", "Docker image", "", default="foundry-sandbox:latest", width=220)
        self._entry("DOCKER_MEMORY_MB", "Container memory (MB)", "", default="1024", kind="int")
        self._entry("DOCKER_CPUS", "Container CPUs", "", default="2.0", kind="float")
        self._entry("FOUNDRY_SANDBOX_TIMEOUT_MS", "Test timeout (ms)", "A test run longer than this fails.", default="120000", kind="int")
        self._entry("FOUNDRY_LSP_TIMEOUT_MS", "Code analysis timeout (ms)", "How long to wait for the language server's diagnostics on a change.",
                    default="1500", kind="int")
        self._section("Talking to the models")
        self._entry("REQUEST_TIMEOUT_MS", "Request timeout (ms)", "Thinking models can take minutes on a hard part.", default="600000", kind="int")
        self._entry("REQUEST_MAX_RETRIES", "Retries", "How many times a failed request is retried (0 to 10).", default="3", kind="int")

    # =============================================================== Improve
    def _page_improve(self):
        self._title("Improve", "How Foundry improves its own engine.")
        self._choice("UI_SELF_IMPROVE_ROUNDS", "Rounds per session", [("1", "1"), ("3", "3"), ("5", "5")],
                     "Improvements to try in a row. It stops early after two rounds that don't work out.", default="1", width=90)
        self._note("With no focus (or \"improve yourself\"), a round first scouts: Claude proposes a few small, checkable improvements from the "
                   "evidence, Gemini picks the best, and that becomes the round's focus. The Diagnostics card on the Improve page shows the evidence.")
        self._action("Check Foundry's health now", "Runs the type-check, the test suite and the log scan, with no AI calls, and shows the result on "
                     "the Improve page.", "Check now", lambda: self.on_run_check and self.on_run_check())
        self._section("Ouroboros (self-healing while it runs)")
        self._switch("OUROBOROS_AUTO_HEAL", "Watch for slowdowns and patch them", "While a run goes on, Foundry watches its own event loop, parse "
                     "times and memory, and may patch itself when a limit is passed.", default="true")
        self._entry("OUROBOROS_MAX_LAG_MS", "Event-loop lag limit (ms)", "", default="180", kind="int")
        self._entry("OUROBOROS_MAX_AST_MS", "Parse-time limit (ms)", "", default="350", kind="int")
        self._entry("OUROBOROS_MAX_HEAP_MB", "Memory limit (MB)", "", default="1024", kind="int")
        self._switch("OUROBOROS_AUTO_RESTART", "Rebuild after patching itself", "Recompile Foundry when a patch to its own code converges.",
                     default="false")

    # ============================================================= AI access
    def _page_access(self):
        self._title("AI access", "Which folders Foundry's models may read, change or run things in. Everything is allowed unless you block "
                                 "it; the most specific rule wins, so a blocked folder can hold an allowed one. The Files pane (✎, right-click "
                                 "a folder) sets these too.")
        rules = load_rules(self.root)
        if not rules:
            self._note("No rules yet: every folder is allowed.")
        for rule in rules:
            control = self._row(rule["path"], "Blocked: Foundry's models never read, change or run anything here." if rule["access"] == "block"
                                else "Allowed (an exception inside a blocked folder, or a reminder).")
            ctk.CTkLabel(control, text="⊘ Blocked" if rule["access"] == "block" else "✓ Allowed", font=ctk.CTkFont(size=12),
                         text_color=theme.ERROR if rule["access"] == "block" else theme.SUCCESS).pack(side="left", padx=(0, 10))
            ctk.CTkButton(control, text="Remove", width=70, height=28, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda p=rule["path"]: self._rule(p, None)).pack(side="left")
        control = self._row("Add a rule", "Choose a folder, then block or allow it.")
        ctk.CTkButton(control, text="Block a folder…", height=30, font=ctk.CTkFont(size=12), **PRIMARY,
                      command=lambda: self._pick_folder("block")).pack(side="left", padx=(0, 6))
        ctk.CTkButton(control, text="Allow a folder…", height=30, font=ctk.CTkFont(size=12), **CHIP,
                      command=lambda: self._pick_folder("allow")).pack(side="left")

    def _pick_folder(self, access):
        folder = filedialog.askdirectory(title=f"{'Block' if access == 'block' else 'Allow'} AI access to which folder?")
        if folder:
            self._rule(os.path.normpath(folder), access)

    def _rule(self, path, access):
        if access == "block" and os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(self.root)):
            messagebox.showinfo("Foundry", "Foundry's own folder can't be blocked: Improve needs it.")
            return
        rules = set_rule(self.root, path, access)
        self._flash("Access rules saved")
        if self.on_rules_changed:
            self.on_rules_changed(rules)
        self.refresh()

    # ================================================================ Skills
    def _page_skills(self):
        count, chars = skill_store.prompt_size(self.root)
        self._title("Skills", "A skill is a SKILL.md file: when it applies and what the models must do (naming, tests, security, a language's "
                              "style, prompt writing...). Enabled skills go into every model call in chats, the prompt studio and Builds, "
                              f"so keep the set small: {count} enabled now, about {chars:,} characters (≈{chars // 4:,} tokens) per call. "
                              "Improve rounds keep their own rules and don't use skills.")
        skills = skill_store.load_skills(self.root)
        enabled = set(skill_store.enabled_names(self.root, skills))
        if not skills:
            self._note(f"No skills found in {skill_store.skills_dir(self.root)}.")
        for skill in skills:
            control = self._row(f"{skill['name']}{'' if skill['builtin'] else '  ·  yours'}", skill["description"])
            switch = ctk.CTkSwitch(control, text="", width=46)
            if skill["name"] in enabled:
                switch.select()
            switch.configure(command=lambda s=skill, w=switch: self._toggle_skill(s, w))
            switch.pack(side="left", padx=(0, 10))
            ctk.CTkButton(control, text="Open", width=56, height=28, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda p=skill["path"]: self._open_skill(p)).pack(side="left")
            ctk.CTkButton(control, text="Remove", width=70, height=28, font=ctk.CTkFont(size=12), **CHIP,
                          command=lambda s=skill: self._remove_skill(s)).pack(side="left", padx=(6, 0))
        control = self._row("Add skills", "Write a new one from a template, bring in a folder with a SKILL.md (or a .md file), or open the folder.")
        ctk.CTkButton(control, text="New skill…", height=30, font=ctk.CTkFont(size=12), **PRIMARY, command=self._new_skill).pack(side="left", padx=(0, 6))
        ctk.CTkButton(control, text="Add a folder…", height=30, font=ctk.CTkFont(size=12), **CHIP, command=lambda: self._import_skill(True)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(control, text="Add a file…", height=30, font=ctk.CTkFont(size=12), **CHIP, command=lambda: self._import_skill(False)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(control, text="Show folder", height=30, font=ctk.CTkFont(size=12), **CHIP,
                      command=lambda: self._open_folder(skill_store.skills_dir(self.root))).pack(side="left")

    def _toggle_skill(self, skill, switch):
        skill_store.set_enabled(self.root, skill["name"], bool(switch.get()))
        count, chars = skill_store.prompt_size(self.root)
        self._flash(f"{skill['name']} {'enabled' if switch.get() else 'disabled'} · {count} enabled, about {chars:,} characters per call")

    def _open_skill(self, path):
        if self.on_open_file:
            self.on_open_file(path)
        else:
            self._open_folder(os.path.dirname(path))

    def _new_skill(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("New skill", "Name (short, e.g. api-design):", parent=self)
        if not name or not name.strip():
            return
        path = skill_store.new_skill(self.root, name)
        skill_store.set_enabled(self.root, skill_store.slug(name), True)
        self._flash("Skill created and enabled; edit it in the Editor")
        self.refresh()
        self._open_skill(path)

    def _import_skill(self, folder):
        chosen = filedialog.askdirectory(title="A folder with a SKILL.md") if folder else \
            filedialog.askopenfilename(title="A skill file", filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")])
        if not chosen:
            return
        try:
            path = skill_store.import_skill(self.root, chosen)
        except (ValueError, OSError) as e:
            messagebox.showerror("Foundry", str(e))
            return
        name = os.path.basename(os.path.dirname(path))
        skill_store.set_enabled(self.root, name, True)
        self._flash(f"{name} added and enabled")
        self.refresh()

    def _remove_skill(self, skill):
        if not messagebox.askyesno("Remove skill?", f"Delete the skill “{skill['name']}” and its files?"
                                   + (" It is one of the built-in ones; you can recreate it from the Foundry folder later." if skill["builtin"] else "")):
            return
        skill_store.remove_skill(self.root, skill["name"])
        self._flash(f"{skill['name']} removed")
        self.refresh()

    # ============================================================== Terminal
    def _page_terminal(self):
        self._title("Terminal", "The terminals in the Terminal pane (›_ at the top right).")
        self._choice("TERMINAL_SHELL", "Shell", [("auto", "PowerShell 7 if installed, else Windows PowerShell"), ("pwsh", "PowerShell 7 (pwsh)"),
                                                 ("powershell", "Windows PowerShell"), ("cmd", "Command Prompt (cmd)")],
                     "Which shell a new terminal tab runs. Commands the models propose always run in PowerShell.", default="auto", width=300)
        self._entry("TERMINAL_FONT_FAMILY", "Font", "Needs a restart to take effect.", default="", width=200, placeholder=theme.MONO_FAMILY)
        self._entry("TERMINAL_FONT_SIZE", "Font size", "Needs a restart to take effect.", default="12", kind="int", width=80)

    # ============================================================= Shortcuts
    def _page_shortcuts(self):
        self._title("Keyboard shortcuts")
        for keys, what in SHORTCUTS:
            control = self._row(what)
            ctk.CTkLabel(control, text=keys, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=12), text_color=theme.TEXT_MUTED).pack()

    # ============================================================== Advanced
    def _page_advanced(self):
        self._title("Advanced")
        self._section("Engine")
        self._switch("FOUNDRY_OFFLINE_MODE", "Offline mode", "Runs with deterministic stand-ins instead of the models: free, and only a demo. "
                     "Nothing real is built.", default="false")
        self._entry("NODE_BINARY_PATH", "Node.js", "The node program Foundry's engine runs with.", default="node", width=260, kind="path", browse="file")
        self._entry("FOUNDRY_SQLITE_PATH", "Memory database", "Where Foundry keeps its memory of past fixes.", default=".foundry-memory.sqlite",
                    width=260, kind="path")
        self._info("Engine log", os.path.join(self.root, ".foundry.log"), "Also in the Terminal pane's Log tab.")
        self._section("Settings file")
        self._action("Edit .env directly", "Every setting, including keys, lives in Foundry's .env file.", "Open the editor", self._raw_env_dialog)
        self._action("Export settings", "Writes every setting, keys included, to a JSON file. Keep it private.", "Export…", self._export)
        self._action("Import settings", "Reads settings from a JSON file exported here.", "Import…", self._import)
        self._action("Reset to defaults", "Puts every setting back to Foundry's defaults. Your keys are kept.", "Reset…", self._reset)
        self._section("Foundry")
        self._action("Foundry's folder", self.root, "Show folder", lambda: self._open_folder(self.root))
        self._action("Restart Foundry", "Closes this window and opens a fresh one. Runs in progress are stopped.", "Restart",
                     lambda: self.on_restart and self.on_restart())

    def _raw_env_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title(".env")
        dialog.geometry("760x560")
        dialog.transient(self.winfo_toplevel())
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(dialog, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        ctk.CTkLabel(bar, text=self.config_manager.env_path, font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT).pack(side="left")
        box = ctk.CTkTextbox(dialog, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11), wrap="none", fg_color=theme.CODE_BG)
        box.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)

        def reload():
            box.delete("1.0", "end")
            try:
                with open(self.config_manager.env_path, "r", encoding="utf-8") as f:
                    box.insert("end", f.read())
            except OSError:
                box.insert("end", "# No .env file yet. Saving here creates it.\n")

        def save():
            try:
                with open(self.config_manager.env_path, "w", encoding="utf-8") as f:
                    f.write(box.get("1.0", "end-1c"))
                self.config_manager.load()
                self._flash(".env saved and reloaded")
                dialog.destroy()
                self.refresh()
            except OSError as e:
                messagebox.showerror("Foundry", f"Couldn't save .env: {e}")
        ctk.CTkButton(bar, text="Save", width=80, height=28, font=ctk.CTkFont(size=12), **PRIMARY, command=save).pack(side="right")
        ctk.CTkButton(bar, text="Reload", width=80, height=28, font=ctk.CTkFont(size=12), **CHIP, command=reload).pack(side="right", padx=(0, 6))
        reload()

    def _export(self):
        f = filedialog.asksaveasfilename(initialdir=self.root, defaultextension=".json", filetypes=[("JSON", "*.json")])
        if f:
            ok, msg = self.config_manager.export_json(f)
            self._flash("Settings exported" if ok else msg, ok)

    def _import(self):
        f = filedialog.askopenfilename(initialdir=self.root, filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if f:
            ok, msg = self.config_manager.import_json(f)
            self._flash("Settings imported" if ok else msg, ok)
            self.refresh()

    def _reset(self):
        if not messagebox.askyesno("Reset settings?", "Put every setting back to Foundry's defaults? Your API keys are kept."):
            return
        keys = {k: v for k, v in self.config_manager.config.items() if k.endswith("_API_KEY")}
        self.config_manager.reset_defaults()
        self.config_manager.save(keys)
        self._flash("Settings reset")
        self.refresh()

    @staticmethod
    def _open_folder(folder):
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Foundry", f"Couldn't open the folder: {e}")

    # ================================================================= About
    def _page_about(self):
        self._title("About Foundry")
        version = "unknown"
        try:
            with open(os.path.join(self.root, "package.json"), "r", encoding="utf-8") as f:
                version = json.load(f).get("version", version)
        except Exception:
            pass
        self._info("Version", version)
        self._info("Folder", self.root)
        self._info("Engine build", self._build_status(), "The GUI runs the compiled engine in dist/. After changing src/, run npm run build.")
        self._info("Python", f"{sys.version.split()[0]}  ·  customtkinter {ctk.__version__}")
        node_row = self._row("Node.js", "")
        node_label = ctk.CTkLabel(node_row, text="checking…", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED)
        node_label.pack()

        result = {}

        def check_node():
            try:
                out = subprocess.run([str(self.config_manager.get("NODE_BINARY_PATH", "node") or "node"), "--version"],
                                     capture_output=True, text=True, timeout=10,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).stdout.strip()
            except Exception as e:
                out = f"not found ({e.__class__.__name__})"
            result["text"] = out or "not found"

        def poll():  # the worker thread never touches Tk
            if not node_label.winfo_exists():
                return
            if "text" in result:
                node_label.configure(text=result["text"])
            else:
                self.after(100, poll)
        threading.Thread(target=check_node, daemon=True).start()
        self.after(100, poll)
        self._action("Help", "How Foundry works, page by page.", "Open Help", lambda: self.on_open_help and self.on_open_help())

    def _build_status(self):
        dist = os.path.join(self.root, "dist", "index.js")
        if not os.path.isfile(dist):
            return "not built yet: run npm run build"
        built = os.path.getmtime(dist)
        newest = 0
        for folder, _, files in os.walk(os.path.join(self.root, "src")):
            for name in files:
                if name.endswith(".ts"):
                    newest = max(newest, os.path.getmtime(os.path.join(folder, name)))
        return "up to date" if built >= newest else "older than the source: run npm run build"
