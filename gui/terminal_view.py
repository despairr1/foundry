import os
import queue
import subprocess
import threading
import time
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import theme
from conpty import command_line_for, default_shell
from shell import Tooltip, icon_button
from terminal_widget import TerminalWidget

FOUNDRY_TAB = "foundry"
LOG_TAB = "log"


class TerminalView(ctk.CTkFrame):
    """The Terminal pane, like the terminal panel in Claude Code:
    - Foundry: what the engine (the AI) is running right now: Builds, Improve rounds, their raw output
    - shells: real interactive terminals (PowerShell on Windows) in the folder you're working on; open more with +
    - commands the models proposed in a project chat run in their own tab, once you click Run
    - Log: the engine's log file (.foundry.log)
    """

    def __init__(self, master, workspace_root, on_process_state_change=None, cwd_provider=None, **kwargs):
        super().__init__(master, corner_radius=0, fg_color=theme.BG, **kwargs)
        self.workspace_root = workspace_root
        self.on_process_state_change = on_process_state_change
        self.cwd_provider = cwd_provider or (lambda: workspace_root)

        self.active_process = None
        self.process_running = False  # set synchronously on the Tk thread; active_process is only set once Popen returns
        self.process_start_time = None
        self.process_label = ""
        self.log_queue = queue.Queue()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.tab_bar = ctk.CTkFrame(self, fg_color="transparent")
        self.tab_bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(0, 6))
        self.tab_strip = ctk.CTkFrame(self.tab_bar, fg_color="transparent")
        self.tab_strip.pack(side="left", fill="x", expand=True)
        icon_button(self.tab_bar, "+", "New terminal (in the folder you're working on)", lambda: self.new_shell(),
                    size=26).pack(side="right")
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        self._tabs = {}      # key -> {"title", "widget", "button", "closable"}
        self._order = []
        self.current = None
        self._shell_count = 0

        self._add_tab(FOUNDRY_TAB, "Foundry", self._build_foundry_tab(), closable=False,
                      tip="What Foundry's AI is running: Builds and Improve rounds")
        self._add_tab(LOG_TAB, "Log", self._build_log_tab(), closable=False, tip="Foundry's engine log (.foundry.log)")
        self.show_tab(FOUNDRY_TAB)
        self._start_log_queue_pump()
        self._start_foundry_log_tail()

    # ================================================================== tabs
    def _add_tab(self, key, title, widget, closable=True, tip=None):
        button = ctk.CTkButton(self.tab_strip, text=title, height=26, width=10, corner_radius=6, font=ctk.CTkFont(size=12),
                               fg_color="transparent", hover_color=theme.SURFACE_HOVER, text_color=theme.TEXT_MUTED,
                               command=lambda: self.show_tab(key))
        button.pack(side="left", padx=(0, 2))
        if tip:
            Tooltip(button, tip)
        if closable:
            button.bind("<Button-2>", lambda e: self.close_tab(key), add="+")   # middle-click closes, as in browsers
            button.bind("<Button-3>", lambda e: self._tab_menu(key), add="+")
        self._tabs[key] = {"title": title, "widget": widget, "button": button, "closable": closable}
        self._order.append(key)
        return key

    def _tab_menu(self, key):
        from shell import popup_menu
        popup_menu(self, [("Close", lambda: self.close_tab(key))])

    def show_tab(self, key):
        if key not in self._tabs:
            return
        for other, tab in self._tabs.items():
            selected = other == key
            tab["button"].configure(fg_color=theme.SELECTED if selected else "transparent",
                                    text_color=theme.TEXT if selected else theme.TEXT_MUTED)
            if not selected:
                tab["widget"].grid_forget()
        self._tabs[key]["widget"].grid(row=0, column=0, sticky="nsew")
        self.current = key
        if isinstance(self._tabs[key]["widget"], TerminalWidget):
            self.after_idle(self._tabs[key]["widget"].focus)
        elif key == LOG_TAB:
            self._reload_foundry_log()

    def _set_title(self, key, title):
        tab = self._tabs.get(key)
        if tab:
            tab["title"] = title
            closer = "  ✕" if tab["closable"] else ""
            tab["button"].configure(text=f"{title[:28]}{'…' if len(title) > 28 else ''}{closer}")

    def close_tab(self, key):
        tab = self._tabs.get(key)
        if not tab or not tab["closable"]:
            return
        widget = tab["widget"]
        if isinstance(widget, TerminalWidget) and not widget.exited:
            widget.kill()
        widget.destroy()
        tab["button"].destroy()
        del self._tabs[key]
        self._order.remove(key)
        if self.current == key:
            self.show_tab(self._order[-1] if self._order else FOUNDRY_TAB)

    def new_shell(self, cwd=None):
        """A real interactive terminal in the folder you're working on (or cwd)."""
        self._shell_count += 1
        key = f"shell-{self._shell_count}"
        folder = cwd or self.cwd_provider() or self.workspace_root
        name = os.path.basename(os.path.normpath(folder)) or folder
        widget = TerminalWidget(self.content, default_shell(), cwd=folder,
                                on_exit=lambda code, text, k=key: self._set_title(k, f"{name} (ended)"))
        self._add_tab(key, name, widget, tip=folder)
        self._set_title(key, name)
        self.show_tab(key)
        return key

    def run_ai_command(self, command, cwd, on_done=None, who="AI"):
        """Runs a command a model proposed (after the user clicked Run) in its own terminal tab.
        on_done(exit_code, output_text) when it has finished."""
        self._shell_count += 1
        key = f"ai-{self._shell_count}"
        short = " ".join(command.split())
        widget = TerminalWidget(self.content, command_line_for(command), cwd=cwd,
                                on_exit=lambda code, text: self._ai_done(key, short, code, text, on_done))
        self._add_tab(key, f"▶ {short}", widget, tip=f"{who} asked to run this in {cwd}")
        self._set_title(key, f"▶ {short}")
        self.show_tab(key)
        return key

    def run_launch(self, command, cwd, name):
        """The project's Launch button: runs its start command in its own tab, like a double-click would."""
        self._shell_count += 1
        key = f"launch-{self._shell_count}"
        widget = TerminalWidget(self.content, command_line_for(command), cwd=cwd,
                                on_exit=lambda code, text, k=key, n=name: self._set_title(k, f"{'✓' if code == 0 else '✕'} {n}"))
        self._add_tab(key, f"▶ {name}", widget, tip=f"Launched: {command}\nin {cwd}")
        self._set_title(key, f"▶ {name}")
        self.show_tab(key)
        return key

    def _ai_done(self, key, short, code, text, on_done):
        self._set_title(key, f"{'✓' if code == 0 else '✕'} {short}")
        if on_done:
            on_done(code, text)

    def shutdown(self):
        """Ends every terminal's program (the window is closing)."""
        for tab in self._tabs.values():
            if isinstance(tab["widget"], TerminalWidget) and not tab["widget"].exited:
                tab["widget"].kill()

    # ============================================================ Foundry tab
    def _build_foundry_tab(self):
        frame = ctk.CTkFrame(self.content, corner_radius=8, fg_color=theme.CODE_BG, border_width=1, border_color=theme.BORDER)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
        self.run_state_label = ctk.CTkLabel(bar, text="Idle. Builds and Improve rounds show their output here.",
                                            font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w")
        self.run_state_label.pack(side="left")
        flat = {"height": 22, "width": 10, "font": ctk.CTkFont(size=11), "fg_color": "transparent",
                "hover_color": theme.SURFACE_HOVER, "text_color": theme.TEXT_MUTED, "corner_radius": 6}
        ctk.CTkButton(bar, text="Copy", command=self._copy_terminal, **flat).pack(side="right")
        ctk.CTkButton(bar, text="Clear", command=self.clear_terminal, **flat).pack(side="right", padx=(0, 2))
        self.btn_kill = ctk.CTkButton(bar, text="■ Stop", state="disabled", command=self.stop_process, **flat)
        self.btn_kill.pack(side="right", padx=(0, 2))
        self.console = ctk.CTkTextbox(frame, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=12), wrap="word",
                                      corner_radius=0, border_width=0, fg_color=theme.CODE_BG, text_color=theme.TEXT)
        self.console.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 6))
        self.console.insert("end", f"Foundry — {self.workspace_root}\n")
        self.console.insert("end", "Builds and Improve rounds run here. Open a terminal of your own with + above.\n\n")
        return frame

    # ================================================================ Log tab
    def _build_log_tab(self):
        frame = ctk.CTkFrame(self.content, corner_radius=8, fg_color=theme.CODE_BG, border_width=1, border_color=theme.BORDER)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(frame, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 4))
        self.log_filter_var = ctk.StringVar(value="ALL")
        filters = ["ALL", "ECONOMICS", "CLAUDE", "GEMINI", "ASTRA", "GIT", "MEMORY", "LSP", "ERROR"]
        ctk.CTkOptionMenu(bar, values=filters, variable=self.log_filter_var, command=self._apply_log_filter, width=110,
                          height=24, font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 6))
        self.log_search_entry = ctk.CTkEntry(bar, placeholder_text="Search the log…", height=24, font=ctk.CTkFont(size=11))
        self.log_search_entry.pack(side="left", fill="x", expand=True)
        self.log_search_entry.bind("<KeyRelease>", lambda e: self._apply_log_filter())
        self.log_textbox = ctk.CTkTextbox(frame, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11), wrap="word",
                                          corner_radius=0, border_width=0, fg_color=theme.CODE_BG, text_color=theme.TEXT)
        self.log_textbox.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 6))
        return frame

    def _reload_foundry_log(self):
        log_file = os.path.join(self.workspace_root, ".foundry.log")
        self.log_textbox.delete("1.0", "end")
        if not os.path.exists(log_file):
            self.log_textbox.insert("end", "No .foundry.log yet. It appears after the first Build or Improve round.\n")
            return
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except Exception as e:
            self.log_textbox.insert("end", f"Couldn't read the log: {e}\n")
            return
        level = self.log_filter_var.get().upper()
        query = self.log_search_entry.get().strip().lower()
        shown = [line for line in lines[-400:]
                 if (level == "ALL" or level in line.upper()) and (not query or query in line.lower())]
        self.log_textbox.insert("end", "".join(shown))
        self.log_textbox.see("end")

    def _apply_log_filter(self, *_args):
        self._reload_foundry_log()

    def _start_foundry_log_tail(self):
        if self.current == LOG_TAB and self.winfo_ismapped():
            self._reload_foundry_log()
        self.after(2000, self._start_foundry_log_tail)

    # ====================================================== engine processes
    def run_command(self, cmd, label=None, env=None, on_line=None, on_exit=None):
        """Runs an engine command (a Build, an Improve round...) and streams its output to the Foundry tab.

        on_line(line) receives every output line and on_exit(return_code) the exit code (None if the
        process could not start); both run on the Tk thread. Engine progress lines (@@FOUNDRY ...) go
        only to on_line, not to the console. Returns False if another process is already running.
        """
        if self.process_running or self.active_process is not None:
            messagebox.showwarning("Foundry is busy", "Foundry is already running something. Wait for it, or stop it first.")
            return False
        if label is None:
            label = cmd if isinstance(cmd, str) else " ".join(cmd)

        self._line_callback = on_line
        self._exit_callback = on_exit
        self.process_running = True
        self.process_label = label
        self._set_running_state(True, label)
        if self.current in (FOUNDRY_TAB, LOG_TAB):   # show the AI at work, but never steal focus from a shell
            self.show_tab(FOUNDRY_TAB)
        self.console.insert("end", f"\n[{datetime.now().strftime('%H:%M:%S')}] ▶ {label}\n")
        self.console.see("end")

        def _worker():
            proc = None
            try:
                shell = isinstance(cmd, str)
                proc = subprocess.Popen(
                    cmd,
                    cwd=self.workspace_root,
                    stdin=subprocess.DEVNULL,  # no interactive input; don't let children block on / steal the console
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    # Node/npm/git emit UTF-8; the Windows locale codec (cp1252) raises on some bytes and
                    # would kill this reader, leaving the child blocked on a full pipe.
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    shell=shell,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                self.active_process = proc
                for line in iter(proc.stdout.readline, ""):
                    self.log_queue.put(line)
                proc.stdout.close()
                rc = proc.wait()
                self.log_queue.put(("__EXIT__", rc))
                self.log_queue.put(f"[{datetime.now().strftime('%H:%M:%S')}] {'✓ done' if rc == 0 else f'exited with code {rc}'}\n")
            except Exception as e:
                self.log_queue.put(("__EXIT__", None))
                self.log_queue.put(f"Couldn't run it: {e}\n")
                # Nobody is reading the pipe any more; don't leave an orphan blocked on it
                if proc is not None and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            finally:
                self.active_process = None
                self.log_queue.put("__PROCESS_COMPLETE__")

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def stop_process(self):
        """Kill the currently running engine process (and everything it started)."""
        proc = self.active_process  # local copy: the worker thread resets self.active_process when it exits
        if proc and proc.poll() is None:
            try:
                killed = False
                if os.name == "nt":
                    # terminate() only kills the direct child; grandchildren keep the stdout pipe open. Kill the tree.
                    res = subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                                         capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    killed = res.returncode == 0
                if not killed:
                    proc.terminate()
                self.console.insert("end", "■ Stopped.\n")
                self.console.see("end")
            except Exception as e:
                self.console.insert("end", f"Couldn't stop it: {e}\n")

    def _set_running_state(self, is_running, label=""):
        if is_running:
            self.process_start_time = time.time()
            self.btn_kill.configure(state="normal", text_color=theme.ERROR)
            self.run_state_label.configure(text=f"● Running: {label[:60]}", text_color=theme.ACCENT)
            self._set_title(FOUNDRY_TAB, "● Foundry")
        else:
            self.btn_kill.configure(state="disabled", text_color=theme.TEXT_MUTED)
            elapsed = f" after {time.time() - self.process_start_time:.0f}s" if self.process_start_time else ""
            self.run_state_label.configure(text=f"Idle. Last run finished{elapsed}.", text_color=theme.TEXT_FAINT)
            self._set_title(FOUNDRY_TAB, "Foundry")
        if self.on_process_state_change:
            self.on_process_state_change(is_running, label)

    def _safe_callback(self, callback, value):
        # A bug in a listener must not kill the pump, or the console stops updating for good.
        try:
            callback(value)
        except Exception as e:
            self.console.insert("end", f"\n[UI callback error: {e}]\n")

    def _start_log_queue_pump(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                if isinstance(msg, tuple) and msg[0] == "__EXIT__":
                    callback, self._exit_callback = getattr(self, "_exit_callback", None), None
                    if callback:
                        self._safe_callback(callback, msg[1])
                elif msg == "__PROCESS_COMPLETE__":
                    self.process_running = False
                    self._line_callback = None
                    self._set_running_state(False)
                else:
                    callback = getattr(self, "_line_callback", None)
                    if callback:
                        self._safe_callback(callback, msg)
                    if not msg.startswith("@@FOUNDRY "):
                        self.console.insert("end", msg)
                        self.console.see("end")
        except queue.Empty:
            pass
        self.after(40, self._start_log_queue_pump)

    def clear_terminal(self):
        self.console.delete("1.0", "end")

    def _copy_terminal(self):
        self.clipboard_clear()
        self.clipboard_append(self.console.get("1.0", "end"))


class EngineStatsView(ctk.CTkScrollableFrame):
    """Spend and process telemetry (moved here from the old Terminal pane's Economics and Telemetry tabs)."""

    def __init__(self, master, workspace_root, terminal, **kwargs):
        super().__init__(master, corner_radius=0, fg_color="transparent", **kwargs)
        self.workspace_root = workspace_root
        self.terminal = terminal
        self.grid_columnconfigure(0, weight=1)

        spend = self._card(0, "Spending (from the engine log)")
        self.lbl_econ_total = ctk.CTkLabel(spend, text="No spend logged yet", font=ctk.CTkFont(size=18, weight="bold"),
                                           text_color=theme.TEXT, anchor="w")
        self.lbl_econ_total.pack(anchor="w")
        self.econ_progress = ctk.CTkProgressBar(spend, height=6)
        self.econ_progress.pack(fill="x", pady=(6, 4))
        self.econ_progress.set(0)
        self.lbl_econ_detail = ctk.CTkLabel(spend, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT,
                                            anchor="w", justify="left")
        self.lbl_econ_detail.pack(anchor="w")

        proc = self._card(1, "Engine process")
        self.lbl_tele_state = ctk.CTkLabel(proc, text="● Idle", font=ctk.CTkFont(size=13, weight="bold"),
                                           text_color=theme.SUCCESS, anchor="w")
        self.lbl_tele_state.pack(anchor="w")
        self.lbl_tele_pid = ctk.CTkLabel(proc, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w")
        self.lbl_tele_pid.pack(anchor="w")
        self.lbl_tele_time = ctk.CTkLabel(proc, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w")
        self.lbl_tele_time.pack(anchor="w")

        box = self._card(2, "Sandbox")
        for line in ("Docker image: foundry-sandbox:latest (falls back to a local sandbox when Docker isn't there)",
                     "Memory limit: 1024 MB · timeout 120 s (Settings → Sandbox & Docker)"):
            ctk.CTkLabel(box, text=line, font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED, anchor="w",
                         justify="left", wraplength=420).pack(anchor="w")
        self._tick()

    def _card(self, row, title):
        card = ctk.CTkFrame(self, corner_radius=10, fg_color="transparent", border_width=1, border_color=theme.BORDER)
        card.grid(row=row, column=0, sticky="ew", padx=12, pady=(0, 10))
        ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.TEXT, anchor="w").pack(
            anchor="w", padx=14, pady=(10, 4))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=14, pady=(0, 12))
        return body

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.winfo_ismapped():
            term = self.terminal
            if term.process_running:
                pid = term.active_process.pid if term.active_process else "starting"
                self.lbl_tele_state.configure(text=f"● Running: {term.process_label[:50]}", text_color=theme.ACCENT)
                self.lbl_tele_pid.configure(text=f"Process id: {pid}")
                if term.process_start_time:
                    self.lbl_tele_time.configure(text=f"Running for {time.time() - term.process_start_time:.0f} s")
            else:
                self.lbl_tele_state.configure(text="● Idle", text_color=theme.SUCCESS)
                self.lbl_tele_pid.configure(text="")
            self._read_spend()
        self.after(1500, self._tick)

    def _read_spend(self):
        # e.g. "[INFO] [Economics] CLAUDE (claude-fable-5-1): +25in/240out ($0.0184) | Total: $0.0551/$5.00"
        log_file = os.path.join(self.workspace_root, ".foundry.log")
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-400:]
        except OSError:
            return
        for line in reversed(lines):
            if "Total: $" in line:
                try:
                    current, limit = line.split("Total: $")[1].split("/$")
                    limit = limit.split()[0]
                    self.lbl_econ_total.configure(text=f"${float(current):.4f} of ${float(limit):.2f}")
                    self.econ_progress.set(min(1.0, float(current) / float(limit)))
                    self.lbl_econ_detail.configure(text=f"Latest: {line.split('] ', 2)[-1].split(' | ')[0].strip()[:120]}")
                except (ValueError, IndexError):
                    pass
                break
