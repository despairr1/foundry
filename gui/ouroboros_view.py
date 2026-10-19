"""Ouroboros: the engine's self-optimization module, as it really is.

While a run goes on, src/singularity/ouroboros.ts measures the engine's own event-loop lag, AST parse time and
heap use. When one passes its limit (the OUROBOROS_* settings) and self-modification is on, it asks the architect
model for a performance patch to Foundry's own source, gates it on the type-check, the test suite and a full build,
and stages it for the next run, keeping the originals under .foundry-ouroboros/. Those measurements live in the
engine process; what reaches this window is what the engine logs under [Ouroboros], so that is what this page shows,
next to the settings it runs with, the staged self-modifications on disk and the spending circuit breaker's freeze file."""
import json
import os
import sys
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import theme
from engine_log import entries_for, read_log
from engine_run_view import format_money
from pane_kit import CHIP, PanePage, clear, empty_state, mono_box, set_text, text_line

SETTINGS = [
    ("OUROBOROS_AUTO_HEAL", "Self-modification", "Patch Foundry's own source when a run detects a bottleneck"),
    ("OUROBOROS_MAX_LAG_MS", "Event-loop lag limit", "mean lag, in ms, above which a bottleneck is reported"),
    ("OUROBOROS_MAX_AST_MS", "Parse-time limit", "one AST parse, in ms"),
    ("OUROBOROS_MAX_HEAP_MB", "Heap limit", "engine heap in use, in MB"),
    ("OUROBOROS_AUTO_RESTART", "Rebuild after a self-patch", "recompile Foundry when a patch to its own code converges"),
]


