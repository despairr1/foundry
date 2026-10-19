"""A terminal you can type in: a program running in a pseudo-terminal (conpty.py), drawn from a VT screen
(vt_screen.py) into a Tk Text widget. Keys go to the program; mouse selection, Ctrl+C (with a selection) and
Ctrl+V copy and paste; the wheel scrolls back through history."""
import queue
import tkinter as tk
import os
import tkinter.font as tkfont

import customtkinter as ctk

import theme
from conpty import PseudoTerminal
from vt_screen import Screen

KEYS = {
    "Return": "\r", "KP_Enter": "\r", "BackSpace": "\x7f", "Tab": "\t", "Escape": "\x1b",
    "Up": "\x1b[A", "Down": "\x1b[B", "Right": "\x1b[C", "Left": "\x1b[D",
    "Home": "\x1b[H", "End": "\x1b[F", "Insert": "\x1b[2~", "Delete": "\x1b[3~", "Prior": "\x1b[5~", "Next": "\x1b[6~",
    "F1": "\x1bOP", "F2": "\x1bOQ", "F3": "\x1bOR", "F4": "\x1bOS", "F5": "\x1b[15~", "F6": "\x1b[17~",
    "F7": "\x1b[18~", "F8": "\x1b[19~", "F9": "\x1b[20~", "F10": "\x1b[21~", "F11": "\x1b[23~", "F12": "\x1b[24~",
}
CTRL_ARROWS = {"Up": "\x1b[1;5A", "Down": "\x1b[1;5B", "Right": "\x1b[1;5C", "Left": "\x1b[1;5D"}
CTRL, SHIFT = 0x4, 0x1
RENDER_EVERY_MS = 30


