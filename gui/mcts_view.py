"""Search tree: what the engine's three-branch search did in a run, read from the engine log (.foundry.log).

For every module of a Build or an Improve round the engine designs, tests and codes three candidate branches
(Alpha, Beta, Gamma; src/engine/tree_search.ts), prunes the ones that fall behind and keeps the first that converges.
The engine logs the module plan, each module's start and result, every pruning with its reason, mutation-test
results and model retries; gui/engine_log.py reads those lines back into runs, modules and branches, and this page
shows the latest run (or an earlier one) exactly as logged. While a run is going, it re-reads the log every two
seconds. The engine also logs one line per branch step (TreeSearchEngine.recordBranchStep: step number, fitness, LSP
errors, mutation kill rate, tests passed or failed); the latest of those is shown on the branch row. Runs logged by
an engine built before that line existed show the branch's state only."""
import os

import customtkinter as ctk

import theme
from engine_log import BRANCHES, RUN_KIND_LABELS, collapse_notes, describe_run, parse_runs, read_log, search_runs
from engine_run_view import format_money
from pane_kit import CHIP, PanePage, clear, empty_state, text_line

LATEST = "Latest run"
MAX_RUNS_LISTED = 15
BRANCH_WORDS = {"converged": ("✓", "converged", theme.SUCCESS), "pruned": ("✕", "pruned", theme.ERROR),
                "failed": ("✕", "architect failed", theme.ERROR), "ended": ("■", "did not converge", theme.TEXT_MUTED),
                "active": ("●", "working", theme.ACCENT), "none": ("○", "no events logged", theme.TEXT_FAINT)}


def step_facts(branch):
    """The branch's latest logged step as short facts: ["step 2", "fitness 75.5", "0 LSP errors", "80% mutants killed",
    "tests passed"]; [] when the run's engine logged no step lines (older builds)."""
    if not branch.get("steps"):
        return []
    facts = [f"step {branch['step']}", f"fitness {branch['fitness']:.1f}",
             f"{branch['lsp_errors']} LSP error{'s' if branch['lsp_errors'] != 1 else ''}"]
    if branch["kill_ratio"] is not None:
        facts.append(f"{branch['kill_ratio']}% mutants killed")
    facts.append("tests passed" if branch["tests_passed"] else "tests failed")
    if branch.get("hidden_passed") is not None:  # the auditor's hidden acceptance suite (GEF), once the visible tests pass
        facts.append("hidden tests passed" if branch["hidden_passed"] else "hidden tests failed")
    return facts


