import tkinter as tk
import customtkinter as ctk

class CommandPalette(ctk.CTkToplevel):
    """
    Fast Command Palette (Ctrl+K / Ctrl+P):
    - Search and trigger any action, view, or execution pipeline in Foundry instantly
    - Keyboard-first navigation (Up, Down, Enter, Escape)
    """

    def __init__(self, master, actions, **kwargs):
        super().__init__(master, **kwargs)
        self.actions = actions  # List of tuples: (label, category, callback)
        self.filtered_actions = list(actions)
        self.selected_index = 0

        self.title("Foundry Command Palette (Ctrl+K)")
        self.geometry("620x440")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._create_ui()

    def _create_ui(self):
        # Search Entry Bar
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        top_bar.grid_columnconfigure(0, weight=1)

        self.search_entry = ctk.CTkEntry(
            top_bar,
            placeholder_text="Type a command or search action (e.g. Build, Patch, Chat, AST, Git, Settings)...",
            height=42,
            font=ctk.CTkFont(size=14)
        )
        self.search_entry.grid(row=0, column=0, sticky="ew")
        self.search_entry.focus_set()
        self.search_entry.bind("<KeyRelease>", self._on_filter)
        self.search_entry.bind("<Return>", lambda e: self._execute_selected())
        self.search_entry.bind("<Up>", self._on_up)
        self.search_entry.bind("<Down>", self._on_down)
        self.search_entry.bind("<Escape>", lambda e: self.destroy())

        # Action list container
        self.scroll_list = ctk.CTkScrollableFrame(self, corner_radius=6, fg_color=("gray92", "gray17"))
        self.scroll_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 14))

        self._render_list()

    def _on_filter(self, event=None):
        query = self.search_entry.get().strip().lower()
        if not query:
            self.filtered_actions = list(self.actions)
        else:
            self.filtered_actions = [
                a for a in self.actions if query in a[0].lower() or query in a[1].lower()
            ]
        self.selected_index = 0
        self._render_list()

    def _render_list(self):
        for w in self.scroll_list.winfo_children():
            w.destroy()

        if not self.filtered_actions:
            ctk.CTkLabel(self.scroll_list, text="No matching commands found.", text_color="gray60").pack(pady=20)
            return

        for i, (label, cat, cb) in enumerate(self.filtered_actions):
            is_sel = (i == self.selected_index)
            btn = ctk.CTkButton(
                self.scroll_list,
                text=f"{label}  [{cat}]",
                anchor="w",
                height=34,
                font=ctk.CTkFont(size=12, weight="bold" if is_sel else "normal"),
                fg_color=["#3B8ED0", "#1F6AA5"] if is_sel else "transparent",
                text_color=("gray10", "gray85"),
                hover_color=["#3B8ED0", "#1F6AA5"],
                command=lambda fn=cb: self._run_and_close(fn)
            )
            btn.pack(fill="x", padx=4, pady=2)

    def _on_up(self, event):
        if self.selected_index > 0:
            self.selected_index -= 1
            self._render_list()
        return "break"

    def _on_down(self, event):
        if self.selected_index < len(self.filtered_actions) - 1:
            self.selected_index += 1
            self._render_list()
        return "break"

    def _execute_selected(self):
        if 0 <= self.selected_index < len(self.filtered_actions):
            cb = self.filtered_actions[self.selected_index][2]
            self._run_and_close(cb)

    def _run_and_close(self, callback):
        self.destroy()
        callback()
