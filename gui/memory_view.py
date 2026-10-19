"""Memory: the engine's episodic memory, read from its SQLite file (FOUNDRY_SQLITE_PATH, default .foundry-memory.sqlite).

Each episode is one module that converged in a Build or an Improve round: what the run had to get past
(failure_pattern), the stack trace it saw, and the semantic patches that made it converge. The engine embeds the
pattern and, before designing or fixing something similar, retrieves the nearest episodes (src/memory/retriever.ts).
This page lists what is really stored; when the file does not exist yet, it says so."""
import os
from datetime import datetime
from tkinter import messagebox

import customtkinter as ctk

import theme
from memory_store import delete_all, describe_patch, filter_episodes, memory_path, read_episodes
from pane_kit import CHIP, PAD, clear, empty_state, mono_box, set_text


def _when(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%b %d, %Y %H:%M")
    except ValueError:
        return iso[:16]


class MemoryView(ctk.CTkFrame):
    """The Memory page (right pane › Engine internals › Memory)."""

    def __init__(self, master, workspace_root, config_manager=None, **kwargs):
        super().__init__(master, corner_radius=0, fg_color="transparent", **kwargs)
        self.workspace_root = workspace_root
        self.config_manager = config_manager
        self.episodes = []
        self.selected = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_header()
        self._build_search()
        self._build_list()
        self._build_detail()
        self.bind("<Configure>", self._on_resize, add="+")
        self.refresh_records()

    @property
    def sqlite_path(self):
        return memory_path(self.workspace_root, self.config_manager)

    # ---------------------------------------------------------------- layout
    def _build_header(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(2, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Memory", font=ctk.CTkFont(size=18, weight="bold"), text_color=theme.TEXT, anchor="w").grid(
            row=0, column=0, sticky="w")
        buttons = ctk.CTkFrame(head, fg_color="transparent")
        buttons.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(buttons, text="Refresh", width=70, height=28, font=ctk.CTkFont(size=12), command=self.refresh_records, **CHIP).pack(
            side="left", padx=(0, 6))
        self.delete_button = ctk.CTkButton(buttons, text="Delete all…", width=90, height=28, font=ctk.CTkFont(size=12),
                                           command=self._delete_all, **CHIP)
        self.delete_button.pack(side="left")
        self.subtitle = ctk.CTkLabel(
            head, text="What the engine remembers from every module that converged: the problem it had to get past and the "
                       "patches that worked. It looks these up before designing or fixing something similar.",
            font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED, anchor="w", justify="left")
        self.subtitle.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.path_label = ctk.CTkLabel(head, text="", font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11), text_color=theme.TEXT_FAINT,
                                       anchor="w", justify="left")
        self.path_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _build_search(self):
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(4, 6))
        row.grid_columnconfigure(0, weight=1)
        self.search_entry = ctk.CTkEntry(row, placeholder_text="Filter by module, language or problem…", height=30)
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda e: self._render_list())
        self.count_label = ctk.CTkLabel(row, text="", font=ctk.CTkFont(size=12), text_color=theme.TEXT_MUTED)
        self.count_label.grid(row=0, column=1)

    def _build_list(self):
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent", corner_radius=0)
        self.list_frame.grid(row=2, column=0, sticky="nsew", padx=PAD - 4)
        self.list_frame.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=2, minsize=120)

    def _build_detail(self):
        frame = ctk.CTkFrame(self, corner_radius=10, fg_color="transparent", border_width=1, border_color=theme.BORDER)
        frame.grid(row=3, column=0, sticky="nsew", padx=PAD, pady=(6, 8))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        self.detail_title = ctk.CTkLabel(frame, text="Episode", font=ctk.CTkFont(size=12, weight="bold"), text_color=theme.TEXT, anchor="w")
        self.detail_title.grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        self.detail = mono_box(frame, height=180)
        self.detail.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 10))
        set_text(self.detail, "Pick an episode above to see its patches.")

    def _on_resize(self, event):
        scaling = ctk.ScalingTracker.get_widget_scaling(self)
        width = max(200, int(event.width / scaling) - 2 * PAD - 8)
        self.subtitle.configure(wraplength=width)
        self.path_label.configure(wraplength=width)

    # ------------------------------------------------------------------ data
    def refresh_records(self):
        path = self.sqlite_path
        result = read_episodes(path)
        self.episodes = result["episodes"]
        mode = f" · journal {result['journal_mode']}" if result["journal_mode"] else ""
        self.path_label.configure(text=f"{path}{mode}" if result["exists"] else f"{path} (not created yet)")
        self.delete_button.configure(state="normal" if self.episodes else "disabled")
        self._error = result["error"]
        self._exists = result["exists"]
        self._render_list()

    def _render_list(self):
        clear(self.list_frame)
        if not self._exists:
            self.count_label.configure(text="")
            empty_state(self.list_frame, "No memory yet. The engine creates this file the first time a Build or an Improve round "
                                         "converges and stores one episode per converged module.", row=0, column=0, sticky="w", padx=8, pady=12)
            return
        if self._error:
            self.count_label.configure(text="")
            empty_state(self.list_frame, f"Couldn't read the memory file: {self._error}", row=0, column=0, sticky="w", padx=8, pady=12)
            return
        shown = filter_episodes(self.episodes, self.search_entry.get())
        total = len(self.episodes)
        self.count_label.configure(text=f"{len(shown)} of {total} episode{'s' if total != 1 else ''}" if shown != self.episodes
                                   else f"{total} episode{'s' if total != 1 else ''}")
        if not self.episodes:
            empty_state(self.list_frame, "The memory file exists but holds no episodes yet.", row=0, column=0, sticky="w", padx=8, pady=12)
            return
        if not shown:
            empty_state(self.list_frame, "No episode matches the filter.", row=0, column=0, sticky="w", padx=8, pady=12)
            return
        for index, episode in enumerate(shown):
            self._episode_row(index, episode)

    def _episode_row(self, index, episode):
        selected = self.selected is not None and self.selected["id"] == episode["id"]
        row = ctk.CTkFrame(self.list_frame, corner_radius=8, fg_color=theme.SELECTED if selected else theme.SURFACE)
        row.grid(row=index, column=0, sticky="ew", padx=4, pady=2)
        row.grid_columnconfigure(0, weight=1)
        patches = len(episode["patches"]) if episode["patches"] is not None else "?"
        title = ctk.CTkLabel(row, text=f"#{episode['id']}  {episode['module']}  ·  {episode['language']}", font=ctk.CTkFont(size=12, weight="bold"),
                             text_color=theme.TEXT, anchor="w")
        title.grid(row=0, column=0, sticky="w", padx=10, pady=(6, 0))
        when = ctk.CTkLabel(row, text=_when(episode["created_at"]), font=ctk.CTkFont(size=10), text_color=theme.TEXT_FAINT, anchor="e")
        when.grid(row=0, column=1, sticky="e", padx=10, pady=(6, 0))
        pattern = " ".join(episode["pattern"].split())
        detail = ctk.CTkLabel(row, text=f"{pattern[:110]}{'…' if len(pattern) > 110 else ''}  ·  {patches} patch{'es' if patches != 1 else ''}",
                              font=ctk.CTkFont(size=11), text_color=theme.TEXT_MUTED, anchor="w")
        detail.grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 6))
        for widget in (row, title, when, detail):
            widget.bind("<Button-1>", lambda e, ep=episode: self._show(ep))

    def _show(self, episode):
        self.selected = episode
        self._render_list()
        self.detail_title.configure(text=f"Episode #{episode['id']} · {episode['module']} ({episode['language']}) · {_when(episode['created_at'])}")
        parts = [f"What the run had to get past:\n{episode['pattern'].strip() or '(none recorded)'}"]
        if episode["stack"].strip():
            parts.append(f"Stack trace / diagnostics:\n{episode['stack'].strip()}")
        parts.append(f"Embedding: {episode['embedding_model']} · {episode['dims']} dimensions")
        if episode["patches"] is None:
            parts.append(f"Winning patches (could not be read as JSON):\n{episode['patches_raw']}")
        elif not episode["patches"]:
            parts.append("Winning patches: none recorded.")
        else:
            parts.append(f"Winning patches ({len(episode['patches'])}):")
            for number, patch in enumerate(episode["patches"], start=1):
                parts.append(f"--- patch {number} ---\n{describe_patch(patch)}")
        set_text(self.detail, "\n\n".join(parts))

    def _delete_all(self):
        if not self.episodes:
            return
        count = len(self.episodes)
        if not messagebox.askyesno("Delete all episodes?", f"Delete all {count} episode{'s' if count != 1 else ''} from {self.sqlite_path}?\n\n"
                                                           "The engine will no longer find earlier fixes for similar problems. This cannot be undone."):
            return
        try:
            removed = delete_all(self.sqlite_path)
        except Exception as e:
            messagebox.showerror("Foundry", f"Couldn't delete the episodes: {e}")
            return
        self.selected = None
        set_text(self.detail, f"Deleted {removed} episode{'s' if removed != 1 else ''}.")
        self.detail_title.configure(text="Episode")
        self.refresh_records()
