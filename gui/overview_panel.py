"""The right pane's default page, modeled on Claude's session panel: what Foundry is working on, how much
of the spending limit is used, which AI models are connected, and the files the last run changed."""
import os
from datetime import datetime

import customtkinter as ctk

import theme
from engine_run_view import OUTCOME_COLORS, OUTCOME_ICONS, format_money
from providers import current_synthesizer

OUTCOME_WORDS = {"done": "finished", "not-finished": "not finished", "spending-limit": "hit the spending limit",
                 "stopped": "stopped by you", "error": "error", "undone": "undone"}

MODELS = [  # (label, role, key settings that count as connected, model setting)
    ("Claude", "architect", ("ANTHROPIC_API_KEY",), "ANTHROPIC_MODEL"),
    ("Gemini", "auditor", ("GEMINI_API_KEY",), "GEMINI_MODEL"),
    ("OpenAI", "synthesizer", ("ASTRA_API_KEY", "OPENAI_API_KEY"), "ASTRA_MODEL"),
]


class OverviewPanel(ctk.CTkScrollableFrame):
    def __init__(self, master, config_manager, on_open_settings, on_open_file, on_open_folder):
        super().__init__(master, corner_radius=0, fg_color=theme.BG)
        self.config_manager = config_manager
        self.on_open_settings = on_open_settings
        self.on_open_file = on_open_file
        self.on_open_folder = on_open_folder
        self.grid_columnconfigure(0, weight=1)
        self._folder = ""
        self._small = ctk.CTkFont(size=12)
        self._tiny = ctk.CTkFont(size=11)

        self.target_card, target = self._card(0, "Working on")
        self.target_name = ctk.CTkLabel(target, text="", anchor="w", font=self._small, text_color=theme.TEXT)
        self.target_name.grid(row=0, column=0, sticky="ew")
        self.target_path = ctk.CTkLabel(target, text="", anchor="w", justify="left", font=self._tiny,
                                        text_color=theme.TEXT_FAINT, wraplength=240)
        self.target_path.grid(row=1, column=0, sticky="ew")
        self.folder_link = self._link(target, "Show folder", lambda: self.on_open_folder(self._folder))
        self.folder_link.grid(row=2, column=0, sticky="w", pady=(4, 0))

        self.spend_card, spend = self._card(1, "Spending")
        self.spend_headline = ctk.CTkLabel(spend, text="", anchor="w", font=self._small, text_color=theme.TEXT)
        self.spend_headline.grid(row=0, column=0, sticky="w")
        self.spend_amount = ctk.CTkLabel(spend, text="", anchor="e", font=self._small, text_color=theme.TEXT_FAINT)
        self.spend_amount.grid(row=0, column=1, sticky="e")
        self.spend_bar = ctk.CTkProgressBar(spend, height=4)
        self.spend_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        self.spend_rows = ctk.CTkFrame(spend, fg_color="transparent")
        self.spend_rows.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.spend_rows.grid_columnconfigure(0, weight=1)

        self.models_card, models = self._card(2, "AI models")
        self.model_rows = ctk.CTkFrame(models, fg_color="transparent")
        self.model_rows.grid(row=0, column=0, sticky="ew")
        self.model_rows.grid_columnconfigure(1, weight=1)
        self._link(models, "Settings ›", self.on_open_settings).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.output_card, outputs = self._card(3, "Last run")
        outputs.grid_columnconfigure(0, weight=0)
        outputs.grid_columnconfigure(1, weight=1)
        self.output_icon = ctk.CTkLabel(outputs, text="", width=16, font=self._small, text_color=theme.TEXT_FAINT)
        self.output_icon.grid(row=0, column=0, sticky="nw")
        self.output_title = ctk.CTkLabel(outputs, text="", anchor="w", justify="left", font=self._small,
                                         text_color=theme.TEXT, wraplength=240)
        self.output_title.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.output_meta = ctk.CTkLabel(outputs, text="", anchor="w", justify="left", font=self._tiny,
                                        text_color=theme.TEXT_FAINT, wraplength=240)
        self.output_meta.grid(row=1, column=1, sticky="ew", padx=(4, 0))
        self.output_files = ctk.CTkFrame(outputs, fg_color="transparent", width=1, height=1)
        self.output_files.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.output_files.grid_columnconfigure(0, weight=1)

        self.bind("<Configure>", self._on_resize, add="+")

    # ---------------------------------------------------------------- layout
    def _card(self, row, title):
        card = ctk.CTkFrame(self, corner_radius=10, fg_color=theme.BG, border_width=1, border_color=theme.BORDER)
        card.grid(row=row, column=0, sticky="ew", padx=(8, 10), pady=(0, 10))
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card, text=title, anchor="w", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=theme.TEXT).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        return card, body

    def _link(self, master, text, command):
        return ctk.CTkButton(master, text=text, height=22, width=10, anchor="w", font=self._tiny, fg_color="transparent",
                             hover_color=theme.SURFACE_HOVER, text_color=theme.TEXT_MUTED, corner_radius=6, command=command)

    def _on_resize(self, event):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        wrap = max(160, int(event.width / scaling) - 70)
        self.target_path.configure(wraplength=wrap)
        for label in (self.output_title, self.output_meta):
            label.configure(wraplength=wrap - 20)

    @staticmethod
    def _clear(frame):
        for child in frame.winfo_children():
            child.destroy()

    # ---------------------------------------------------------------- update
    def update_target(self, name, folder, entries, live=None, chat_spent=0.0, note=None):
        """live: (spent, limit) while a run is going on this target, else None (the last run is shown).
        chat_spent: what the project chat has cost so far. note: shown instead of the folder (e.g. a studio conversation)."""
        self._folder = folder
        self.target_name.configure(text=name)
        self.target_path.configure(text=note or folder or "Pick a folder for the new project")
        if note:
            self.folder_link.grid_remove()
        else:
            self.folder_link.grid()
        self.update_spending(entries, live, chat_spent)
        self._show_last_run(entries, folder)
        self._show_models()

    def update_spending(self, entries, live=None, chat_spent=None):
        last = entries[-1] if entries else None
        if live:
            spent, limit = live
            label = "This run"
        elif last:
            spent, limit = float(last.get("spentUsd", 0) or 0), float(last.get("limitUsd", 0) or 0)
            label = "Last run"
        else:
            spent, limit, label = 0.0, 0.0, None
        ratio = min(1.0, spent / limit) if limit else 0.0
        if label is None:
            self.spend_headline.configure(text="No runs yet")
            self.spend_amount.configure(text="")
        else:
            self.spend_headline.configure(text=f"{label}: {round(ratio * 100)}% of limit" if limit else label)
            self.spend_amount.configure(text=f"{format_money(spent)} / {format_money(limit)}" if limit else format_money(spent))
        self.spend_bar.set(ratio)
        self.spend_bar.configure(progress_color=theme.ACCENT if ratio < 0.75 else theme.WARNING if ratio < 1 else theme.ERROR)

        self._clear(self.spend_rows)
        total = sum(float(e.get("spentUsd", 0) or 0) for e in entries)
        kept = sum(1 for e in entries if e.get("outcome") == "done")
        if chat_spent is not None:
            self._chat_spent = chat_spent  # live spend updates leave the chat total as it was
        rows = [("All runs here", format_money(total))]
        if getattr(self, "_chat_spent", 0.0) > 0:
            rows.append(("Chat here", format_money(self._chat_spent)))
        rows.append(("Runs", f"{len(entries)}" + (f"  ·  {kept} finished" if entries else "")))
        for idx, (key, value) in enumerate(rows):
            ctk.CTkLabel(self.spend_rows, text=key, anchor="w", font=self._tiny, text_color=theme.TEXT_MUTED).grid(
                row=idx, column=0, sticky="w")
            ctk.CTkLabel(self.spend_rows, text=value, anchor="e", font=self._tiny, text_color=theme.TEXT_MUTED).grid(
                row=idx, column=1, sticky="e")

    def _show_models(self):
        self._clear(self.model_rows)
        get = lambda k: str(self.config_manager.get(k, "") or os.environ.get(k, "")).strip()
        offline = get("FOUNDRY_OFFLINE_MODE").lower() == "true"
        _choice, provider = current_synthesizer(self.config_manager.workspace_root, self.config_manager)
        for idx, (label, role, keys, model_key) in enumerate(MODELS):
            if label == "OpenAI" and provider:
                label, keys, model_key = provider["name"], ("ASTRA_API_KEY",), "ASTRA_MODEL"
            # An OpenAI key is optional: without one, Claude also does the synthesizer's job.
            own_key = any(get(k) for k in keys)
            connected = own_key or (role == "synthesizer" and get("ANTHROPIC_API_KEY"))
            color = theme.TEXT_FAINT if offline else theme.SUCCESS if connected else theme.ERROR
            if offline:
                status = "offline mode"
            elif not connected:
                status = "no key: add one in Settings"
            elif not own_key:
                status = "no key: Claude does this job"
            elif provider and role == "synthesizer":
                status = f"{get(model_key) or 'connected'}  ·  code writer"
            else:
                status = get(model_key) or "connected"
            ctk.CTkLabel(self.model_rows, text="●", width=14, font=self._tiny, text_color=color).grid(
                row=2 * idx, column=0, sticky="w", pady=(4 if idx else 0, 0))
            ctk.CTkLabel(self.model_rows, text=f"{label}  ·  {role}", anchor="w", font=self._tiny, text_color=theme.TEXT).grid(
                row=2 * idx, column=1, sticky="w", padx=(4, 0), pady=(4 if idx else 0, 0))
            ctk.CTkLabel(self.model_rows, text=status, anchor="w", font=self._tiny, text_color=theme.TEXT_FAINT, height=14).grid(
                row=2 * idx + 1, column=1, sticky="w", padx=(4, 0))

    def _show_last_run(self, entries, folder):
        self._clear(self.output_files)
        if not entries:
            self.output_icon.configure(text="")
            self.output_title.configure(text="Nothing yet. Files Foundry changes will show up here.", text_color=theme.TEXT_FAINT)
            self.output_meta.configure(text="")
            return
        last = entries[-1]
        outcome = last.get("outcome")
        try:
            when = datetime.fromisoformat(str(last.get("timestamp", "")).replace("Z", "+00:00")).astimezone().strftime("%b %d, %H:%M")
        except ValueError:
            when = ""
        # A Build from the chat carries the conversation after its first line; the first line is the request.
        lines = str(last.get("task", "")).strip().splitlines()
        task = " ".join(lines[0].split()) if lines else "Run"
        self.output_icon.configure(text=OUTCOME_ICONS.get(outcome, "•"), text_color=OUTCOME_COLORS.get(outcome, theme.TEXT_FAINT))
        self.output_title.configure(text=task[:140] + ("…" if len(task) > 140 else ""), text_color=theme.TEXT)
        self.output_meta.configure(text=f"{when}  ·  {OUTCOME_WORDS.get(outcome, outcome or '')}  ·  "
                                        f"{format_money(float(last.get('spentUsd', 0) or 0))}")
        files = [f for f in (last.get("filesChanged") or []) if isinstance(f, str)]
        for idx, rel in enumerate(files[:8]):
            path = rel if os.path.isabs(rel) else os.path.join(folder, rel)
            exists = os.path.isfile(path)
            btn = ctk.CTkButton(self.output_files, text=f"‹›  {rel}", anchor="w", height=22, font=self._tiny,
                                fg_color="transparent", hover_color=theme.SURFACE_HOVER, corner_radius=6,
                                text_color=theme.TEXT_MUTED if exists else theme.TEXT_FAINT,
                                state="normal" if exists else "disabled",
                                command=lambda p=path: self.on_open_file(p))
            btn.grid(row=idx, column=0, sticky="ew")
        if len(files) > 8:
            ctk.CTkLabel(self.output_files, text=f"and {len(files) - 8} more", anchor="w", font=self._tiny,
                         text_color=theme.TEXT_FAINT).grid(row=8, column=0, sticky="w", padx=8)
