"""Chat text you can select and copy, like Claude's messages: a read-only tk.Text that sizes itself to its content.

Drag to select, Ctrl+C / Ctrl+A, double-click a word, right-click for Copy. The page still scrolls with the mouse
wheel (the text never scrolls by itself: it is always as tall as its content)."""
import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk

import theme


class SelectableText(tk.Text):
    def __init__(self, master, text="", size=13, mono=False, color=None, bg=None, shrink=False, max_width_px=None,
                 on_copy_to_box=None):
        """shrink: as narrow as the text (chat bubbles), up to max_width_px; otherwise it fills its column."""
        self._scaling = ctk.ScalingTracker.get_widget_scaling(master)
        family = theme.MONO_FAMILY if mono else theme.UI_FAMILY
        self._font = tkfont.Font(family=family, size=-int(round(size * self._scaling)))
        self._bg_spec = bg if bg is not None else theme.BG
        self._fg_spec = color if color is not None else theme.TEXT
        super().__init__(master, wrap="word", font=self._font, height=1, width=1, borderwidth=0, highlightthickness=0,
                         padx=0, pady=0, cursor="xterm", insertwidth=0, takefocus=0, exportselection=True,
                         relief="flat", undo=False)
        self._shrink = shrink
        self._max_width_px = max_width_px
        self._on_copy_to_box = on_copy_to_box
        self._apply_colors()
        self.set_text(text)
        self.bind("<Configure>", lambda e: self._fit_height(), add="+")
        self.bind("<Control-a>", self._select_all, add="+")
        self.bind("<Control-A>", self._select_all, add="+")
        self.bind("<Button-3>", self._menu, add="+")
        self.bind("<FocusOut>", lambda e: None, add="+")

    # ------------------------------------------------------------------ look
    def _apply_colors(self):
        bg = theme.mode_color(self._bg_spec)
        if bg == "transparent":
            bg = theme.mode_color(theme.BG)
        self.configure(bg=bg, fg=theme.mode_color(self._fg_spec), selectbackground=theme.ACCENT,
                       selectforeground=theme.ACCENT_TEXT, inactiveselectbackground=theme.mode_color(theme.SELECTED))

    # ------------------------------------------------------------------ text
    def set_text(self, text):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", text)
        self.configure(state="disabled")
        if self._shrink:
            self._fit_width()
        self.after_idle(self._fit_height)

    def get_text(self):
        return self.get("1.0", "end-1c")

    def set_max_width(self, px):
        self._max_width_px = px
        if self._shrink:
            self._fit_width()

    def _fit_width(self):
        longest = max((self._font.measure(line) for line in self.get_text().splitlines() or [""]), default=0)
        limit = self._max_width_px * self._scaling if self._max_width_px else longest
        px = max(self._font.measure("0") * 2, min(longest + 4, limit))
        self.configure(width=max(1, int(px / max(1, self._font.measure("0"))) + 1))

    def _fit_height(self):
        if not self.winfo_exists():
            return
        try:
            lines = int((self.count("1.0", "end-1c", "displaylines") or (0,))[0]) + 1
        except tk.TclError:
            return
        if int(self.cget("height")) != lines:
            self.configure(height=lines)
        self.yview_moveto(0)

    # ------------------------------------------------------------------ copy
    def _select_all(self, _event=None):
        self.tag_add("sel", "1.0", "end-1c")
        return "break"

    def selected(self):
        try:
            return self.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _menu(self, event):
        from shell import popup_menu
        chosen = self.selected()
        items = []
        if chosen:
            items.append(("Copy selection", lambda: self._copy(chosen)))
        items.append(("Copy all", lambda: self._copy(self.get_text())))
        items.append(("Select all", self._select_all))
        if self._on_copy_to_box:
            items += [None, ("Copy to the box below", lambda: self._on_copy_to_box(chosen or self.get_text()))]
        popup_menu(self, items)
        return "break"