class OuroborosView(PanePage):
    """The Ouroboros page (right pane › Engine internals › Ouroboros)."""

    def __init__(self, master, workspace_root, config_manager=None, terminal=None, on_force_restart=None,
                 on_open_settings=None, **kwargs):
        super().__init__(master, **kwargs)
        self.workspace_root = workspace_root
        self.config_manager = config_manager
        self.terminal = terminal
        self.on_force_restart = on_force_restart
        self.on_open_settings = on_open_settings

        self.header("Ouroboros", "The engine's self-optimization module. During a run it measures its own event-loop lag, parse time "
                                 "and heap; past a limit it may patch its own source, verify the patch with the type-check, the tests "
                                 "and a build, and stage it for the next run. Below: the settings it runs with, what it has logged, "
                                 "and what it has staged.")
        self._build_settings()
        self._build_events()
        self._build_staged()
        self._build_freeze()
        self._build_actions()
        self.refresh()

    # ---------------------------------------------------------------- layout
    def _build_settings(self):
        body = self.card("Settings (Settings › Improve › Ouroboros)")
        body.grid_columnconfigure(1, weight=1)
        self.setting_values = {}
        for row, (key, title, meaning) in enumerate(SETTINGS):
            text_line(body, title, size=12, row=row, column=0, sticky="w", pady=1)
            value = text_line(body, "", size=12, mono=True, row=row, column=1, sticky="w", padx=(12, 0), pady=1)
            self.wrap(text_line(body, meaning, size=11, color=theme.TEXT_FAINT, row=row, column=2, sticky="w", padx=(12, 0), pady=1), inset=330)
            self.setting_values[key] = value
        if self.on_open_settings:
            ctk.CTkButton(body, text="Open Settings", width=110, height=26, font=ctk.CTkFont(size=12), command=self.on_open_settings,
                          **CHIP).grid(row=len(SETTINGS), column=0, sticky="w", pady=(8, 0))

    def _build_events(self):
        body = self.card("What Ouroboros logged (.foundry.log, lines tagged [Ouroboros])")
        self.events_empty = self.wrap(empty_state(body, ""), inset=40)
        self.events_box = mono_box(body, height=150)
        self.events_count = text_line(body, "", size=11, color=theme.TEXT_FAINT)

    def _build_staged(self):
        body = self.card("Staged self-modifications (.foundry-ouroboros/)")
        self.staged_frame = body

    def _build_freeze(self):
        body = self.card("Spending circuit breaker (FOUNDRY_FREEZE_PATH)")
        self.freeze_frame = body

    def _build_actions(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=self.next_row(), column=0, sticky="w", padx=12, pady=(0, 10))
        ctk.CTkButton(row, text="Refresh", width=80, height=28, font=ctk.CTkFont(size=12), command=self.refresh, **CHIP).pack(side="left")
        if self.terminal is not None:
            ctk.CTkButton(row, text="Rebuild engine (npm run build)", width=200, height=28, font=ctk.CTkFont(size=12),
                          command=self._rebuild, **CHIP).pack(side="left", padx=(8, 0))
        ctk.CTkButton(row, text="Relaunch window…", width=130, height=28, font=ctk.CTkFont(size=12), command=self._relaunch,
                      **CHIP).pack(side="left", padx=(8, 0))
        self.wrap(text_line(self, "Rebuild compiles src/ into dist/, which is what every Build, Improve round and chat runs; a staged "
                                  "self-modification is already built when it is staged. Relaunch restarts this window.",
                            size=11, color=theme.TEXT_FAINT, row=self.next_row(), column=0, sticky="w", padx=12, pady=(0, 12)))

    # ------------------------------------------------------------------ data
    def refresh(self):
        self._refresh_settings()
        self._refresh_events()
        self._refresh_staged()
        self._refresh_freeze()

    def _setting(self, key, default=""):
        value = str(self.config_manager.get(key, default) if self.config_manager is not None else os.environ.get(key, default) or default)
        return value.strip()

    def _refresh_settings(self):
        for key, _title, _meaning in SETTINGS:
            raw = self._setting(key)
            if key in ("OUROBOROS_AUTO_HEAL", "OUROBOROS_AUTO_RESTART"):
                if raw:
                    shown = "on" if raw.lower() in ("true", "1", "yes", "on") else "off"
                else:  # the engine's own default (src/config.ts) when the setting is unset
                    shown = "on (engine default)" if key == "OUROBOROS_AUTO_HEAL" else "off (engine default)"
                color = theme.SUCCESS if shown.startswith("on") else theme.TEXT_MUTED
            else:
                defaults = {"OUROBOROS_MAX_LAG_MS": "180", "OUROBOROS_MAX_AST_MS": "350", "OUROBOROS_MAX_HEAP_MB": "1024"}
                shown = raw if raw else f"{defaults[key]} (engine default)"
                color = theme.TEXT
            self.setting_values[key].configure(text=shown, text_color=color)

    def _refresh_events(self):
        entries = entries_for(read_log(os.path.join(self.workspace_root, ".foundry.log")), "Ouroboros")
        if not entries:
            self.events_box.grid_forget()
            self.events_count.grid_forget()
            self.events_empty.configure(text="No Ouroboros events in the log. The engine logs here when a run's lag, parse time or heap "
                                             "passes a limit above, and when a self-patch is generated, staged, rejected or reverted. "
                                             "None has happened yet.")
            self.events_empty.grid(row=0, column=0, sticky="w")
            return
        self.events_empty.grid_forget()
        lines = [f"[{e['time'][:8]}] {e['level']:<5} {e['message']}" for e in entries[-200:]]
        set_text(self.events_box, "\n".join(lines), end=True)
        self.events_box.grid(row=0, column=0, sticky="ew")
        self.events_count.configure(text=f"{len(entries)} event{'s' if len(entries) != 1 else ''} in the log"
                                         + (" (last 200 shown)" if len(entries) > 200 else ""))
        self.events_count.grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _refresh_staged(self):
        clear(self.staged_frame)
        folder = os.path.join(self.workspace_root, ".foundry-ouroboros")
        generations = []
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                path = os.path.join(folder, name)
                if os.path.isdir(path):
                    files = []
                    for current, _dirs, names in os.walk(path):
                        files += [os.path.relpath(os.path.join(current, n), path).replace(os.sep, "/") for n in names]
                    generations.append((name, files, os.path.getmtime(path)))
        if not generations:
            self.wrap(empty_state(self.staged_frame, "None. When a self-patch passes verification, the engine keeps the files it replaced "
                                                     "here (one folder per generation) and the new code takes effect on the next run.",
                                  row=0, column=0, sticky="w"), inset=40)
            return
        for row, (name, files, mtime) in enumerate(reversed(generations)):
            when = datetime.fromtimestamp(mtime).strftime("%b %d, %Y %H:%M")
            text_line(self.staged_frame, f"{name}  ·  {when}", size=12, mono=True, bold=True, row=row * 2, column=0, sticky="w")
            self.wrap(text_line(self.staged_frame, "originals kept: " + (", ".join(files) if files else "(empty)"), size=11,
                                color=theme.TEXT_MUTED, row=row * 2 + 1, column=0, sticky="w", pady=(0, 6)), inset=40)

    def _freeze_path(self):
        configured = self._setting("FOUNDRY_FREEZE_PATH") or ".foundry-freeze.json"
        return configured if os.path.isabs(configured) else os.path.normpath(os.path.join(self.workspace_root, configured))

    def _refresh_freeze(self):
        clear(self.freeze_frame)
        path = self._freeze_path()
        if not os.path.isfile(path):
            self.wrap(empty_state(self.freeze_frame, f"No freeze file at {path}. The engine writes one when a run stops at its "
                                                     f"spending limit, with the ledger of every model call that run paid for.",
                                  row=0, column=0, sticky="w"), inset=40)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self.wrap(empty_state(self.freeze_frame, f"Couldn't read {path}: {e}", row=0, column=0, sticky="w"), inset=40)
            return
        when = str(data.get("timestamp", ""))
        try:
            when = datetime.fromisoformat(when.replace("Z", "+00:00")).astimezone().strftime("%b %d, %Y %H:%M")
        except ValueError:
            pass
        spent, limit = float(data.get("totalSpentUsd", 0) or 0), float(data.get("maxBudgetUsd", 0) or 0)
        ledger = data.get("ledger") if isinstance(data.get("ledger"), list) else []
        module = data.get("moduleBudget") if isinstance(data.get("moduleBudget"), dict) else {}
        lines = [f"Last freeze: {when}  ·  {format_money(spent)} of {format_money(limit)}  ·  {len(ledger)} model call{'s' if len(ledger) != 1 else ''} in the ledger"]
        if module:
            lines.append(f"Module at the time: {module.get('moduleName', '?')} ({format_money(float(module.get('spentUsd', 0) or 0))} of "
                         f"{format_money(float(module.get('maxUsd', 0) or 0))})")
        self.wrap(text_line(self.freeze_frame, "\n".join(lines), size=12, row=0, column=0, sticky="w"), inset=40)
        self.wrap(text_line(self.freeze_frame, f"Reason: {str(data.get('reason', ''))[:400]}", size=11, color=theme.TEXT_MUTED, row=1,
                            column=0, sticky="w", pady=(4, 0)), inset=40)
        self.wrap(text_line(self.freeze_frame, f"{path}. This is the run's record, not a lock: the next run starts normally with its own limit.",
                            size=11, color=theme.TEXT_FAINT, row=2, column=0, sticky="w", pady=(4, 0)), inset=40)

    # --------------------------------------------------------------- actions
    def _rebuild(self):
        if self.terminal is None:
            return
        if self.terminal.process_running:
            messagebox.showinfo("Foundry", "Foundry is running something. Wait for it to finish, or stop it first.")
            return
        self.terminal.run_ai_command("npm run build", self.workspace_root, on_done=lambda code, text: self._rebuilt(code), who="Ouroboros page")

    def _rebuilt(self, code):
        if code == 0:
            messagebox.showinfo("Foundry", "The engine was rebuilt. The next Build, Improve round or chat runs the new code.")
        else:
            messagebox.showwarning("Foundry", f"The build failed (exit code {code}). The output is in the Terminal pane.")

    def _relaunch(self):
        if not messagebox.askyesno("Relaunch Foundry?", "Close this window and start it again? Nothing that is running in the "
                                                        "Terminal pane survives a relaunch."):
            return
        if self.on_force_restart:
            self.on_force_restart("Relaunch from the Ouroboros page")
        else:
            import subprocess
            subprocess.Popen([sys.executable] + sys.argv, cwd=self.workspace_root)
            self.winfo_toplevel().destroy()
            sys.exit(0)