class TerminalWidget(ctk.CTkFrame):
    def __init__(self, master, argv, cwd=None, env=None, on_exit=None, on_title=None):
        """on_exit(code, text) once the program has ended and all its output is shown; on_title(title) when it changes."""
        super().__init__(master, corner_radius=8, fg_color=theme.CODE_BG, border_width=1, border_color=theme.BORDER)
        self.on_exit, self.on_title = on_exit, on_title
        self.exited = False
        self.exit_code = None
        self._queue = queue.Queue()
        self._render_job = None
        self._resize_job = None
        self._hist_in_text = 0       # history lines currently in the Text widget
        self._hist_rendered = 0      # screen.history_added at the last render
        self._styles = {}            # style tuple -> tag name
        self._title = ""

        try:
            size = int(os.environ.get("TERMINAL_FONT_SIZE", "") or 10)
        except ValueError:
            size = 10
        self.font = tkfont.Font(family=os.environ.get("TERMINAL_FONT_FAMILY", "").strip() or theme.MONO_FAMILY, size=max(7, min(24, size)))
        self.char_w = max(1, self.font.measure("M"))
        self.line_h = max(1, self.font.metrics("linespace"))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.text = tk.Text(self, font=self.font, wrap="none", borderwidth=0, highlightthickness=0, padx=8, pady=6,
                            undo=False, cursor="xterm", insertwidth=0, exportselection=True)
        self.text.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=4)
        self.scrollbar = ctk.CTkScrollbar(self, command=self.text.yview, width=10)
        self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 2), pady=4)
        self.text.configure(yscrollcommand=self.scrollbar.set)
        self._apply_colors()

        self.text.bind("<Key>", self._on_key)
        self.text.bind("<<Paste>>", lambda e: (self.paste(), "break")[1])
        self.text.bind("<<Cut>>", lambda e: "break")
        self.text.bind("<Button-1>", lambda e: self.text.focus_set(), add="+")
        self.text.bind("<Button-3>", self._menu)
        self.text.bind("<FocusIn>", lambda e: self._schedule_render(force=True))
        self.text.bind("<FocusOut>", lambda e: self._schedule_render(force=True))
        self.text.bind("<Configure>", self._on_configure)

        cols, rows = 100, 30
        self.screen = Screen(cols, rows, respond=self.send)
        self.pty = PseudoTerminal(argv, cwd=cwd, cols=cols, rows=rows, env=env)
        try:
            self.pty.start(on_output=lambda text: self._queue.put(("out", text)),
                           on_exit=lambda code: self._queue.put(("exit", code)))
        except OSError as e:
            self.screen.feed(f"\x1b[91mCouldn't start {argv[0]}: {e}\x1b[0m\r\n")
            self._queue.put(("exit", -1))
        self._pump()

    # --------------------------------------------------------------- colors
    def _apply_colors(self):
        self.default_fg = theme.mode_color(theme.TEXT)
        self.default_bg = theme.mode_color(theme.CODE_BG)
        self.text.configure(bg=self.default_bg, fg=self.default_fg, selectbackground=theme.mode_color(theme.SELECTED),
                            selectforeground=self.default_fg, inactiveselectbackground=theme.mode_color(theme.SELECTED))
        self.text.tag_configure("cursor", background=self.default_fg, foreground=self.default_bg)
        self.text.tag_raise("sel")

    def _tag(self, style):
        tag = self._styles.get(style)
        if tag is None:
            fg, bg, bold, italic, underline, inverse = style
            fg, bg = fg or self.default_fg, bg or self.default_bg
            if inverse:
                fg, bg = bg, fg
            tag = self._styles[style] = f"s{len(self._styles)}"
            options = {"foreground": fg, "underline": underline}
            if bg != self.default_bg:
                options["background"] = bg
            if bold or italic:
                options["font"] = (self.font.actual("family"), self.font.actual("size"),
                                   " ".join(w for w, on in (("bold", bold), ("italic", italic)) if on))
            self.text.tag_configure(tag, **options)
            self.text.tag_raise("cursor")
            self.text.tag_raise("sel")
        return tag

    # --------------------------------------------------------------- output
    def _pump(self):
        if not self.winfo_exists():
            return
        exited = None
        try:
            while True:
                kind, value = self._queue.get_nowait()
                if kind == "out":
                    self.screen.feed(value)
                else:
                    exited = value
        except queue.Empty:
            pass
        if self.screen.dirty:
            self._schedule_render()
        if exited is not None and not self.exited:
            self._finish(exited)
        self.after(15, self._pump)

    def _finish(self, code):
        self.exited, self.exit_code = True, code
        self.screen.cursor_visible = False
        text = self.screen.text()
        if code not in (None, 0):
            self.screen.feed(f"\r\n\x1b[90m[exited with code {code}]\x1b[0m")
        self._render(force=True)
        if self.on_exit:
            self.on_exit(code, text)

    def _schedule_render(self, force=False):
        if force:
            self.screen.dirty = True
        if self._render_job is None:
            self._render_job = self.after(RENDER_EVERY_MS, self._render)

    def _render(self, force=False):
        self._render_job = None
        screen = self.screen
        if not (screen.dirty or force) or not self.winfo_exists():
            return
        screen.dirty = False
        if screen.title != self._title:
            self._title = screen.title
            if self.on_title:
                self.on_title(self._title)
        text = self.text
        follow = text.yview()[1] >= 0.999
        text.delete(f"{self._hist_in_text + 1}.0", "end")   # the live screen is redrawn; history stays
        new = min(screen.history_added - self._hist_rendered, len(screen.history))
        if new > 0:
            for line in list(screen.history)[-new:]:
                self._insert_line(line)
                text.insert("end", "\n")
            self._hist_in_text += new
        self._hist_rendered = screen.history_added
        excess = self._hist_in_text - len(screen.history)
        if excess > 0:  # the scrollback is full: drop the oldest lines
            text.delete("1.0", f"{excess + 1}.0")
            self._hist_in_text -= excess
        show_cursor = screen.cursor_visible and not self.exited and self.focus_get() is text
        for row, line in enumerate(screen.lines):
            cursor = screen.x if show_cursor and row == screen.y else None
            self._insert_line(line, cursor)
            if row < len(screen.lines) - 1:
                text.insert("end", "\n")
        if follow:
            text.see("end")

    def _insert_line(self, line, cursor=None):
        end = len(line)
        while end > 0 and line[end - 1][0] == " " and line[end - 1][1][1] is None and not line[end - 1][1][5]:
            end -= 1   # trailing blanks would only add width
        if cursor is not None:
            end = max(end, cursor + 1)
        start_index = self.text.index("end-1c")
        run_text, run_style = "", None
        for ch, style in line[:end]:
            if style != run_style and run_text:
                self.text.insert("end", run_text, self._tag(run_style))
                run_text = ""
            run_style = style
            run_text += ch
        if run_text:
            self.text.insert("end", run_text, self._tag(run_style))
        if cursor is not None:
            row = start_index.split(".")[0]
            self.text.tag_add("cursor", f"{row}.{cursor}", f"{row}.{cursor + 1}")

    # ---------------------------------------------------------------- input
    def send(self, text):
        if not self.exited:
            self.pty.write(text)

    def _on_key(self, event):
        keysym, state = event.keysym, event.state
        if state & CTRL and keysym.lower() == "c" and (state & SHIFT or self._selection()):
            self.copy()
            return "break"
        if state & CTRL and keysym.lower() == "v":
            self.paste()
            return "break"
        if self.exited:
            return "break"
        if keysym == "Tab" and state & SHIFT:
            self.send("\x1b[Z")
        elif state & CTRL and keysym in CTRL_ARROWS:
            self.send(CTRL_ARROWS[keysym])
        elif keysym in KEYS:
            self.send(KEYS[keysym])
        elif event.char:
            self.send(event.char)  # letters, and Ctrl+letter arrives as its control character
        if self.text.yview()[1] < 0.999:
            self.text.see("end")   # typing jumps back to the prompt
        return "break"

    def _selection(self):
        try:
            return self.text.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def copy(self):
        selected = self._selection()
        if selected:
            self.clipboard_clear()
            self.clipboard_append("\n".join(line.rstrip() for line in selected.split("\n")))

    def paste(self):
        try:
            data = self.clipboard_get()
        except tk.TclError:
            return
        self.send(data.replace("\r\n", "\r").replace("\n", "\r"))
        self.text.focus_set()

    def _menu(self, event):
        from shell import popup_menu
        popup_menu(self.text, [("Copy", self.copy), ("Paste", self.paste), None,
                               ("Clear scrollback", self.clear_scrollback)])

    def clear_scrollback(self):
        self.screen.history.clear()
        self.text.delete("1.0", f"{self._hist_in_text + 1}.0")
        self._hist_in_text = 0
        self._schedule_render(force=True)

    def focus(self):
        self.text.focus_set()

    def kill(self):
        self.pty.kill()

    # --------------------------------------------------------------- resize
    def _on_configure(self, _event):
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._fit)

    def _fit(self):
        self._resize_job = None
        width, height = self.text.winfo_width() - 16, self.text.winfo_height() - 12
        cols, rows = max(20, width // self.char_w), max(4, height // self.line_h)
        if (cols, rows) != (self.screen.cols, self.screen.rows):
            self.screen.resize(cols, rows)
            self.pty.resize(cols, rows)
            self._schedule_render(force=True)
