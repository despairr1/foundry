"""Small building blocks the right-pane tool pages share, in the Claude Code look (gui/theme.py): a page header,
bordered cards, read-only monospace boxes, empty states, labels that re-wrap to the pane's width, and a safe way to
do slow work off the Tk thread."""
import queue
import threading

import customtkinter as ctk

import theme

CHIP = {"fg_color": theme.SURFACE_HOVER, "text_color": theme.TEXT, "hover_color": theme.SELECTED}
PRIMARY = {"fg_color": theme.ACCENT, "hover_color": theme.ACCENT_HOVER, "text_color": theme.ACCENT_TEXT}
PAD = 12


class PanePage(ctk.CTkScrollableFrame):
    """A scrolling tool page: header, then cards. Subclasses build with header(), card(), ...; labels passed to
    wrap() follow the pane's width."""

    def __init__(self, master, **kwargs):
        super().__init__(master, corner_radius=0, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(0, weight=1)
        self._row = 0
        self._wrapped = []
        self._width = 420
        self.bind("<Configure>", self._on_resize, add="+")

    def next_row(self):
        row = self._row
        self._row += 1
        return row

    def wrap(self, label, inset=0):
        self._wrapped.append((label, inset))
        label.configure(wraplength=max(160, self._width - inset))
        return label

    def _on_resize(self, event):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = int(event.width / scaling) - 2 * PAD - 24
        if abs(width - self._width) < 8:
            return
        self._width = width
        for label, inset in list(self._wrapped):
            if label.winfo_exists():
                label.configure(wraplength=max(160, width - inset))
            else:
                self._wrapped.remove((label, inset))

    def header(self, title, subtitle):
        """Title and a wrapped subtitle at the top of the page; returns (title_label, subtitle_label)."""
        title_label = ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=18, weight="bold"), text_color=theme.TEXT, anchor="w")
        title_label.grid(row=self.next_row(), column=0, sticky="w", padx=PAD, pady=(2, 0))
        subtitle_label = self.wrap(ctk.CTkLabel(self, text=subtitle, font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED,
                                                anchor="w", justify="left"))
        subtitle_label.grid(row=self.next_row(), column=0, sticky="w", padx=PAD, pady=(2, 10))
        return title_label, subtitle_label

    def card(self, title=None, pady=(0, 10)):
        """A bordered card; returns its body frame (one column). The optional title sits above the body."""
        card = ctk.CTkFrame(self, corner_radius=10, fg_color="transparent", border_width=1, border_color=theme.BORDER)
        card.grid(row=self.next_row(), column=0, sticky="ew", padx=PAD, pady=pady)
        card.grid_columnconfigure(0, weight=1)
        if title:
            ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.TEXT, anchor="w").grid(
                row=0, column=0, sticky="w", padx=14, pady=(10, 2))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4 if title else 12, 12))
        body.grid_columnconfigure(0, weight=1)
        card.body = body
        return body


def clear(frame):
    for child in frame.winfo_children():
        child.destroy()


def text_line(master, text, size=12, color=None, mono=False, bold=False, **grid):
    """A left-aligned single label; grid it with the keyword arguments (row, column, ...)."""
    font = ctk.CTkFont(family=theme.MONO_FAMILY, size=size, weight="bold" if bold else "normal") if mono else \
        ctk.CTkFont(size=size, weight="bold" if bold else "normal")
    label = ctk.CTkLabel(master, text=text, font=font, text_color=color or theme.TEXT, anchor="w", justify="left")
    if grid:
        label.grid(**grid)
    return label


def empty_state(master, text, **grid):
    label = ctk.CTkLabel(master, text=text, font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT, anchor="w", justify="left")
    if grid:
        label.grid(**grid)
    return label


def mono_box(master, height=140, wrap="word"):
    """A read-only monospace box for output and records; fill it with set_text()."""
    box = ctk.CTkTextbox(master, height=height, font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11), wrap=wrap,
                         fg_color=theme.CODE_BG, text_color=theme.TEXT, corner_radius=8)
    box.configure(state="disabled")
    return box


def set_text(box, text, end=False):
    box.configure(state="normal")
    box.delete("1.0", "end")
    box.insert("end", text)
    box.configure(state="disabled")
    if end:
        box.see("end")


def append_text(box, text, end=True):
    box.configure(state="normal")
    box.insert("end", text)
    box.configure(state="disabled")
    if end:
        box.see("end")


def status_color(ok):
    return theme.SUCCESS if ok else theme.ERROR


def run_in_background(widget, work, on_done, poll_ms=80):
    """Runs work() on a thread and hands its result to on_done(result) on the Tk thread.

    The thread never touches Tk: it only puts the result (or the exception it raised) on a queue that the Tk thread
    polls with after(). If the widget is gone by then, the result is dropped."""
    results = queue.Queue()

    def worker():
        try:
            results.put((True, work()))
        except Exception as e:  # reported to on_done's caller as the exception object
            results.put((False, e))

    def poll():
        try:
            ok, value = results.get_nowait()
        except queue.Empty:
            if widget.winfo_exists():
                widget.after(poll_ms, poll)
            return
        if widget.winfo_exists():
            on_done(value if ok else value)

    threading.Thread(target=worker, daemon=True).start()
    widget.after(poll_ms, poll)
