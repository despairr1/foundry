"""Reads the engine's log (.foundry.log) for the right-pane pages.

The engine writes one entry per line: `[HH:MM:SS.mmm] [LEVEL] [Component] message` (older builds left out the
space before the message); a multi-line message continues on lines that do not start with `[`. There is no date,
so runs are told apart by the lines that start and end them, not by time.

parse_runs() turns the log into the Builds, Improve sessions and self-improvement sessions it records, each with
the modules it planned and what happened to the search branches (Alpha, Beta, Gamma) of every module. Only what
the engine actually logged is reported: a branch with no logged event is shown as having none."""
import os
import re

LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)\] \[(INFO|WARN|ERROR|DEBUG)\] \[([A-Za-z]+)\]\s?(.*)$")
BRANCHES = ("Alpha", "Beta", "Gamma")

# Components that only the build loop logs: a line from one of them opens a run even in older logs that have no start line.
BUILD_COMPONENTS = ("Convergence", "TreeSearch", "ASTPatcher", "ClaudeArchitect", "GeminiAuditor", "AstraSynthesizer",
                    "FableSynthesizer", "MutationEngine")

_MONEY = r"(\d+(?:\.\d+)?)"
_START_BUILD = re.compile(r"^(Building a new project|Improving the existing project) in (.+?) \(spending limit \$" + _MONEY)
_START_SELF = re.compile(r"^Self-improvement of (.+?): up to (\d+) round\(s\), spending limit \$" + _MONEY)
_END_PREFIXES = ("Finished:", "Not finished:", "Stopped at the $", "Stopped by an error", "Kept ", "No improvement was kept",
                 "Build converged on", "Type-check", "Nothing needed rolling back")
_MODULE_START = re.compile(r"^Module (\d+/\d+): (\S+) -> (\S+)")
_MODULE_CONVERGED = re.compile(r"^Module (\d+/\d+) (\S+) converged on Branch (Alpha|Beta|Gamma) after (\d+) iteration")
_MODULE_FAILED_CLI = re.compile(r"^Module (\d+/\d+) (\S+) did not converge after (\d+) iteration\(s\)\. Cost: \$" + _MONEY)
_MODULE_FAILED = re.compile(r"^Module (\d+/\d+) (\S+) did not converge: (.*)$")
_MODULE_SKIPPED = re.compile(r"^Module (\d+/\d+) (\S+) skipped: (.*)$")
_PLAN_ITEM = re.compile(r"(\d+)\. (\S+) \(([^,)]+)(?:, imports ([^)]*))?\)")
_PRUNED_REASON = re.compile(r"^Pruned Branch (Alpha|Beta|Gamma): (.*)$")
_PRUNED_FITNESS = re.compile(r"^Pruned Branch (Alpha|Beta|Gamma) \(fitness ([\d.]+) vs leader ([\d.]+)\)")
# "Branch Alpha step 2: fitness 75.5, 0 LSP errors, mutation kill 80%, tests passed" (TreeSearchEngine.recordBranchStep);
# "mutation kill not measured" when the tests failed, since mutation testing only runs on passing tests.
_STEP = re.compile(r"^Branch (Alpha|Beta|Gamma) step (\d+): fitness ([\d.]+), (\d+) LSP errors?, "
                   r"mutation kill (?:(\d+)%|not measured), tests (passed|failed)$")
_LETHALITY = re.compile(r"^Branch (Alpha|Beta|Gamma) tests failed mutation lethality \((\d+)%\)")
_HARDENING = re.compile(r"^Test hardening failed on Branch (Alpha|Beta|Gamma): (.*)$")
_ARCHITECT_FAILED = re.compile(r"^Architect failed for Branch (Alpha|Beta|Gamma) on \S+: (.*)$")
_ARCHITECT_FALLBACK = re.compile(r"^Falling back to deterministic contract synthesis on Branch (Alpha|Beta|Gamma)")
_SYNTH_FALLBACK = re.compile(r"on Branch (Alpha|Beta|Gamma)[: ].*falling back to (\w+)", re.IGNORECASE)
_RETRY = re.compile(r"^(\w+)\[(Alpha|Beta|Gamma)\] failed \(attempt (\d+)/(\d+)\): (.*?)\.? Retrying in", re.DOTALL)
_OLD_CONVERGED = re.compile(r"^Build converged on Branch (Alpha|Beta|Gamma)\.(.*?)(?:Total Cost: \$" + _MONEY + r")?$")
_TOTAL = re.compile(r"Total: \$" + _MONEY + r"/\$" + _MONEY)
_COMMIT = re.compile(r"^Committed ([0-9a-f]+):\s*(.*)$")
_EPISODE = re.compile(r"^Indexed winning semantic patch episode #(\d+) for\s*(\S+)")
_FATAL_MESSAGE = re.compile(r'"message":\s*"([^"]*)"')


