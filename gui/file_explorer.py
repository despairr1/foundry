"""The Files pane: the whole computer, not just the project, with AI access control.

Roots: the project being worked on, Foundry's own folder, your home folder, and every drive. Folders open on
click; files open in the Editor. Right-click a folder to allow or block AI access to it (saved in
.foundry/access.json and honoured by the engine), show it in Explorer, open it as a project, or open a
terminal there. Blocked folders show ⊘."""
import os
import string
import sys
from tkinter import messagebox, simpledialog

import customtkinter as ctk

import theme
from access_rules import access_for, load_rules, own_rule, set_rule
from shell import Tooltip, icon_button, popup_menu

NOISY_DIRS = {".git", "node_modules", "__pycache__", ".idea", ".vscode", "$RECYCLE.BIN", "System Volume Information"}
MAX_ENTRIES = 400


def list_drives():
    if sys.platform != "win32":
        return ["/"]
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


def file_icon(name):
    lower = name.lower()
    if lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return "‹›"
    if lower.endswith(".py"):
        return "py"
    if lower.endswith((".json", ".toml", ".yaml", ".yml", ".ini", ".env")):
        return "{}"
    if lower.endswith((".md", ".txt", ".rst")):
        return "≡"
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg")):
        return "▣"
    if lower.endswith((".bat", ".cmd", ".ps1", ".sh", ".exe")):
        return "▶"
    return "·"


