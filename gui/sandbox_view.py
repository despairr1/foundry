"""Tests: runs the test command of the folder on screen in the Terminal pane and reads the result from its output.

The command comes from the folder itself (package.json's test script, Cargo.toml, go.mod, pytest or unittest files)
and can be edited before it runs. It runs in a real terminal tab (the same way a command a model proposes in a chat
runs), so the full output is there; this page shows the pass/fail counts its runner printed, the tests that failed,
and the last lines. The Docker card reports what the docker command says, because the engine only isolates the
tests it generates during a Build when the sandbox image exists; otherwise they run directly on this machine."""
import os
import time

import customtkinter as ctk

import theme
from engine_run_view import format_elapsed
from pane_kit import CHIP, PRIMARY, PanePage, mono_box, run_in_background, set_text, text_line
from test_report import detect_test_command, docker_state, parse_test_output

OUTPUT_TAIL_LINES = 40


class SandboxView(PanePage):
    """The Tests page (right pane › Checks › Tests)."""

    def __init__(self, master, workspace_root, terminal=None, config_manager=None, cwd_provider=None, on_show_terminal=None, **kwargs):
        super().__init__(master, **kwargs)
        self.workspace_root = workspace_root
        self.terminal = terminal
        self.config_manager = config_manager
        self.cwd_provider = cwd_provider or (lambda: workspace_root)
        self.on_show_terminal = on_show_terminal
        self.folder = None
        self.detected = None
        self.running_since = None
        self.last = None            # the last run: {"folder", "command", "code", "result", "text", "seconds"}
        self._docker_job = None

        self.header("Tests", "Runs the folder's test command in the Terminal pane and shows what its output said: how many tests "
                             "passed and failed, which failed, and the last lines. Nothing here is estimated; a run that prints no "
                             "summary is shown as such.")
        self._build_folder_card()
        self._build_result_card()
        self._build_docker_card()
        self.refresh()
        self._check_docker()

    # ---------------------------------------------------------------- layout
    def _build_folder_card(self):
        body = self.card("Folder on screen")
        self.folder_label = self.wrap(text_line(body, "", size=11, mono=True, color=theme.TEXT_MUTED, row=0, column=0, sticky="w"), inset=40)
        self.detect_label = self.wrap(text_line(body, "", size=11, color=theme.TEXT_FAINT, row=1, column=0, sticky="w", pady=(2, 6)), inset=40)
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew")
        row.grid_columnconfigure(0, weight=1)
        self.command_entry = ctk.CTkEntry(row, height=32, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=12),
                                          placeholder_text="test command, e.g. npm test")
        self.command_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.command_entry.bind("<Return>", lambda e: self.run_tests())
        self.run_button = ctk.CTkButton(row, text="Run tests", width=100, height=32, font=ctk.CTkFont(size=12, weight="bold"),
                                        command=self.run_tests, **PRIMARY)
        self.run_button.grid(row=0, column=1)
        if self.on_show_terminal:
            ctk.CTkButton(row, text="Show terminal", width=104, height=32, font=ctk.CTkFont(size=12), command=self.on_show_terminal,
                          **CHIP).grid(row=0, column=2, padx=(6, 0))

    def _build_result_card(self):
        body = self.card("Last run")
        self.result_label = self.wrap(text_line(body, "Not run yet.", size=15, bold=True, color=theme.TEXT_MUTED, row=0, column=0, sticky="w"), inset=40)
        self.result_detail = self.wrap(text_line(body, "", size=11, color=theme.TEXT_FAINT, row=1, column=0, sticky="w", pady=(2, 0)), inset=40)
        self.failures_title = text_line(body, "Failed", size=11, bold=True, color=theme.ERROR)
        self.failures_box = mono_box(body, height=90, wrap="none")
        self.output_title = text_line(body, f"Output (last {OUTPUT_TAIL_LINES} lines; the whole run is in the Terminal pane)", size=11,
                                      color=theme.TEXT_FAINT)
        self.output_box = mono_box(body, height=170, wrap="none")

    def _build_docker_card(self):
        body = self.card("Sandbox for generated tests (Docker)")
        self.docker_label = self.wrap(text_line(body, "Checking the docker command…", size=12, row=0, column=0, sticky="w"), inset=40)
        self.docker_detail = self.wrap(text_line(body, "", size=11, color=theme.TEXT_FAINT, row=1, column=0, sticky="w", pady=(2, 0)), inset=40)
        ctk.CTkButton(body, text="Check again", width=96, height=26, font=ctk.CTkFont(size=12), command=self._check_docker, **CHIP).grid(
            row=2, column=0, sticky="w", pady=(8, 0))

    # --------------------------------------------------------------- folder
    def refresh(self):
        """Re-reads the folder on screen; re-detects the command when the folder changed."""
        folder = self.cwd_provider() or self.workspace_root
        if os.path.normcase(os.path.normpath(folder)) == os.path.normcase(os.path.normpath(self.folder or "")):
            return
        self.folder = folder
        self.folder_label.configure(text=folder)
        self.detected = detect_test_command(folder)
        self.command_entry.delete(0, "end")
        if self.detected:
            self.command_entry.insert(0, self.detected["command"])
            self.detect_label.configure(text=f"From {self.detected['source']} ({self.detected['framework']}). Edit the command if you want another.")
        else:
            self.detect_label.configure(text="No test command recognized here (no package.json test script, Cargo.toml, go.mod, "
                                             "pytest or unittest files). Type one to run it.")
        if self.last and os.path.normcase(os.path.normpath(self.last["folder"])) != os.path.normcase(os.path.normpath(folder)):
            self.result_detail.configure(text=f"Last run was in {self.last['folder']}.")

    # ------------------------------------------------------------------ run
    def run_tests(self):
        if self.running_since is not None:
            return
        command = self.command_entry.get().strip()
        if not command:
            self.command_entry.focus_set()
            return
        if self.terminal is None:
            self.result_label.configure(text="The Terminal pane isn't available in this window.", text_color=theme.ERROR)
            return
        folder = self.folder or self.workspace_root
        self.running_since = time.time()
        self.run_button.configure(state="disabled", text="Running…")
        self.result_label.configure(text=f"Running {command}…", text_color=theme.ACCENT)
        self.result_detail.configure(text=f"In {folder}. The output is in the Terminal pane as it comes.")
        self.failures_title.grid_forget()
        self.failures_box.grid_forget()
        self.output_title.grid_forget()
        self.output_box.grid_forget()
        self.terminal.run_ai_command(command, folder, on_done=lambda code, text, c=command, f=folder: self._done(c, f, code, text), who="Tests page")
        self._tick()

    def _tick(self):
        if self.running_since is None or not self.winfo_exists():
            return
        self.result_detail.configure(text=f"In {self.folder}. Running for {format_elapsed(time.time() - self.running_since)}; "
                                          f"the output is in the Terminal pane as it comes.")
        self.after(1000, self._tick)

    def _done(self, command, folder, code, text):
        seconds = time.time() - (self.running_since or time.time())
        self.running_since = None
        self.run_button.configure(state="normal", text="Run tests")
        result = parse_test_output(text)
        self.last = {"folder": folder, "command": command, "code": code, "result": result, "text": text, "seconds": seconds}
        self._render_result()

    def _render_result(self):
        last = self.last
        result = last["result"]
        code = last["code"]
        ok = code == 0
        if result["summary"]:
            headline = result["summary"]
            if result["failed"] and ok:
                headline += " (exit code 0)"
            color = theme.SUCCESS if ok and not result["failed"] else theme.ERROR
        else:
            headline = "Passed (exit code 0), but no test counts were recognized in the output." if ok else \
                f"Failed with exit code {code}; no test counts were recognized in the output."
            color = theme.SUCCESS if ok else theme.ERROR
        self.result_label.configure(text=headline, text_color=color)
        bits = [last["command"], f"exit code {code}" if code is not None else "could not start", f"{format_elapsed(last['seconds'])} wall clock"]
        if result["framework"]:
            bits.append(result["framework"])
        if result["duration"]:
            bits.append(f"runner time {result['duration']}")
        if result["files_passed"] is not None:
            unit = "packages" if result["framework"] == "go" else "files"
            bits.append(f"{result['files_passed']} {unit} passed" + (f", {result['files_failed']} failed" if result["files_failed"] else ""))
        bits.append(f"in {last['folder']}")
        self.result_detail.configure(text="  ·  ".join(bits))
        row = 2
        if result["failures"]:
            self.failures_title.configure(text=f"Failed ({len(result['failures'])}{'+' if len(result['failures']) >= 50 else ''})")
            self.failures_title.grid(row=row, column=0, sticky="w", pady=(10, 2))
            set_text(self.failures_box, "\n".join(result["failures"]))
            self.failures_box.grid(row=row + 1, column=0, sticky="ew")
            row += 2
        lines = [l for l in (last["text"] or "").splitlines() if l.strip()]
        self.output_title.grid(row=row, column=0, sticky="w", pady=(10, 2))
        set_text(self.output_box, "\n".join(lines[-OUTPUT_TAIL_LINES:]) if lines else "(the command printed nothing)", end=True)
        self.output_box.grid(row=row + 1, column=0, sticky="ew")

    # --------------------------------------------------------------- docker
    def _image_name(self):
        if self.config_manager is not None:
            return str(self.config_manager.get("FOUNDRY_SANDBOX_IMAGE", "") or "").strip() or "foundry-sandbox:latest"
        return os.environ.get("FOUNDRY_SANDBOX_IMAGE", "").strip() or "foundry-sandbox:latest"

    def _forced_local(self):
        value = self.config_manager.get("FOUNDRY_FORCE_LOCAL_SANDBOX", "false") if self.config_manager is not None else \
            os.environ.get("FOUNDRY_FORCE_LOCAL_SANDBOX", "false")
        return str(value).strip().lower() in ("true", "1", "yes", "on")

    def _check_docker(self):
        image = self._image_name()
        self.docker_label.configure(text="Checking the docker command…", text_color=theme.TEXT_MUTED)
        self.docker_detail.configure(text="")
        run_in_background(self, lambda: docker_state(image), lambda state: self._show_docker(state, image))

    def _show_docker(self, state, image):
        if isinstance(state, Exception):
            self.docker_label.configure(text="The docker check failed.", text_color=theme.TEXT_MUTED)
            self.docker_detail.configure(text=str(state)[:300])
            return
        forced = self._forced_local()
        if not state["installed"]:
            self.docker_label.configure(text="Docker is not installed on this machine.", text_color=theme.TEXT_MUTED)
            self.docker_detail.configure(text="Tests the engine generates during a Build run directly on this machine, without isolation. "
                                              "Install Docker Desktop and build the image to isolate them.")
            return
        if not state["running"]:
            self.docker_label.configure(text="Docker is installed, but its daemon is not running.", text_color=theme.WARNING)
            self.docker_detail.configure(text=f"{state['detail']}\nUntil it runs, generated tests run directly on this machine, without isolation.")
            return
        version = f"Docker {state['version']}" + (f" on {state['os']}" if state["os"] else "")
        if state["image_present"]:
            if forced:
                self.docker_label.configure(text=f"{version}: image {image} is built, but FOUNDRY_FORCE_LOCAL_SANDBOX is on.", text_color=theme.WARNING)
                self.docker_detail.configure(text="Generated tests run directly on this machine until that setting is off (Settings › Builds).")
            else:
                self.docker_label.configure(text=f"{version}: image {image} is built.", text_color=theme.SUCCESS)
                self.docker_detail.configure(text="Tests the engine generates during a Build run inside this image, with no network"
                                                  + self._limits_text() + ".")
        elif state["image_present"] is False:
            self.docker_label.configure(text=f"{version} is running, but the image {image} is not built.", text_color=theme.WARNING)
            self.docker_detail.configure(text="Generated tests run directly on this machine, without isolation. Build the image in the Foundry folder:\n"
                                              f"docker build -f Dockerfile.sandbox -t {image} .")
        else:
            self.docker_label.configure(text=f"{version} is running; the image check did not answer.", text_color=theme.TEXT_MUTED)
            self.docker_detail.configure(text="")

    def _limits_text(self):
        if self.config_manager is None:
            return ""
        memory = str(self.config_manager.get("DOCKER_MEMORY_MB", "") or "").strip()
        cpus = str(self.config_manager.get("DOCKER_CPUS", "") or "").strip()
        timeout = str(self.config_manager.get("FOUNDRY_SANDBOX_TIMEOUT_MS", "") or "").strip()
        parts = []
        if memory:
            parts.append(f"{memory} MB of memory")
        if cpus:
            parts.append(f"{cpus} CPUs")
        if timeout.isdigit():
            parts.append(f"a {int(timeout) // 1000} s timeout")
        return (", " + ", ".join(parts)) if parts else ""