def parse_lines(text):
    """[{"time", "level", "component", "message"}] in file order; continuation lines join the entry before them."""
    entries = []
    for raw in text.splitlines():
        m = LINE_RE.match(raw)
        if m:
            entries.append({"time": m.group(1), "level": m.group(2), "component": m.group(3), "message": m.group(4).rstrip()})
        elif entries and raw.strip():
            entries[-1]["message"] += "\n" + raw.rstrip()
    return entries


def read_log(path, max_bytes=4_000_000):
    """The log's entries (the last max_bytes of it), or [] when there is no log yet."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop the partial line
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return parse_lines(text)


def entries_for(entries, component):
    """The entries one component logged, in order (e.g. "Ouroboros")."""
    return [e for e in entries if e["component"] == component]


def _new_branch():
    # steps: how many step lines the engine logged for the branch; step, fitness, lsp_errors, kill_ratio (percent, or
    # None when the tests failed and mutation testing did not run) and tests_passed come from the latest of them.
    return {"status": "none", "notes": [], "retries": 0, "last_retry": "", "steps": 0, "step": None, "fitness": None,
            "lsp_errors": None, "kill_ratio": None, "tests_passed": None}


def _new_module(name, index="", path=""):
    return {"name": name, "index": index, "path": path, "imports": [], "status": "planned", "winner": None,
            "iterations": None, "cost_usd": None, "problem": "", "branches": {b: _new_branch() for b in BRANCHES}}


def _new_run(kind, entry):
    return {"kind": kind, "started": entry["time"], "ended": entry["time"], "target": "", "limit_usd": None,
            "rounds": None, "spent_usd": None, "modules": [], "commits": [], "episodes": [], "outcome": "",
            "outcome_ok": None, "problems": 0, "health": "", "fatal": [], "lines": 0}


class _RunBuilder:
    def __init__(self):
        self.runs = []
        self.run = None
        self.orphans = []   # entries since the last run ended; an implicit (old-format) run takes them in

    def feed(self, entry):
        component, message = entry["component"], entry["message"]
        if component == "Fatal":
            self._close()
            run = _new_run("fatal", entry)
            run["fatal"] = _FATAL_MESSAGE.findall(message) or [message.strip()[:300]]
            run["outcome"], run["outcome_ok"] = "The engine could not start.", False
            self.runs.append(run)
            self.orphans = []
            return
        start = self._start_kind(component, message)
        if start:
            self._close()
            self.run = _new_run(start, entry)
            self._describe_start(message)
            self.orphans = []
            return
        if self.run is None:
            if component in BUILD_COMPONENTS:
                self.run = _new_run("run", entry)
                for orphan in self.orphans:
                    self._attach(orphan)
                self.orphans = []
            else:
                self.orphans.append(entry)
                if component == "CLI":  # a chat reply, a stray message: it belongs to no run
                    self.orphans = []
                return
        self._attach(entry)
        if component == "CLI" and message.startswith(_END_PREFIXES):
            self._close()

    def finish(self):
        self._close()
        return self.runs

    def _start_kind(self, component, message):
        if component == "CLI" and _START_BUILD.match(message):
            return "build" if message.startswith("Building") else "improve"
        if component == "SelfImprove":
            if message.startswith("Self-improvement of"):
                return "self-improve"
            if message.startswith("Health:") and self.run is None:
                return "check"
        return None

    def _describe_start(self, message):
        run = self.run
        m = _START_BUILD.match(message)
        if m:
            run["target"], run["limit_usd"] = m.group(2), float(m.group(3))
            return
        m = _START_SELF.match(message)
        if m:
            run["target"], run["rounds"], run["limit_usd"] = m.group(1), int(m.group(2)), float(m.group(3))
            return
        if message.startswith("Health:"):
            run["health"] = message[len("Health:"):].strip()

    def _close(self):
        if self.run is not None:
            self.runs.append(self.run)
            self.run = None

    def _module(self, name=None, index=None):
        for module in self.run["modules"]:
            if (name and module["name"] == name) or (index and module["index"] == index):
                return module
        module = _new_module(name or "(module)", index or "")
        self.run["modules"].append(module)
        return module

    def _current_module(self):
        run = self.run
        running = [m for m in run["modules"] if m["status"] == "running"]
        if running:
            return running[-1]
        return run["modules"][-1] if run["modules"] else self._module("(module)")

    def _attach(self, entry):
        run = self.run
        component, message, level = entry["component"], entry["message"], entry["level"]
        run["ended"] = entry["time"]
        run["lines"] += 1
        if level in ("WARN", "ERROR") and component != "Backoff":
            run["problems"] += 1
        first = message.split("\n", 1)[0]

        if component == "Economics":
            m = _TOTAL.search(first)
            if m:
                run["spent_usd"] = float(m.group(1))
                if run["limit_usd"] is None:
                    run["limit_usd"] = float(m.group(2))
        elif component == "Convergence":
            self._convergence(first)
        elif component == "TreeSearch":
            m = _STEP.match(first)
            if m:
                branch = self._current_module()["branches"][m.group(1)]
                branch["steps"] += 1
                branch.update(step=int(m.group(2)), fitness=float(m.group(3)), lsp_errors=int(m.group(4)),
                              kill_ratio=int(m.group(5)) if m.group(5) is not None else None, tests_passed=m.group(6) == "passed")
                if branch["status"] == "none":
                    branch["status"] = "active"
                return
            m = _PRUNED_REASON.match(first)
            if m:
                branch = self._current_module()["branches"][m.group(1)]
                branch["status"] = "pruned"
                branch["notes"].append(f"Pruned: {m.group(2)[:220]}")
            else:
                m = _PRUNED_FITNESS.match(first)
                if m:
                    branch = self._current_module()["branches"][m.group(1)]
                    branch["status"] = "pruned"
                    branch["notes"].append(f"Pruned: fitness {m.group(2)} against the leader's {m.group(3)}")
        elif component == "Backoff":
            m = _RETRY.match(message)
            if m:
                branch = self._current_module()["branches"][m.group(2)]
                branch["retries"] += 1
                branch["last_retry"] = f"{m.group(1)} attempt {m.group(3)}/{m.group(4)}: {m.group(5).strip()[:160]}"
        elif component in ("AstraSynthesizer", "FableSynthesizer", "ClaudeSynthesizer"):
            m = _SYNTH_FALLBACK.search(first)
            if m:
                self._current_module()["branches"][m.group(1)]["notes"].append(
                    f"{component.replace('Synthesizer', '')} failed to write the code; {m.group(2)} wrote it instead")
        elif component == "ClaudeArchitect":
            m = _ARCHITECT_FALLBACK.match(first)
            if m:
                self._current_module()["branches"][m.group(1)]["notes"].append("Architect fell back to a deterministic contract")
        elif component == "Git":
            m = _COMMIT.match(first)
            if m:
                run["commits"].append({"hash": m.group(1), "message": m.group(2)})
        elif component == "Memory":
            m = _EPISODE.match(first)
            if m:
                run["episodes"].append({"id": int(m.group(1)), "module": m.group(2)})
        elif component == "SelfImprove":
            if first.startswith("Health:"):
                run["health"] = first[len("Health:"):].strip()
        elif component == "CLI":
            self._cli(first, level, message)

    def _convergence(self, first):
        run = self.run
        if first.startswith("Module plan:"):
            items = _PLAN_ITEM.findall(first[len("Module plan:"):])
            for number, name, path, imports in items:
                module = _new_module(name, f"{number}/{len(items)}", path)
                module["imports"] = [i.strip() for i in imports.split(",") if i.strip()] if imports else []
                run["modules"].append(module)
            return
        m = _MODULE_START.match(first)
        if m:
            module = self._module(m.group(2), m.group(1))
            module["index"], module["path"], module["status"] = m.group(1), m.group(3), "running"
            for branch in module["branches"].values():
                if branch["status"] == "none":
                    branch["status"] = "active"
            return
        m = _MODULE_CONVERGED.match(first)
        if m:
            module = self._module(m.group(2), m.group(1))
            module.update(status="converged", winner=m.group(3), iterations=int(m.group(4)))
            module["branches"][m.group(3)]["status"] = "converged"
            for branch in module["branches"].values():
                if branch["status"] == "active":
                    branch["status"] = "ended"
            return
        m = _MODULE_FAILED.match(first)
        if m:
            module = self._module(m.group(2), m.group(1))
            module.update(status="failed", problem=m.group(3))
            return
        m = _LETHALITY.match(first)
        if m:
            self._current_module()["branches"][m.group(1)]["notes"].append(
                f"Tests killed only {m.group(2)}% of mutants; a hardened test suite was generated")
            return
        m = _HARDENING.match(first)
        if m:
            self._current_module()["branches"][m.group(1)]["notes"].append(f"Test hardening failed: {m.group(2)[:200]}")
            return
        m = _ARCHITECT_FAILED.match(first)
        if m:
            branch = self._current_module()["branches"][m.group(1)]
            branch["status"] = "failed"
            branch["notes"].append(f"Architect failed: {m.group(2)[:200]}")

    def _cli(self, first, level, message):
        run = self.run
        m = _MODULE_FAILED_CLI.match(first)
        if m:
            module = self._module(m.group(2), m.group(1))
            module.update(status="failed", iterations=int(m.group(3)), cost_usd=float(m.group(4)))
            for branch in module["branches"].values():
                if branch["status"] == "active":
                    branch["status"] = "ended"
            return
        m = _MODULE_SKIPPED.match(first)
        if m:
            module = self._module(m.group(2), m.group(1))
            module.update(status="skipped", problem=m.group(3))
            return
        m = _OLD_CONVERGED.match(first)
        if m:
            module = run["modules"][-1] if run["modules"] else self._module("(module)")
            module.update(status="converged", winner=m.group(1))
            module["branches"][m.group(1)]["status"] = "converged"
            if m.group(3):
                run["spent_usd"] = float(m.group(3))
            run["outcome"], run["outcome_ok"] = f"Converged on Branch {m.group(1)}.", True
            return
        if first.startswith("Nothing needed rolling back"):
            run["outcome"] = "Stopped before it finished; nothing needed rolling back."
            run["outcome_ok"] = None
        elif first.startswith(_END_PREFIXES):
            run["outcome"] = message.strip()
            run["outcome_ok"] = level == "INFO"


def parse_runs(entries):
    """The runs the log records, oldest first. Each: kind (build, improve, self-improve, check, fatal, or run for an
    older log), target, limit_usd, spent_usd, started/ended times, modules (each with its branches), commits,
    episodes, outcome (the engine's own closing line) and outcome_ok."""
    builder = _RunBuilder()
    for entry in entries:
        builder.feed(entry)
    return builder.finish()


def collapse_notes(notes):
    """Repeated notes as one line each, with a count: ["x", "x", "y"] -> ["x (×2)", "y"]."""
    collapsed = []
    for note in notes:
        if collapsed and collapsed[-1][0] == note:
            collapsed[-1][1] += 1
        else:
            collapsed.append([note, 1])
    return [f"{text} (×{count})" if count > 1 else text for text, count in collapsed]


def search_runs(runs):
    """The runs that used the branch search (not health checks or failed starts), oldest first."""
    return [r for r in runs if r["kind"] not in ("check", "fatal")]


RUN_KIND_LABELS = {"build": "Build", "improve": "Improve a project", "self-improve": "Self-improve Foundry", "check": "Health check",
                   "fatal": "Failed start", "run": "Build (older log)"}


def describe_run(run):
    """One line naming a run, for a list: "Build · moviemaker · started 22:29:51"."""
    target = os.path.basename(os.path.normpath(run["target"])) if run["target"] else ""
    parts = [RUN_KIND_LABELS.get(run["kind"], run["kind"])]
    if target:
        parts.append(target)
    parts.append(f"started {run['started'][:8]}")
    return " · ".join(parts)
