"""Window chrome modeled on the Claude desktop app: a left sidebar (Improve, + New project, the project list,
Settings at the bottom), a top bar whose right-hand icon buttons open tool pages in a right-side pane, and
the pane itself (resizable, expandable, closable)."""
import tkinter as tk

import customtkinter as ctk

import theme
from engine_run_view import load_journal
from project_list import is_same_folder, project_name, project_subtitle

SIDEBAR_WIDTH = 252
ICON_FONT_SIZE = 15


def _set_cursor(widget, cursor):
    try:
        tk.Frame.configure(widget, cursor=cursor)
    except tk.TclError:
        pass


def _clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def popup_menu(widget, items, x=None, y=None):
    """items: (label, command) pairs, None for a separator. Opens a themed Tk menu at the pointer (or x, y)."""
    menu = tk.Menu(widget, tearoff=0, bd=0, relief="flat",
                   bg=theme.mode_color(theme.SURFACE), fg=theme.mode_color(theme.TEXT),
                   activebackground=theme.mode_color(theme.SELECTED), activeforeground=theme.mode_color(theme.TEXT),
                   font=(theme.UI_FAMILY, 10))
    for item in items:
        if item is None:
            menu.add_separator()
        else:
            menu.add_command(label=f"  {item[0]}  ", command=item[1])
    if x is None:
        x, y = widget.winfo_pointerx(), widget.winfo_pointery()
    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


