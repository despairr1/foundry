"""Code map: the source files in the folder on screen and the names each one exports.

A regular-expression scan of top-level declarations (gui/code_map.py), run in the background and listed per file;
click a file to open it in the Editor. It is honest about what it is: not the engine's Tree-sitter parse and not a
call graph. The engine's own call graph (src/ast/call_graph.ts) is built inside the engine process for the models'
context and is not written anywhere this window can read."""
import os
import time

import customtkinter as ctk

import theme
from code_map import KIND_LABELS, filter_files, scan_folder
from pane_kit import CHIP, PanePage, clear, empty_state, run_in_background, text_line

MAX_FILES_SHOWN = 300
MAX_SYMBOLS_PER_FILE = 40


class ASTGraphView(PanePage):
    """The Code map page (right pane › Checks › Code map)."""

    def __init__(self, master, workspace_root, cwd_provider=None, on_open_file=None, **kwargs):
        super().__init__(master, **kwargs)
        self.workspace_root = workspace_root
        self.cwd_provider = cwd_provider or (lambda: workspace_root)
        self.on_open_file = on_open_file
        self.folder = None
        self.scan = None
        self.scanning = False
        self._scan_id = 0

        self.header("Code map", "The source files in the folder on screen and the names each one exports (functions, classes, types), "
                                "found by scanning top-level declarations. Not a parse and not a call graph: it says what each file "
                                "declares, not who calls it. Click a file to open it in the Editor.")
        self._build_controls()
        self.list_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.list_frame.grid(row=self.next_row(), column=0, sticky="ew", padx=12, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.refresh()

    def _build_controls(self):
        body = self.card()
        body.grid_columnconfigure(0, weight=1)
        self.folder_label = self.wrap(text_line(body, "", size=11, mono=True, color=theme.TEXT_MUTED, row=0, column=0, sticky="w"), inset=150)
        ctk.CTkButton(body, text="Rescan", width=76, height=26, font=ctk.CTkFont(size=12), command=lambda: self.refresh(force=True), **CHIP).grid(
            row=0, column=1, sticky="e", padx=(8, 0))
        self.filter_entry = ctk.CTkEntry(body, height=30, placeholder_text="Filter files and symbols…")
        self.filter_entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.filter_entry.bind("<KeyRelease>", lambda e: self._render())
        self.summary_label = self.wrap(text_line(body, "", size=11, color=theme.TEXT_FAINT, row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)), inset=40)

    # ------------------------------------------------------------------ scan
    def refresh(self, force=False):
        folder = self.cwd_provider() or self.workspace_root
        same = self.folder and os.path.normcase(os.path.normpath(folder)) == os.path.normcase(os.path.normpath(self.folder))
        if same and not force:
            return
        self.folder = folder
        self.folder_label.configure(text=folder)
        self.scanning = True
        self._scan_id += 1
        scan_id = self._scan_id
        self.summary_label.configure(text="Scanning…")
        clear(self.list_frame)
        run_in_background(self, lambda: scan_folder(folder), lambda scan: self._scanned(scan_id, scan))

    def _scanned(self, scan_id, scan):
        if scan_id != self._scan_id:
            return  # a newer scan replaced this one
        self.scanning = False
        if isinstance(scan, Exception):
            self.scan = None
            self.summary_label.configure(text=f"The scan failed: {str(scan)[:200]}")
            return
        self.scan = scan
        self._render()

    # ---------------------------------------------------------------- render
    def _render(self):
        clear(self.list_frame)
        scan = self.scan
        if scan is None:
            return
        files = scan["files"]
        if not files:
            self.summary_label.configure(text=f"No TypeScript, JavaScript, Python, Go or Rust files under {scan['root']}"
                                              " (node_modules, dist, build and hidden folders are skipped).")
            return
        shown = filter_files(files, self.filter_entry.get())
        with_symbols = [f for f in shown if f["symbols"]]
        without = [f for f in shown if not f["symbols"]]
        parts = [f"{scan['file_count']} file{'s' if scan['file_count'] != 1 else ''}",
                 f"{scan['symbol_count']} exported name{'s' if scan['symbol_count'] != 1 else ''}",
                 f"scanned {time.strftime('%H:%M:%S')} in {scan['seconds']:.2f} s"]
        if scan["truncated"]:
            parts.append("stopped at 2000 files")
        if shown is not files:
            parts.append(f"{len(shown)} match the filter")
        self.summary_label.configure(text="  ·  ".join(parts))
        row = 0
        for entry in with_symbols[:MAX_FILES_SHOWN]:
            self._file_block(row, entry)
            row += 1
        if len(with_symbols) > MAX_FILES_SHOWN:
            empty_state(self.list_frame, f"… and {len(with_symbols) - MAX_FILES_SHOWN} more files with exported names. Filter to narrow the list.",
                        row=row, column=0, sticky="w", pady=(6, 0))
            row += 1
        if without:
            names = ", ".join(f["path"] for f in without[:12]) + (f" and {len(without) - 12} more" if len(without) > 12 else "")
            self.wrap(empty_state(self.list_frame, f"{len(without)} file{'s' if len(without) != 1 else ''} with no exported names: {names}",
                                  row=row, column=0, sticky="w", pady=(10, 0)), inset=40)

    def _file_block(self, row, entry):
        block = ctk.CTkFrame(self.list_frame, corner_radius=8, fg_color=theme.SURFACE)
        block.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        block.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(block, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
        head.grid_columnconfigure(0, weight=1)
        path_label = text_line(head, entry["path"], size=12, mono=True, bold=True, row=0, column=0, sticky="w")
        text_line(head, f"{len(entry['symbols'])} exported · {entry['lines']} lines · {entry['language']}", size=10,
                  color=theme.TEXT_FAINT, row=0, column=1, sticky="e")
        if self.on_open_file:
            full = os.path.join(self.folder, entry["path"].replace("/", os.sep))
            for widget in (block, head, path_label):
                widget.bind("<Button-1>", lambda e, p=full: self.on_open_file(p))
                widget.configure(cursor="hand2")
        body = ctk.CTkFrame(block, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 6))
        body.grid_columnconfigure(1, weight=1)
        for index, symbol in enumerate(entry["symbols"][:MAX_SYMBOLS_PER_FILE]):
            text_line(body, KIND_LABELS.get(symbol["kind"], symbol["kind"]), size=11, mono=True, color=theme.TEXT_FAINT,
                      row=index, column=0, sticky="w", padx=(0, 8))
            text_line(body, symbol["name"], size=11, mono=True, row=index, column=1, sticky="w")
            text_line(body, f":{symbol['line']}", size=10, mono=True, color=theme.TEXT_FAINT, row=index, column=2, sticky="e")
        if len(entry["symbols"]) > MAX_SYMBOLS_PER_FILE:
            empty_state(body, f"… {len(entry['symbols']) - MAX_SYMBOLS_PER_FILE} more", row=MAX_SYMBOLS_PER_FILE, column=0, columnspan=3, sticky="w")