class FileExplorer(ctk.CTkFrame):
    """A lazy tree over the whole file system with AI-access marks and a right-click menu."""

    def __init__(self, master, workspace_root, on_file_select=None, foundry_root=None, on_open_project=None,
                 on_open_terminal=None, on_rules_changed=None, **kwargs):
        super().__init__(master, corner_radius=0, fg_color="transparent", **kwargs)
        self.workspace_root = workspace_root     # the project on screen (the first root)
        self.foundry_root = foundry_root or workspace_root
        self.on_file_select = on_file_select
        self.on_open_project = on_open_project
        self.on_open_terminal = on_open_terminal
        self.on_rules_changed = on_rules_changed
        self.expanded_folders = set()
        self.rules = load_rules(self.foundry_root)
        self._rows = 0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._create_header()
        self._create_search_bar()
        self.tree = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.tree.grid(row=2, column=0, sticky="nsew", padx=2, pady=2)
        self.tree.grid_columnconfigure(0, weight=1)
        self.expanded_folders.add(self._key(self.workspace_root))
        self.refresh_tree()

    # ------------------------------------------------------------------ chrome
    def _create_header(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="Files", font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.TEXT).grid(row=0, column=0, sticky="w")
        self.legend = ctk.CTkLabel(hdr, text="", font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT)
        self.legend.grid(row=0, column=1, sticky="e", padx=(0, 6))
        icon_button(hdr, "+", "New file in the project", self._create_new_file, size=24).grid(row=0, column=2)
        icon_button(hdr, "↻", "Refresh", self.refresh_tree, size=24).grid(row=0, column=3)

    def _create_search_bar(self):
        self.search_entry = ctk.CTkEntry(self, placeholder_text="Filter by name (in open folders)", height=26, font=ctk.CTkFont(size=11))
        self.search_entry.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.search_entry.bind("<KeyRelease>", lambda e: self.refresh_tree())

    @staticmethod
    def _key(path):
        return os.path.normcase(os.path.abspath(path))

    # ------------------------------------------------------------------ roots
    def set_project(self, folder):
        """The project on screen changed: it becomes the first root (open), everything else stays as it was."""
        if folder and self._key(folder) != self._key(self.workspace_root):
            self.workspace_root = folder
            self.expanded_folders.add(self._key(folder))
            self.refresh_tree()

    def roots(self):
        """[(label, path)]: the project, Foundry, home, and each drive."""
        items = []
        seen = set()

        def add(label, path):
            if path and os.path.isdir(path) and self._key(path) not in seen:
                seen.add(self._key(path))
                items.append((label, os.path.abspath(path)))

        add(f"Project · {os.path.basename(os.path.normpath(self.workspace_root)) or self.workspace_root}", self.workspace_root)
        add("Foundry", self.foundry_root)
        add("Home", os.path.expanduser("~"))
        for drive in list_drives():
            add(f"Drive {drive}" if sys.platform == "win32" else "This computer", drive)
        return items

    # ------------------------------------------------------------------- tree
    def refresh_tree(self):
        self.rules = load_rules(self.foundry_root)
        for widget in self.tree.winfo_children():
            widget.destroy()
        self._rows = 0
        query = self.search_entry.get().strip().lower() if hasattr(self, "search_entry") else ""
        blocked = sum(1 for r in self.rules if r["access"] == "block")
        self.legend.configure(text=f"⊘ {blocked} blocked" if blocked else "")
        for label, path in self.roots():
            self._row(path, label, level=0, is_dir=True, root=True)
            if self._key(path) in self.expanded_folders:
                self._populate(path, level=1, query=query)

    def _populate(self, folder, level, query):
        try:
            entries = sorted(os.listdir(folder), key=lambda n: (not os.path.isdir(os.path.join(folder, n)), n.lower()))
        except OSError:
            self._note("(can't read this folder)", level)
            return
        shown = 0
        for name in entries:
            if name in NOISY_DIRS:
                continue
            full = os.path.join(folder, name)
            is_dir = os.path.isdir(full)
            if query and not is_dir and query not in name.lower():
                continue
            if shown >= MAX_ENTRIES:
                self._note(f"… {len(entries) - shown} more (use the filter)", level)
                break
            shown += 1
            self._row(full, name, level, is_dir)
            if is_dir and self._key(full) in self.expanded_folders:
                self._populate(full, level + 1, query)

    def _note(self, text, level):
        ctk.CTkLabel(self.tree, text=text, font=ctk.CTkFont(size=11), text_color=theme.TEXT_FAINT, anchor="w").grid(
            row=self._rows, column=0, sticky="w", padx=(10 + 14 * level, 4), pady=1)
        self._rows += 1

    def _row(self, path, name, level, is_dir, root=False):
        access = access_for(path, self.rules) if is_dir else None
        own = own_rule(path, self.rules) if is_dir else None
        if is_dir:
            mark = "⊘ " if access == "block" else "✓ " if own and own["access"] == "allow" else ""
            glyph = "▾" if self._key(path) in self.expanded_folders else "▸"
            text = f"{glyph} {mark}{name}"
        else:
            text = f"{file_icon(name)}  {name}"
        color = theme.ERROR if access == "block" else theme.TEXT if (is_dir or root) else theme.TEXT_MUTED
        btn = ctk.CTkButton(self.tree, text=text, anchor="w", height=24 if is_dir else 22, corner_radius=6,
                            font=ctk.CTkFont(size=12, weight="bold" if root else "normal"), fg_color="transparent",
                            text_color=color, hover_color=theme.SURFACE_HOVER,
                            command=(lambda p=path: self._toggle_folder(p)) if is_dir else (lambda p=path: self._on_click_file(p)))
        btn.grid(row=self._rows, column=0, sticky="ew", padx=(4 + 14 * level, 4), pady=0)
        btn.bind("<Button-3>", lambda e, p=path, d=is_dir: self._menu(p, d), add="+")
        if root or access == "block":
            Tooltip(btn, f"{path}\nAI access blocked: Foundry's models won't read, change or run anything here" if access == "block" else path)
        self._rows += 1

    def _toggle_folder(self, folder):
        key = self._key(folder)
        if key in self.expanded_folders:
            self.expanded_folders.remove(key)
        else:
            self.expanded_folders.add(key)
        self.refresh_tree()

    # ------------------------------------------------------------------ menu
    def _menu(self, path, is_dir):
        items = []
        if is_dir:
            access = access_for(path, self.rules)
            own = own_rule(path, self.rules)
            if access == "block":
                items.append(("Allow AI access", lambda: self._set_access(path, "allow")))
            else:
                items.append(("Block AI access", lambda: self._set_access(path, "block")))
            if own:
                items.append(("Clear this folder's rule", lambda: self._set_access(path, None)))
            items.append(None)
            if self.on_open_project:
                items.append(("Open as a project", lambda: self.on_open_project(path)))
            if self.on_open_terminal:
                items.append(("Open a terminal here", lambda: self.on_open_terminal(path)))
            items.append(("New file here…", lambda: self._create_new_file(path)))
        else:
            items.append(("Open in the Editor", lambda: self._on_click_file(path)))
            items.append(("Copy path", lambda: (self.clipboard_clear(), self.clipboard_append(path))))
        items += [None, ("Show in Explorer", lambda: self._show(path))]
        popup_menu(self, items)

    def _set_access(self, path, access):
        if access == "block" and self._key(self.foundry_root) == self._key(path):
            messagebox.showinfo("Foundry", "Foundry's own folder can't be blocked: Improve needs it.")
            return
        self.rules = set_rule(self.foundry_root, path, access)
        self.refresh_tree()
        if self.on_rules_changed:
            self.on_rules_changed(self.rules)

    def _show(self, path):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
        except Exception as e:
            messagebox.showerror("Foundry", f"Couldn't open the folder: {e}")

    # ----------------------------------------------------------------- files
    def _on_click_file(self, full_path):
        if self.on_file_select:
            self.on_file_select(full_path)

    def _create_new_file(self, folder=None):
        folder = folder or self.workspace_root
        name = simpledialog.askstring("New file", f"File name, relative to\n{folder}", parent=self)
        if not name or not name.strip():
            return
        new_path = os.path.normpath(os.path.join(folder, name.strip()))
        if not self._key(new_path).startswith(self._key(folder)):
            messagebox.showerror("Foundry", "The file must be inside that folder.")
            return
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            if not os.path.exists(new_path):
                with open(new_path, "w", encoding="utf-8"):
                    pass
        except OSError as e:
            messagebox.showerror("Foundry", f"Couldn't create the file: {e}")
            return
        self.expanded_folders.add(self._key(folder))
        self.refresh_tree()
        self._on_click_file(new_path)