class MCTSView(PanePage):
    """The Search tree page (right pane › Engine internals › Search tree)."""

    def __init__(self, master, workspace_root, terminal=None, **kwargs):
        super().__init__(master, **kwargs)
        self.workspace_root = workspace_root
        self.terminal = terminal
        self.log_path = os.path.join(workspace_root, ".foundry.log")
        self.runs = []
        self.all_runs = []
        self.choice = LATEST
        self._signature = None

        self.header("Search tree", "What the engine's three-branch search did in a run, from its log: for each module, which of "
                                   "Alpha, Beta and Gamma converged, which were pruned and why, each branch's latest logged step "
                                   "(fitness, LSP errors, mutation kill rate, tests), and what the run cost. Only what the engine "
                                   "logged is shown; runs from an older engine build have no step numbers.")
        self._build_controls()
        self.body_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.body_frame.grid(row=self.next_row(), column=0, sticky="ew")
        self.body_frame.grid_columnconfigure(0, weight=1)
        self.refresh()
        self._poll()

    def _build_controls(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=self.next_row(), column=0, sticky="ew", padx=12, pady=(0, 8))
        row.grid_columnconfigure(1, weight=1)
        self.run_menu = ctk.CTkOptionMenu(row, values=[LATEST], width=260, height=28, font=ctk.CTkFont(size=12),
                                          dropdown_font=ctk.CTkFont(size=12), command=self._on_pick)
        self.run_menu.grid(row=0, column=0, sticky="w")
        self.live_label = text_line(row, "", size=11, color=theme.ACCENT, row=0, column=1, sticky="w", padx=(10, 0))
        ctk.CTkButton(row, text="Refresh", width=76, height=28, font=ctk.CTkFont(size=12), command=lambda: self.refresh(force=True), **CHIP).grid(
            row=0, column=2, sticky="e")

    # ------------------------------------------------------------------ data
    def _live(self):
        return bool(self.terminal is not None and getattr(self.terminal, "process_running", False))

    def refresh(self, force=False):
        try:
            stat = os.stat(self.log_path)
            signature = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            signature = None
        live = self._live()
        if signature == self._signature and not force and live == getattr(self, "_was_live", None):
            return
        self._signature, self._was_live = signature, live
        self.all_runs = parse_runs(read_log(self.log_path)) if signature else []
        self.runs = search_runs(self.all_runs)
        names = [LATEST] + [describe_run(r) for r in reversed(self.runs[-MAX_RUNS_LISTED:])]
        self.run_menu.configure(values=names)
        if self.choice not in names:
            self.choice = LATEST
        self.run_menu.set(self.choice)
        self.live_label.configure(text="● Foundry is running now; this page follows the log" if live else "")
        self._render()

    def _poll(self):
        if not self.winfo_exists():
            return
        if self.winfo_ismapped():
            self.refresh()
        self.after(2000, self._poll)

    def _on_pick(self, choice):
        self.choice = choice
        self._render()

    def _chosen_run(self):
        if not self.runs:
            return None
        if self.choice == LATEST:
            return self.runs[-1]
        for run in reversed(self.runs[-MAX_RUNS_LISTED:]):
            if describe_run(run) == self.choice:
                return run
        return self.runs[-1]

    # ---------------------------------------------------------------- render
    def _render(self):
        clear(self.body_frame)
        run = self._chosen_run()
        if run is None:
            if not os.path.exists(self.log_path):
                text = "No run yet. The engine writes .foundry.log the first time a Build, an Improve round or a chat runs; " \
                       "its search branches appear here."
            elif self.all_runs and self.all_runs[-1]["kind"] == "fatal":
                text = "No Build or Improve run in the log yet. The engine's last start failed: " + "; ".join(self.all_runs[-1]["fatal"][:3])
            else:
                text = "No Build or Improve run in the log yet. Build a project or press Improve Foundry; the search branches appear here."
            self.wrap(empty_state(self.body_frame, text, row=0, column=0, sticky="w", padx=12, pady=8), inset=40)
            return
        row = 0
        row = self._run_card(row, run)
        if not run["modules"]:
            card = self._card(row, "Modules")
            self.wrap(empty_state(card, "No module was planned before the run ended." if run["outcome"] else
                                  "No module planned yet." if self._live() else "No module was logged for this run.",
                                  row=0, column=0, sticky="w"), inset=60)
            row += 1
        for module in run["modules"]:
            row = self._module_card(row, run, module)
        if run["commits"] or run["episodes"]:
            card = self._card(row, "Kept")
            line = 0
            for commit in run["commits"]:
                self.wrap(text_line(card, f"Committed {commit['hash'][:8]}  {commit['message']}", size=11, mono=True, row=line, column=0, sticky="w"), inset=60)
                line += 1
            for episode in run["episodes"]:
                self.wrap(text_line(card, f"Memory episode #{episode['id']} for {episode['module']} (Engine internals › Memory)", size=11,
                                    color=theme.TEXT_MUTED, row=line, column=0, sticky="w"), inset=60)
                line += 1

    def _card(self, row, title=None):
        frame = ctk.CTkFrame(self.body_frame, corner_radius=10, fg_color="transparent", border_width=1, border_color=theme.BORDER)
        frame.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)
        if title:
            text_line(frame, title, size=12, bold=True, row=0, column=0, sticky="w", padx=14, pady=(10, 2))
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=14, pady=(4 if title else 12, 12))
        body.grid_columnconfigure(0, weight=1)
        return body

    def _run_card(self, row, run):
        live = self._live() and run is self.runs[-1] and not run["outcome"]
        card = self._card(row, RUN_KIND_LABELS.get(run["kind"], run["kind"]))
        line = 0
        if run["target"]:
            self.wrap(text_line(card, run["target"], size=11, mono=True, color=theme.TEXT_MUTED, row=line, column=0, sticky="w"), inset=60)
            line += 1
        facts = [f"started {run['started'][:8]}", f"last line {run['ended'][:8]}"]
        if run["limit_usd"] is not None:
            facts.append(f"spending limit {format_money(run['limit_usd'])}")
        facts.append(f"spent {format_money(run['spent_usd'])}" if run["spent_usd"] is not None else "no spend logged")
        if run["rounds"]:
            facts.append(f"up to {run['rounds']} round{'s' if run['rounds'] != 1 else ''}")
        if run["problems"]:
            facts.append(f"{run['problems']} warning{'s' if run['problems'] != 1 else ''}/error{'s' if run['problems'] != 1 else ''} logged")
        self.wrap(text_line(card, "  ·  ".join(facts), size=11, color=theme.TEXT_FAINT, row=line, column=0, sticky="w", pady=(2, 0)), inset=60)
        line += 1
        if run["health"]:
            self.wrap(text_line(card, f"Health at the start: {run['health']}", size=11, color=theme.TEXT_MUTED, row=line, column=0, sticky="w",
                                pady=(2, 0)), inset=60)
            line += 1
        if live:
            text, color = "● Running now (Terminal › Foundry shows the output)", theme.ACCENT
        elif run["outcome"]:
            glyph = "✓" if run["outcome_ok"] else "■" if run["outcome_ok"] is None else "✕"
            text = f"{glyph} {' '.join(run['outcome'].split())[:600]}"
            color = theme.SUCCESS if run["outcome_ok"] else theme.TEXT_MUTED if run["outcome_ok"] is None else theme.ERROR
        else:
            text, color = "■ Ended without a closing line: stopped, or the engine was interrupted.", theme.TEXT_MUTED
        self.wrap(text_line(card, text, size=12, color=color, row=line, column=0, sticky="w", pady=(6, 0)), inset=60)
        return row + 1

    def _module_card(self, row, run, module):
        live = self._live() and run is self.runs[-1] and not run["outcome"]
        title = f"Module {module['index']}  ·  {module['name']}" if module["index"] else f"Module  ·  {module['name']}"
        card = self._card(row, title)
        line = 0
        if module["path"] or module["imports"]:
            detail = module["path"] + (f"  ·  imports {', '.join(module['imports'])}" if module["imports"] else "")
            self.wrap(text_line(card, detail, size=11, mono=True, color=theme.TEXT_FAINT, row=line, column=0, sticky="w"), inset=60)
            line += 1
        status = module["status"]
        if status == "converged":
            text = f"✓ Converged on Branch {module['winner']}" + (f" after {module['iterations']} iteration{'s' if module['iterations'] != 1 else ''}"
                                                                   if module["iterations"] else "")
            color = theme.SUCCESS
        elif status == "failed":
            text = "✕ Did not converge"
            if module["iterations"]:
                text += f" after {module['iterations']} iteration{'s' if module['iterations'] != 1 else ''}"
            if module["cost_usd"] is not None:
                text += f" · cost {format_money(module['cost_usd'])}"
            if module["problem"]:
                text += f": {' '.join(module['problem'].split())[:300]}"
            color = theme.ERROR
        elif status == "skipped":
            text, color = f"— Skipped: {module['problem'] or 'an earlier module did not converge'}", theme.TEXT_FAINT
        elif status == "running":
            text, color = ("● In progress", theme.ACCENT) if live else ("■ Ended without a result; the run stopped here", theme.TEXT_MUTED)
        else:
            text, color = "○ Not started", theme.TEXT_FAINT
        self.wrap(text_line(card, text, size=12, color=color, row=line, column=0, sticky="w", pady=(2, 4)), inset=60)
        line += 1
        branches = module["branches"]
        if all(b["status"] == "none" and not b["notes"] and not b["retries"] for b in branches.values()):
            if status not in ("skipped", "planned"):
                self.wrap(empty_state(card, "No branch events were logged for this module.", row=line, column=0, sticky="w"), inset=60)
            return row + 1
        table = ctk.CTkFrame(card, fg_color="transparent")
        table.grid(row=line, column=0, sticky="ew")
        table.grid_columnconfigure(2, weight=1)
        for index, name in enumerate(BRANCHES):
            branch = branches[name]
            state = branch["status"]
            if state == "active" and not live:
                state = "ended"
            glyph, word, color = BRANCH_WORDS.get(state, BRANCH_WORDS["none"])
            text_line(table, glyph, size=12, color=color, row=index, column=0, sticky="n", padx=(0, 6), pady=(2, 0))
            text_line(table, name, size=12, bold=True, row=index, column=1, sticky="nw", padx=(0, 10), pady=(2, 0))
            notes = collapse_notes(branch["notes"])
            if branch["retries"]:
                notes.append(f"{branch['retries']} model retr{'y' if branch['retries'] == 1 else 'ies'}"
                             + (f" (last: {branch['last_retry']})" if branch["last_retry"] else ""))
            headline = " · ".join([word] + step_facts(branch))
            text = headline + ("\n" + "\n".join(f"· {n}" for n in notes) if notes else "")
            self.wrap(text_line(table, text, size=11, color=color if not notes else theme.TEXT_MUTED, row=index, column=2, sticky="w",
                                pady=(2, 0)), inset=130)
        return row + 1