class Tooltip:
    """A small hover label, like the ones on Claude's icon buttons."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self):
        self._job = None
        if self._tip or not self.widget.winfo_exists():
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.text, bg=theme.mode_color(theme.SURFACE), fg=theme.mode_color(theme.TEXT),
                 font=(theme.UI_FAMILY, 9), padx=8, pady=4, bd=1, relief="solid",
                 highlightthickness=0).pack()
        tip.update_idletasks()
        top = self.widget.winfo_toplevel()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - tip.winfo_width() // 2
        x = max(top.winfo_rootx() + 4, min(x, top.winfo_rootx() + top.winfo_width() - tip.winfo_width() - 6))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _event=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


def icon_button(master, glyph, tip, command, size=30):
    btn = ctk.CTkButton(master, text=glyph, width=size, height=size, corner_radius=8,
                        font=ctk.CTkFont(size=ICON_FONT_SIZE), fg_color="transparent",
                        hover_color=theme.SURFACE_HOVER, text_color=theme.TEXT_MUTED, command=command)
    Tooltip(btn, tip)
    return btn


class _Row(ctk.CTkFrame):
    """A clickable sidebar row: a title, an optional faint second line, and a spot for a live spinner."""

    def __init__(self, master, title, subtitle=None, icon=None, on_click=None, on_menu=None):
        super().__init__(master, corner_radius=8, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        self._selected = False
        self._on_click = on_click
        self.icon = None
        if icon:
            self.icon = ctk.CTkLabel(self, text=icon, width=18, font=ctk.CTkFont(size=13), text_color=theme.TEXT_MUTED)
            self.icon.grid(row=0, column=0, padx=(10, 0), pady=6, sticky="w")
        self.title = ctk.CTkLabel(self, text=title, anchor="w", font=ctk.CTkFont(size=13), text_color=theme.TEXT_MUTED)
        self.title.grid(row=0, column=1, sticky="ew", padx=(8 if icon else 12, 6), pady=(6, 0 if subtitle else 6))
        self.subtitle = None
        if subtitle:
            self.subtitle = ctk.CTkLabel(self, text=subtitle, anchor="w", font=ctk.CTkFont(size=11),
                                         text_color=theme.TEXT_FAINT, height=16)
            self.subtitle.grid(row=1, column=1, sticky="ew", padx=(12, 6), pady=(0, 6))
        self.spinner = ctk.CTkLabel(self, text="", width=16, font=ctk.CTkFont(size=13), text_color=theme.ACCENT)
        self.spinner.grid(row=0, column=2, rowspan=2, padx=(0, 8))
        for widget in [self, self.title, self.spinner] + [w for w in (self.icon, self.subtitle) if w]:
            widget.bind("<Button-1>", lambda e: self._on_click and self._on_click(), add="+")
            widget.bind("<Enter>", lambda e: self._hover(True), add="+")
            widget.bind("<Leave>", lambda e: self._hover(False), add="+")
            if on_menu:
                widget.bind("<Button-3>", lambda e: on_menu(e), add="+")
        _set_cursor(self, "hand2")  # children without their own cursor inherit it

    def _hover(self, inside):
        if not self._selected:
            self.configure(fg_color=theme.SURFACE_HOVER if inside else "transparent")

    def set_selected(self, selected):
        self._selected = selected
        self.configure(fg_color=theme.SELECTED if selected else "transparent")
        self.title.configure(text_color=theme.TEXT if selected else theme.TEXT_MUTED)
        if self.icon:
            self.icon.configure(text_color=theme.TEXT if selected else theme.TEXT_MUTED)


class Sidebar(ctk.CTkFrame):
    """Improve, + New project, the list of projects, and Settings / Help / Search at the bottom."""

    def __init__(self, master, on_improve, on_new_project, on_open_project, on_forget_project, on_open_folder,
                 on_settings, on_help, on_search, on_collapse, on_new_prompt=None):
        super().__init__(master, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=theme.SIDEBAR)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)
        self.on_open_project = on_open_project
        self.on_forget_project = on_forget_project
        self.on_open_folder = on_open_folder
        self.on_new_project = on_new_project
        self._project_rows = []  # (path, row)
        self._selected = None
        self._running = set()

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=(12, 8))
        ctk.CTkLabel(top, text="✻", font=ctk.CTkFont(size=17), text_color=theme.ACCENT).pack(side="left")
        ctk.CTkLabel(top, text="Foundry", font=ctk.CTkFont(size=14, weight="bold"), text_color=theme.TEXT).pack(side="left", padx=(7, 0))
        icon_button(top, "◧", "Hide sidebar", on_collapse, size=28).pack(side="right")

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="ew", padx=8)
        nav.grid_columnconfigure(0, weight=1)
        self.improve_row = _Row(nav, "Improve", icon="↻", on_click=on_improve)
        self.improve_row.grid(row=0, column=0, sticky="ew", pady=1)
        Tooltip(self.improve_row.title, "Foundry learns, patches and updates itself")
        self.new_row = _Row(nav, "New project", icon="+", on_click=on_new_project)
        self.new_row.grid(row=1, column=0, sticky="ew", pady=1)
        self.prompt_row = _Row(nav, "New prompt", icon="✎", on_click=on_new_prompt or (lambda: None))
        self.prompt_row.grid(row=2, column=0, sticky="ew", pady=1)
        Tooltip(self.prompt_row.title, "The prompt studio: write and refine a prompt with the models; saved on the right")

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=2, column=0, sticky="ew", padx=(20, 10), pady=(18, 2))
        ctk.CTkLabel(header, text="Projects", font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT).pack(side="left")
        icon_button(header, "+", "New project", on_new_project, size=22).pack(side="right")

        self.empty_label = ctk.CTkLabel(self, text="No projects yet. Start one with\n+ New project.", justify="left",
                                        anchor="w", font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT)
        # The scrollbar stays invisible until hovered, as in Claude's sidebar
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0,
                                                 scrollbar_button_color=theme.SIDEBAR,
                                                 scrollbar_button_hover_color=theme.SURFACE_HOVER)
        self.list_frame.grid(row=4, column=0, sticky="nsew", padx=(4, 0))
        try:  # a slim scrollbar, so project rows line up with the rows above
            self.list_frame._scrollbar.configure(width=8)
        except Exception:
            pass
        self.list_frame.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=5, column=0, sticky="ew", padx=8, pady=(6, 10))
        bottom.grid_columnconfigure(0, weight=1)
        self.settings_row = _Row(bottom, "Settings", icon="⚙", on_click=on_settings)
        self.settings_row.grid(row=0, column=0, sticky="ew")
        icon_button(bottom, "?", "Help (F1)", on_help, size=28).grid(row=0, column=1, padx=(2, 0))
        icon_button(bottom, "⌕", "Search commands (Ctrl K)", on_search, size=28).grid(row=0, column=2)

    # ------------------------------------------------------------- projects
    def set_projects(self, items):
        for _, row in self._project_rows:
            row.destroy()
        self._project_rows = []
        if not items:
            self.empty_label.grid(row=3, column=0, sticky="ew", padx=20, pady=(4, 0))
        else:
            self.empty_label.grid_remove()
        for idx, item in enumerate(items):
            path = item["path"]
            row = _Row(self.list_frame, _clip(project_name(path), 28), subtitle=_clip(project_subtitle(path), 34),
                       on_click=lambda p=path: self.on_open_project(p),
                       on_menu=lambda e, p=path: self._project_menu(p))
            row.grid(row=idx, column=0, sticky="ew", padx=(4, 6), pady=1)
            Tooltip(row.title, path)
            self._project_rows.append((path, row))
        self._apply_selection()
        self.tick(0)

    def _project_menu(self, path):
        popup_menu(self, [("Open", lambda: self.on_open_project(path)),
                          ("Show folder", lambda: self.on_open_folder(path)),
                          None,
                          ("Remove from list", lambda: self.on_forget_project(path))])

    # ---------------------------------------------------- selection / status
    def select(self, kind, path=None):
        """kind: "improve", "new", "project" (with path), "prompt" / "new_prompt", "settings", or None."""
        self._selected = (kind, path)
        self._apply_selection()

    def _apply_selection(self):
        kind, path = self._selected or (None, None)
        self.improve_row.set_selected(kind == "improve")
        self.new_row.set_selected(kind == "new")
        self.prompt_row.set_selected(kind in ("prompt", "new_prompt"))
        self.settings_row.set_selected(kind == "settings")
        for row_path, row in self._project_rows:
            row.set_selected(kind == "project" and is_same_folder(row_path, path))

    def set_running(self, running):
        """running: list of ("improve", None) / ("project", path) that are working right now."""
        self._running = list(running)
        self.improve_row.spinner.configure(text="")
        for _, row in self._project_rows:
            row.spinner.configure(text="")
        self.tick(0)

    def tick(self, frame):
        """Advances the spinner on rows that are working (called by the window's animation timer)."""
        glyph = theme.SPINNER_FRAMES[frame % len(theme.SPINNER_FRAMES)]
        for kind, path in self._running:
            row = self.improve_row if kind == "improve" else self._row_for(path)
            if row:
                row.spinner.configure(text=glyph)

    def _row_for(self, path):
        return next((row for p, row in self._project_rows if is_same_folder(p, path)), None)


class TopBar(ctk.CTkFrame):
    """The page title on the left and the tool-page icon buttons on the right, like Claude's top bar."""

    def __init__(self, master, on_show_sidebar):
        super().__init__(master, height=46, corner_radius=0, fg_color=theme.BG)
        self.grid_columnconfigure(1, weight=1)
        self.sidebar_button = icon_button(self, "◧", "Show sidebar", on_show_sidebar, size=28)
        titles = ctk.CTkFrame(self, fg_color="transparent")
        titles.grid(row=0, column=1, sticky="w", padx=(16, 8), pady=(8, 6))
        self.title = ctk.CTkLabel(titles, text="", font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.TEXT)
        self.title.pack(side="left")
        self.subtitle = ctk.CTkLabel(titles, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_FAINT)
        self.subtitle.pack(side="left", padx=(10, 0))
        # 1x1 when empty: an empty CTkFrame is 200x200 by default, which made the top bar 267 px tall on pages without chips
        self.actions_frame = ctk.CTkFrame(titles, fg_color="transparent", width=1, height=1)  # e.g. the project's Launch button
        self.actions_frame.pack(side="left", padx=(14, 0))
        self.buttons_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.buttons_frame.grid(row=0, column=2, sticky="e", padx=(8, 12))
        self.buttons = {}

    def show_sidebar_button(self, visible):
        if visible:
            self.sidebar_button.grid(row=0, column=0, padx=(10, 0))
        else:
            self.sidebar_button.grid_remove()

    def set_title(self, title, subtitle="", actions=()):
        """actions: (text, tooltip, command, primary) chips shown after the title, like a session's header buttons."""
        self.title.configure(text=title)
        self.subtitle.configure(text=_clip(subtitle, 90))
        for child in self.actions_frame.winfo_children():
            child.destroy()
        for text, tip, command, primary in actions:
            btn = ctk.CTkButton(self.actions_frame, text=text, height=26, width=10, corner_radius=13, font=ctk.CTkFont(size=12),
                                fg_color=theme.ACCENT if primary else theme.SURFACE_HOVER,
                                hover_color=theme.ACCENT_HOVER if primary else theme.SELECTED,
                                text_color=theme.ACCENT_TEXT if primary else theme.TEXT, command=command)
            btn.pack(side="left", padx=(0, 6))
            if tip:
                Tooltip(btn, tip)

    def add_button(self, page_id, glyph, tip, command):
        btn = icon_button(self.buttons_frame, glyph, tip, command)
        btn.pack(side="left", padx=1)
        self.buttons[page_id] = btn
        return btn

    def set_active(self, page_id):
        for pid, btn in self.buttons.items():
            active = pid == page_id
            btn.configure(fg_color=theme.SELECTED if active else "transparent",
                          text_color=theme.TEXT if active else theme.TEXT_MUTED)


class PageGroup(ctk.CTkFrame):
    """Several related tool pages behind one top-bar button, switched with small tabs."""

    def __init__(self, master):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.tabs = ctk.CTkSegmentedButton(self, values=[], height=28, font=ctk.CTkFont(size=12), command=self.show)
        self.tabs.grid(row=0, column=0, sticky="w", padx=12, pady=(0, 8))
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages = {}
        self.current = None

    def add(self, name, widget):
        self.pages[name] = widget
        self.tabs.configure(values=list(self.pages))
        if self.current is None:
            self.show(name)
        return widget

    def show(self, name):
        if name not in self.pages:
            return
        for page_name, widget in self.pages.items():
            if page_name != name:
                widget.grid_forget()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        self.tabs.set(name)
        self.current = name


class SidePane(ctk.CTkFrame):
    """The right-hand pane that the top-bar buttons open: one page at a time, with expand and close."""

    def __init__(self, master, on_expand, on_close):
        super().__init__(master, corner_radius=0, fg_color=theme.BG)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=(16, 8), pady=(6, 6))
        header.grid_columnconfigure(0, weight=1)
        self.title = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=13, weight="bold"), text_color=theme.TEXT)
        self.title.grid(row=0, column=0, sticky="w")
        self.expand_button = icon_button(header, "⤢", "Expand", on_expand, size=26)
        self.expand_button.grid(row=0, column=1)
        icon_button(header, "✕", "Close", on_close, size=26).grid(row=0, column=2, padx=(2, 0))
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.pages = {}  # id -> (title, widget)
        self.current = None

    def add_page(self, page_id, title, widget):
        self.pages[page_id] = (title, widget)
        return widget

    def show(self, page_id):
        for pid, (_, widget) in self.pages.items():
            if pid != page_id:
                widget.grid_forget()
        title, widget = self.pages[page_id]
        widget.grid(row=0, column=0, sticky="nsew", padx=(4, 4), pady=(0, 4))
        self.title.configure(text=title)
        self.current = page_id

    def set_expanded(self, expanded):
        self.expand_button.configure(text="⤡" if expanded else "⤢")


class Sash(ctk.CTkFrame):
    """A thin draggable divider between the main page and the right pane."""

    def __init__(self, master, on_drag):
        super().__init__(master, width=7, corner_radius=0, fg_color="transparent")
        self.grid_propagate(False)
        self.line = ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=theme.BORDER)
        self.line.place(relx=0.5, rely=0, relheight=1, anchor="n")
        _set_cursor(self, "sb_h_double_arrow")
        for widget in (self, self.line):
            widget.bind("<B1-Motion>", lambda e: on_drag(e.x_root), add="+")
            widget.bind("<Enter>", lambda e: self.line.configure(fg_color=theme.TEXT_FAINT), add="+")
            widget.bind("<Leave>", lambda e: self.line.configure(fg_color=theme.BORDER), add="+")


def journal_totals(folder):
    """(entries, total spent, runs kept) for a folder's run history."""
    entries = load_journal(folder)
    total = sum(float(e.get("spentUsd", 0) or 0) for e in entries)
    kept = sum(1 for e in entries if e.get("outcome") == "done")
    return entries, total, kept
