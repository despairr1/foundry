import json
import os
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import theme
from engine_run_view import CHIP, THINKING_LEVELS, EngineRunView, format_money, load_journal, thinking_label


def load_carryovers(root):
    """Saved partial work of unfinished rounds (.foundry/carryover/*/manifest.json), oldest first."""
    base = os.path.join(root, ".foundry", "carryover")
    manifests = []
    if os.path.isdir(base):
        for name in os.listdir(base):
            try:
                with open(os.path.join(base, name, "manifest.json"), "r", encoding="utf-8") as f:
                    manifests.append(json.load(f))
            except Exception:
                pass
    return sorted(manifests, key=lambda m: m.get("createdAt", ""))


def load_health(root):
    """The latest health check (.foundry/health.json, written by the engine), or {}."""
    try:
        with open(os.path.join(root, ".foundry", "health.json"), "r", encoding="utf-8") as f:
            report = json.load(f)
        return report if isinstance(report, dict) else {}
    except Exception:
        return {}


class SelfImproveView(EngineRunView):
    """One-click self-improvement of the harness: Foundry patches its own engine (src/), verifies the
    change with the type-check and the full test suite, rebuilds itself, and remembers every round."""

    go_text = "Improve Foundry"
    limit_pref_key = "UI_SELF_IMPROVE_LIMIT"

    def __init__(self, master, workspace_root, config_manager, terminal, **kwargs):
        super().__init__(master, workspace_root, config_manager, terminal, **kwargs)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        self.add_title(
            "Self-Improve Foundry",
            "Foundry studies its own logs, failures and test results, picks an improvement to its own engine, "
            "tests it with its full test suite, and updates itself. It remembers every improvement and every failed "
            "attempt, so each round builds on the last. A change that breaks anything is rolled back automatically."
        )

        self.add_section("Diagnostics", top=2)
        self.add_hint("Foundry's real health: its type-check, its test suite and recent problems from its log. A round starts from "
                      "this evidence; “Fix this” puts a problem in the focus box.")
        self.diag_card = ctk.CTkFrame(self.body, corner_radius=10, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER)
        self.diag_card.grid(row=self._next_row(), column=0, sticky="ew", pady=(8, 2), **self.pad)
        self.diag_card.grid_columnconfigure(0, weight=1)

        self.add_section("What to improve")
        self.focus_entry = ctk.CTkEntry(
            self.body, height=38, font=ctk.CTkFont(size=13),
            placeholder_text="Optional. Leave empty to let Foundry choose, or e.g. “make JSON parsing of model output more reliable”"
        )
        self.focus_entry.grid(row=self._next_row(), column=0, sticky="ew", pady=(6, 2), **self.pad)
        self.add_hint("With no focus (or just “improve yourself”), the round scouts first: Claude proposes a few small, checkable "
                      "improvements from the evidence above, Gemini picks the best, and that becomes the focus.")

        self.add_section("Rounds")
        self.rounds_picker = ctk.CTkSegmentedButton(self.body, values=["1", "3", "5"], height=30, width=150,
                                                    font=ctk.CTkFont(size=12, weight="bold"),
                                                    selected_color=theme.ACCENT, selected_hover_color=theme.ACCENT_HOVER)
        rounds = str(self.config_manager.get("UI_SELF_IMPROVE_ROUNDS", "1"))
        self.rounds_picker.set(rounds if rounds in ("1", "3", "5") else "1")
        self.rounds_picker.grid(row=self._next_row(), column=0, sticky="w", pady=(6, 2), **self.pad)
        self.add_hint("Each round is one improvement. It stops early if two rounds in a row don't work out.")

        self.add_section("Thinking")
        self.thinking_picker = ctk.CTkSegmentedButton(self.body, values=[label for _, label, _ in THINKING_LEVELS], height=30,
                                                      font=ctk.CTkFont(size=12), selected_color=theme.ACCENT,
                                                      selected_hover_color=theme.ACCENT_HOVER, command=self._on_thinking)
        self.thinking_picker.set(thinking_label(self.thinking_level()))
        self.thinking_picker.grid(row=self._next_row(), column=0, sticky="w", pady=(6, 2), **self.pad)
        self.add_hint("How hard the models think. Higher is more careful and slower, and costs more. "
                      "The same setting as under the message box in a project.")

        self.add_limit_row("Spending limit",
                           "The whole session stops before it spends more than this on AI usage. "
                           "Improvements that already passed are kept.")

        # Shown only while an unfinished round's partial work is saved and waiting to be continued.
        self.carry_row = ctk.CTkFrame(self.body, corner_radius=8, fg_color=theme.SURFACE, border_width=1,
                                      border_color=theme.BORDER)
        self.carry_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.carry_row, text="↻", width=20, font=ctk.CTkFont(size=15), text_color=theme.ACCENT).grid(
            row=0, column=0, rowspan=2, padx=(12, 6), pady=10, sticky="n")
        self.carry_title = self.wrap(ctk.CTkLabel(self.carry_row, text="", font=ctk.CTkFont(size=13), text_color=theme.TEXT,
                                                  anchor="w", justify="left"), inset=220)
        self.carry_title.grid(row=0, column=1, sticky="ew", pady=(9, 0))
        self.carry_detail = self.wrap(ctk.CTkLabel(self.carry_row, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT,
                                                   anchor="w", justify="left"), inset=220)
        self.carry_detail.grid(row=1, column=1, sticky="ew", pady=(0, 9))
        self.carry_discard = ctk.CTkButton(self.carry_row, text="Start fresh instead", width=140, height=28,
                                           font=ctk.CTkFont(size=12), command=self._discard_carryover, **CHIP)
        self.carry_discard.grid(row=0, column=2, rowspan=2, padx=12)
        self.carry_row_index = self._next_row()

        self.add_go_button()
        self.add_activity_feed()
        self.add_result_card()
        self.add_history("Improvement history",
                         "Foundry's memory of every improvement it kept, every attempt that failed, and every change you undid.")
        self.undo_button = ctk.CTkButton(self.history_header, text="↶  Undo last improvement", height=28, width=180,
                                         font=ctk.CTkFont(size=12), command=self._undo_last, **CHIP)
        self.undo_button.pack(side="right")

    # ------------------------------------------------------------------ state
    def _entries(self):
        return load_journal(self.workspace_root)

    def _undoable(self, entries):
        undone = {e.get("backupId") for e in entries if e.get("outcome") == "undone"}
        for entry in reversed(entries):
            if entry.get("outcome") == "done" and entry.get("backupId") and entry.get("backupId") not in undone:
                return entry
        return None

    def _on_thinking(self, label):
        self.set_thinking_level(next((level for level, text, _ in THINKING_LEVELS if text == label), "high"))

    def refresh(self):
        self.thinking_picker.set(thinking_label(self.thinking_level()))  # it may have changed in a project
        self._render_diagnostics()
        entries = self._entries()
        self.render_history(entries, "No improvements yet. Each round will show up here, newest first.")
        undoable = self._undoable(entries)
        self.undo_button.configure(state="normal" if undoable and not self.running else "disabled")
        self._show_pending_carryover()
        if self.on_status and not self.running:
            self.on_status(project=self.workspace_root)

    # ------------------------------------------------------------ diagnostics
    def _render_diagnostics(self):
        for child in self.diag_card.winfo_children():
            child.destroy()
        health = load_health(self.workspace_root)
        header = ctk.CTkFrame(self.diag_card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        header.grid_columnconfigure(0, weight=1)
        when = ""
        if health.get("checkedAt"):
            try:
                when = datetime.fromisoformat(str(health["checkedAt"]).replace("Z", "+00:00")).astimezone().strftime("%b %d, %H:%M")
            except ValueError:
                when = ""
        ctk.CTkLabel(header, text=f"Checked {when}" if when else "Not checked yet. Every Improve round checks; or check now, free.",
                     font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT, anchor="w").grid(row=0, column=0, sticky="w")
        self.check_button = ctk.CTkButton(header, text="Check now", width=92, height=26, font=ctk.CTkFont(size=12), **CHIP,
                                          command=self.check_health, state="disabled" if self.running else "normal")
        self.check_button.grid(row=0, column=1, sticky="e")
        row = 1
        for key, name in (("typecheck", "Type-check"), ("tests", "Test suite")):
            check = health.get(key) or {}
            if not health:
                text, color, fix = f"{name}: unknown", theme.TEXT_FAINT, None
            elif check.get("ok"):
                text, color, fix = f"✓  {name} passing", theme.SUCCESS, None
            else:
                detail = str(check.get("detail") or "see the Terminal's Log tab")
                text, color = f"✕  {name} failing: {detail[:220]}", theme.ERROR
                fix = f"Fix the failing {name.lower()}: {detail[:400]}"
            self._diag_row(row, text, color, fix)
            row += 1
        problems = [str(p) for p in (health.get("problems") or [])]
        quota = [p for p in problems if "insufficient_quota" in p or "no credits remaining" in p.lower()]
        if quota:
            who = "OpenAI" if "AstraSynthesizer" in quota[-1] or "openai" in quota[-1].lower() else "A model provider"
            line = ctk.CTkFrame(self.diag_card, fg_color="transparent")
            line.grid(row=row, column=0, sticky="ew", padx=14, pady=1)
            line.grid_columnconfigure(0, weight=1)
            self.wrap(ctk.CTkLabel(line, text=f"✕  {who} reports no credits remaining ({len(quota)} failed calls). Until credits are added, "
                                              f"or another code writer is chosen in Settings › AI models, nothing can write code.",
                                   font=ctk.CTkFont(size=12), text_color=theme.ERROR, anchor="w", justify="left"), inset=180).grid(
                row=0, column=0, sticky="ew")
            if self.on_open_settings:
                ctk.CTkButton(line, text="Settings", width=66, height=24, font=ctk.CTkFont(size=11), **CHIP,
                              command=self.on_open_settings).grid(row=0, column=1, padx=(8, 0))
            row += 1
            problems = [p for p in problems if p not in quota]
        if health and not problems and not quota:
            self._diag_row(row, "✓  No recent warnings or errors in the log", theme.SUCCESS, None)
            row += 1
        for problem in problems[-6:]:
            self._diag_row(row, f"⚠  {problem[:220]}", theme.WARNING, f"Fix the cause of this log problem: {problem[:400]}")
            row += 1
        if len(problems) > 6:
            ctk.CTkLabel(self.diag_card, text=f"and {len(problems) - 6} more in the Terminal's Log tab", font=ctk.CTkFont(size=11),
                         text_color=theme.TEXT_FAINT, anchor="w").grid(row=row, column=0, sticky="w", padx=14, pady=(0, 6))
            row += 1
        ctk.CTkFrame(self.diag_card, height=6, fg_color="transparent").grid(row=row, column=0)

    def _diag_row(self, row, text, color, fix):
        line = ctk.CTkFrame(self.diag_card, fg_color="transparent")
        line.grid(row=row, column=0, sticky="ew", padx=14, pady=1)
        line.grid_columnconfigure(0, weight=1)
        self.wrap(ctk.CTkLabel(line, text=text, font=ctk.CTkFont(size=12), text_color=color, anchor="w", justify="left"), inset=180).grid(
            row=0, column=0, sticky="ew")
        if fix:
            ctk.CTkButton(line, text="Fix this", width=66, height=24, font=ctk.CTkFont(size=11), **CHIP,
                          command=lambda: self.set_focus(fix)).grid(row=0, column=1, padx=(8, 0))

    def set_focus(self, text):
        self.focus_entry.delete(0, "end")
        self.focus_entry.insert(0, text)
        self.focus_entry.focus_set()

    def check_health(self):
        """Settings › Improve › Check now, and the Diagnostics card: the type-check, the tests and the log scan. Free."""
        if self.running:
            return
        if self.launch(["self-improve", "--check"], "Foundry: health check", {"kind": "check", "limit": 0.0}, "Checking"):
            self.feed.prompt("Check Foundry's health (no AI calls)")
            self.check_button.configure(state="disabled")

    def _show_pending_carryover(self):
        pending = [c for c in load_carryovers(self.workspace_root) if c.get("status") == "pending"]
        if not pending:
            self.carry_row.grid_remove()
            return
        carry = pending[-1]
        files = len(carry.get("files", []))
        label = carry.get("label") or "Automatic self-improvement"
        self.carry_title.configure(text=f"An unfinished round is saved: “{label[:90]}”")
        self.carry_detail.configure(
            text=f"{carry.get('createdAt', '')[:10]} · {format_money(float(carry.get('spentUsd', 0) or 0))} of work · "
                 f"{files} file{'s' if files != 1 else ''}. Improve Foundry continues it when the focus is empty or the same.")
        self.carry_discard.configure(state="disabled" if self.running else "normal")
        self.carry_row.grid(row=self.carry_row_index, column=0, sticky="ew", padx=self.pad["padx"], pady=(18, 0))

    def _discard_carryover(self):
        if self.running:
            return
        if not messagebox.askyesno("Start fresh?", "Forget the unfinished round's saved work? The next round will start "
                                                   "from scratch. Nothing in Foundry's code changes."):
            return
        if self.launch(["self-improve", "--discard-carryover"], "Foundry: discard unfinished round", {"kind": "discard"},
                       "Discarding"):
            self.step("Discarding the saved partial work")

    # -------------------------------------------------------------------- run
    def _on_go_pressed(self):
        if self.running:
            self.stop("Stop now? Improvements that already passed are kept; the round in progress is rolled back.")
            return
        limit = self.read_limit()
        if limit is None:
            return
        missing = self.missing_keys()
        if missing:
            self.show_keys_needed(missing)
            return
        focus = self.focus_entry.get().strip()
        rounds = self.rounds_picker.get() or "1"
        self.remember({self.limit_pref_key: f"{limit:.2f}", "UI_SELF_IMPROVE_ROUNDS": rounds})

        args = ["self-improve", "--budget-usd", f"{limit:.2f}", "--rounds", rounds] + self.thinking_args()
        if focus:
            args += ["--focus", focus]
        if self.launch(args, "Foundry: self-improve", {"kind": "improve", "limit": limit, "rounds": int(rounds), "round": 1},
                       "Starting"):
            self.undo_button.configure(state="disabled")
            self.feed.prompt(focus or "Improve yourself (no focus given; choose from the evidence)")

    def _undo_last(self):
        if self.running:
            return
        entry = self._undoable(self._entries())
        if not entry:
            return
        files = ", ".join(entry.get("filesChanged", [])[:6]) or "its files"
        if not messagebox.askyesno("Undo improvement?", f"Roll back the improvement from {entry.get('timestamp', '')[:10]}?\n\n"
                                                        f"{entry.get('summary', '')}\n\nFiles: {files}\n\n"
                                                        "Foundry will be rebuilt, and it will remember not to redo this."):
            return
        if self.launch(["self-improve", "--undo"], "Foundry: undo self-improvement", {"kind": "undo"}, "Rolling back"):
            self.undo_button.configure(state="disabled")
            self.feed.prompt("Undo the last improvement")
            self.step("Restoring the files from before the improvement")

    def _round_prefix(self):
        rounds = self.run_state.get("rounds", 1)
        return f"Round {self.run_state.get('round', 1)}/{rounds} · " if rounds > 1 else ""

    def handle_event(self, event):
        kind = event.get("type")
        prefix = self._round_prefix()
        if kind == "start":
            self.run_state["rounds"] = event.get("rounds", self.run_state.get("rounds", 1))
            remembered = event.get("historyEntries", 0)
            rounds = self.run_state["rounds"]
            self.detail(f"{rounds} round{'s' if rounds != 1 else ''} · {format_money(event.get('limitUsd', 0) or 0)} limit"
                        f"{f' · remembers {remembered} earlier round' + ('s' if remembered != 1 else '') if remembered else ''}")
        elif kind == "self-step":
            self.run_state["round"] = event.get("round", 1)
            prefix = self._round_prefix()
            step = event.get("step")
            if step == "health":
                self.step("Checking Foundry's health")
            elif step == "evidence":
                self.step(f"{prefix}Reading its logs and past attempts")
                if event.get("detail"):
                    self.detail(event["detail"])
            elif step == "continuing":
                self.step(f"{prefix}Continuing the unfinished round")
                if event.get("detail"):
                    self.detail(f"continuing {event['detail']}")
            elif step == "scouting":
                if event.get("detail"):
                    self.detail(event["detail"])
                else:
                    self.step(f"{prefix}Scouting for the best improvement")
            elif step == "chosen":
                self.detail(f"chose: {event.get('detail', '')}", "success")
            elif step == "saving":
                self.detail(event.get("detail", "saved partial work for the next round"), "warning")
            elif step == "improving":
                self.step(f"{prefix}Designing an improvement")
            elif step == "verifying":
                self.step(f"{prefix}Running the full test suite on the change")
            elif step == "updating":
                self.step(f"{prefix}Rebuilding Foundry with the change")
            elif step == "reverting":
                self.feed.finish("failed")
                self.step(f"{prefix}Rolling back the change")
        elif kind == "health":
            if event.get("typecheck") and event.get("tests"):
                self.detail("type-check passing · test suite passing", "success")
            else:
                failing = "type-check failing" if not event.get("typecheck") else "test suite failing"
                self.detail(f"{failing}{': ' + event['detail'] if event.get('detail') else ''}", "warning")
            if "problems" in event:
                count = len(event.get("problems") or [])
                self.detail(f"{count} recent warning{'s' if count != 1 else ''}/error{'s' if count != 1 else ''} in the log",
                            "warning" if count else "success")
            self._render_diagnostics()
        elif kind == "plan":
            modules = [m.replace("_", " ") for m in event.get("modules", [])]
            if modules:
                self.detail(f"plan: {', '.join(modules)}")
        elif kind == "phase":
            phase = event.get("phase")
            if phase == "testing":
                self.step(f"{prefix}Writing a check that proves it works")
            elif phase == "coding":
                attempt = event.get("attempt", 1)
                self.step(f"{prefix}Writing the change" + ("" if attempt <= 1 else f" (attempt {attempt})"))
                if attempt > 1:
                    self.detail("learning from the last attempt's errors")
            elif phase == "checking":
                self.step(f"{prefix}Testing it in the sandbox")
        elif kind == "module-done":
            if event.get("success"):
                self.detail("sandbox tests and mutation checks passed", "success")
            else:
                self.feed.finish("failed")
                self.detail("could not get the change to pass in the sandbox", "error")
        elif kind == "round-done":
            summary = event.get("summary", "")
            if event.get("kept"):
                self.feed.finish("done")
                self.detail(summary.replace("Kept: ", "kept: "), "success")
            else:
                self.feed.finish("failed")
                self.detail(summary.replace("Not kept: ", "not kept: "), "error")
            self.refresh()

    def show_result(self, event):
        outcome = event.get("outcome")
        summary = event.get("summary", "")
        spent = format_money(float(event.get("spentUsd", 0) or 0))
        problem = (event.get("problem") or "").strip()
        files = event.get("filesChanged") or []
        file_text = f"Files: {', '.join(files[:8])}{' and more' if len(files) > 8 else ''}. " if files else ""
        kind = self.run_state.get("kind")

        if kind == "check":
            self.show_card("Health check done." if outcome == "done" else "The health check found problems.",
                           f"{summary} The Diagnostics card above has the details; “Fix this” turns a problem into a focus.")
        elif kind == "recover":
            self.show_card("Stopped.", f"{summary} Improvements that already passed are kept.")
        elif kind == "discard":
            self.show_card("Starting fresh.", summary)
        elif kind == "undo":
            if outcome == "done":
                self.show_card("Improvement rolled back.", f"{summary}\n\n{file_text}Foundry was rebuilt without it.")
            else:
                self.show_card("Nothing was undone.", problem or summary)
        elif outcome == "done":
            self.show_card("Foundry improved itself.",
                           f"{summary} {file_text}Cost: {spent}. The new version is already in use for the next run; "
                           f"use “Undo last improvement” if you don't want it.")
        elif outcome == "spending-limit":
            self.show_card("Stopped at your spending limit.",
                           f"{summary} Cost: {spent}. Raise the limit to keep going; passed improvements are kept.")
        elif outcome == "not-finished":
            self.show_card("No improvement passed this time.",
                           f"{summary} Cost: {spent}. Nothing was changed. Foundry remembers what it tried and will "
                           f"take a different approach next time.\n\nLast problem: {problem.split(chr(10))[0][:300]}")
        elif problem.startswith("missing-keys"):
            self.show_keys_needed([problem.split(":", 1)[-1].strip()])
        else:
            self.show_card("Self-improvement stopped because of an error.", f"{summary} {problem[:400]}")

    def on_stopped(self):
        if self.run_state.get("kind") != "improve":
            super().on_stopped()
            return
        # The engine was killed mid-round: put back anything the unfinished round changed.
        spent = self.run_state.get("spent", 0.0)

        def recover():
            if self.launch(["self-improve", "--recover"], "Foundry: roll back interrupted round",
                           {"kind": "recover", "limit": 0.0}, "Rolling back", keep_feed=True):
                self.step("Rolling back the unfinished change")
            else:
                self.show_card("Stopped.", f"Spent {format_money(spent)}. The unfinished change will be rolled back "
                                           "automatically the next time you press Improve Foundry.")

        self.when_terminal_free(recover)
