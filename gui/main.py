import os
import sys
import webbrowser
from tkinter import messagebox, simpledialog
import customtkinter as ctk

try:
    import winsound
except ImportError:
    winsound = None

# Ensure gui directory is in Python path for sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_manager import ConfigManager
from terminal_view import EngineStatsView, TerminalView
from file_explorer import FileExplorer
from editor_view import EditorView
from memory_view import MemoryView
from mcts_view import MCTSView
from ouroboros_view import OuroborosView
from settings_view import SettingsView
from ast_graph_view import ASTGraphView
from git_timeline_view import GitTimelineView
from playground_view import PlaygroundView
from sandbox_view import SandboxView
from command_palette import CommandPalette
from help_view import HelpView
from self_improve_view import SelfImproveView
from projects_view import ProjectsView
from prompts_view import PromptListPanel, PromptsView, remove_prompt, rename_prompt
from overview_panel import OverviewPanel
from context_panel import ContextPanel
import errors
from engine_run_view import load_journal
from launcher import describe_launcher, find_launcher, launcher_request
from project_chat import chat_spend, load_chat
from project_list import forget_project, is_same_folder, load_projects, project_name, remember_project
from prompt_store import STUDIO_MODES
from shell import PageGroup, Sash, Sidebar, SidePane, TopBar, popup_menu
import theme

# The old Build/Patch tabs became Improve; old links to them land there. Chat moved into the project page.
# The old Diagnostics tab (sample data) became the Diagnostics card on Improve.
VIEW_ALIASES = {"build": "improve", "patch": "improve", "start": "projects", "chat": "projects", "lsp": "improve"}

# Pages in the main (middle) area. Everything else opens in the right pane.
CENTER_PAGES = ("improve", "projects", "prompts", "settings", "help")

# Top-right buttons, left to right: (pane page, glyph, tooltip, pane title, [(tab, old view id)])
PANE_PAGES = [
    ("overview", "◨", "Overview", "Overview", []),
    ("context", "◔", "Context: what the models see on this page", "Context", []),
    ("saved", "✧", "Prompts: your saved prompt conversations", "Prompts", []),
    ("terminal", "›_", "Terminal: your shells, and what Foundry's AI runs", "Terminal", [("Terminal", "terminal")]),
    ("files", "✎", "Files: the whole computer, and AI access per folder", "Files", [("Files", "explorer"), ("Editor", "editor")]),
    ("changes", "±", "Changes (Git timeline)", "Changes", [("Timeline", "git")]),
    ("checks", "✓", "Tests and code map", "Checks", [("Tests", "sandbox"), ("Code map", "ast")]),
    ("models", "✦", "Arena: compare the models side by side", "Arena", [("Arena", "playground")]),
    ("engine", "◎", "Engine internals: search tree, memory, Ouroboros", "Engine internals",
     [("Search tree", "mcts"), ("Memory", "memory"), ("Ouroboros", "ouroboros"), ("Spend", "stats")]),
]
# Old view id -> (pane page, tab)
PANE_ROUTES = {view: (page, tab) for page, _, _, _, tabs in PANE_PAGES for tab, view in tabs}

MIN_CENTER_WIDTH = 420
MIN_PANE_WIDTH = 280


def _bool(value, default=True):
    text = str(value if value is not None else "").strip().lower()
    return default if not text else text in ("true", "1", "yes", "on")


class FoundryIDE(ctk.CTk):
    """
    Foundry Autonomous Software Engineering Engine — laid out like the Claude desktop app:
    - Left sidebar: Improve (Foundry improves itself), + New project, + New prompt, the project list, Settings at the bottom
    - Middle: the page you're on (Improve, a project, a prompt conversation, Settings or Help) with its live activity feed
    - Right: a pane the top-right buttons open — Overview, Context, Prompts, Terminal, Files, Changes, Checks, Arena, Engine internals
    - Command Palette (Ctrl+K / Ctrl+P)
    """

    def __init__(self):
        # Workspace Root Directory
        self.workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.config_manager = ConfigManager(self.workspace_root)

        # Claude Code look; the theme must be in place before the root window is created
        mode = self.config_manager.get("UI_THEME_MODE", "Dark")
        theme.apply_theme(mode if mode in ("Dark", "Light", "System") else "Dark")
        super().__init__()
        theme.init_fonts()

        self.title("Foundry")
        self.geometry("1620x940")
        self.minsize(1100, 680)

        self.grid_columnconfigure(0, weight=0)  # sidebar
        self.grid_columnconfigure(1, weight=1)  # main region
        self.grid_rowconfigure(0, weight=1)

        self.sidebar_visible = True
        self.current_view = None      # the middle page: improve / projects / prompts / settings / help
        self.selection = ("improve", None)
        self.pane_page = None         # the open right-pane page, or None
        self.pane_expanded = False
        self.pane_widths = {"overview": 300, "context": 320, "saved": 320}
        self._spin_frame = 0
        self._spin_job = None
        self._live = {}               # run view -> (spent, limit) while it runs
        self.project_views = {}       # folder key -> its page
        self.new_view = None          # the page behind "+ New project", until its first message or Build
        self.projects_view = None     # the project page on screen (or last on screen)
        self._recent_views = []
        self.prompt_views = {}        # prompt conversation folder key -> its page
        self.new_prompt_view = None   # the page behind "+ New prompt", until its first message
        self.prompts_view = None      # the prompt page on screen (or last on screen)

        self._create_sidebar()
        self._create_main_region()
        self._create_pane_pages()
        self._create_center_pages()
        self._bind_hotkeys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._load_project_list()
        self._open_start_page()
        errors.install(self, self.workspace_root, on_error=self._show_error)

    # ----------------------------------------------------------------- errors
    def _show_error(self, text):
        """A Tk callback raised: the traceback is in .foundry/gui-errors.log; say so instead of failing silently."""
        if not hasattr(self, "error_bar"):
            self.error_bar = ctk.CTkFrame(self.main, corner_radius=8, fg_color=theme.SURFACE, border_width=1, border_color=theme.ERROR)
            self.error_bar.grid_columnconfigure(0, weight=1)
            self.error_label = ctk.CTkLabel(self.error_bar, text="", anchor="w", justify="left", font=ctk.CTkFont(size=12),
                                            text_color=theme.ERROR, wraplength=900)
            self.error_label.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
            ctk.CTkButton(self.error_bar, text="Dismiss", width=70, height=26, font=ctk.CTkFont(size=12), fg_color=theme.SURFACE_HOVER,
                          hover_color=theme.SELECTED, text_color=theme.TEXT, command=self.error_bar.grid_remove).grid(row=0, column=1, padx=(0, 10))
        self.error_label.configure(text=f"Something went wrong in the window: {text}   (details in {errors.LOG_NAME})")
        self.error_bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))

    def _open_start_page(self):
        start = str(self.config_manager.get("UI_START_PAGE", "improve") or "improve")
        if start == "new":
            self.new_project()
        elif start == "last":
            last = self.config_manager.get("UI_LAST_PROJECT", "")
            items = load_projects(self.workspace_root)
            folder = last if last and os.path.isdir(last) else items[0]["path"] if items else ""
            if folder and os.path.isdir(folder):
                self.open_project(folder)
            else:
                self.switch_view("improve")
        else:
            self.switch_view("improve")
        if _bool(self.config_manager.get("UI_OPEN_OVERVIEW", "true")):
            self.open_pane("overview")

    def _on_close(self):
        busy = self.improve_view.running or any(v.running for v in self._all_project_views())
        if busy and not messagebox.askyesno("Quit Foundry?", "Foundry is still working. Quit anyway? "
                                                             "Unfinished self-improvement changes are rolled back on the next run."):
            return
        # Child processes are not killed with the parent on Windows; don't leave an engine run orphaned
        try:
            self.terminal.stop_process()
            self.terminal.shutdown()
        except Exception:
            pass
        for view in self._all_chat_views():
            if view.chat:
                view.chat.stop()
        self.destroy()

    # =========================================================================
    # LEFT SIDEBAR
    # =========================================================================
    def _create_sidebar(self):
        self.sidebar = Sidebar(
            self,
            on_improve=lambda: self.switch_view("improve"),
            on_new_project=self.new_project,
            on_new_prompt=self.new_prompt,
            on_open_project=self.open_project,
            on_forget_project=self._forget_project,
            on_open_folder=self._show_folder,
            on_settings=lambda: self.switch_view("settings"),
            on_help=lambda: self.switch_view("help"),
            on_search=self.open_command_palette,
            on_collapse=self.toggle_sidebar,
        )
        self.sidebar.grid(row=0, column=0, sticky="nsew")

    def toggle_sidebar(self):
        self.sidebar_visible = not self.sidebar_visible
        if self.sidebar_visible:
            self.sidebar.grid(row=0, column=0, sticky="nsew")
        else:
            self.sidebar.grid_remove()
        self.topbar.show_sidebar_button(not self.sidebar_visible)
        self.after_idle(self._fit_pane)

    def _load_project_list(self):
        items = load_projects(self.workspace_root)
        last = self.config_manager.get("UI_LAST_PROJECT", "")
        if not items and last and os.path.isdir(last):  # carry over the folder used before the list existed
            items = remember_project(self.workspace_root, last)
        self.sidebar.set_projects(items)

    # Each project has its own page, like each Claude session: its conversation, its live Build and its
    # message box stay as they are while you look at another project. Pages are made when first opened.
    @staticmethod
    def _key(folder):
        return os.path.normcase(os.path.abspath(folder))

    def _all_project_views(self):
        views = list(self.project_views.values())
        if self.new_view is not None and self.new_view not in views:
            views.append(self.new_view)
        return views

    def _all_prompt_views(self):
        views = list(self.prompt_views.values())
        if self.new_prompt_view is not None and self.new_prompt_view not in views:
            views.append(self.new_prompt_view)
        return views

    def _all_chat_views(self):
        return self._all_project_views() + self._all_prompt_views()

    def _make_project_view(self, folder=None, new=None):
        cell = []  # the callbacks need the view, which exists only once the constructor returns
        view = ProjectsView(self.center, workspace_root=self.workspace_root, config_manager=self.config_manager,
                            terminal=self.terminal, folder=folder, new=new,
                            on_project_used=lambda f: cell and self._on_project_used(cell[0], f),
                            on_open_settings=lambda: self.switch_view("settings"),
                            on_show_details=self._show_details,
                            on_run_command=self._run_ai_command,
                            on_use_prompt=self._use_prompt,
                            on_status=lambda **kw: cell and self._on_run_status(cell[0], **kw))
        cell.append(view)
        return view

    def new_project(self):
        view = self.new_view
        if view is None or view.running or view.chat or view.has_history(view.current_folder()):
            view = self.new_view = self._make_project_view(new=True)
        # else: the new-project page that hasn't started yet comes back as it was, draft included
        self._show_project(view, ("new", None))
        self.after_idle(view.task_box.focus_set)

    def open_project(self, folder):
        key = self._key(folder)
        view = self.project_views.get(key)
        if view is None:
            view = self.project_views[key] = self._make_project_view(folder, new=False)
        self._show_project(view, ("project", folder))

    def _show_project(self, view, selection):
        self.projects_view = view
        self.selection = selection
        if view in self._recent_views:
            self._recent_views.remove(view)
        self._recent_views.append(view)
        self._trim_project_views()
        self.switch_view("projects")

    def _trim_project_views(self, keep=12):
        """Closes the pages of projects not looked at for a while (their files and history stay on disk)."""
        for view in list(self._recent_views[:-keep]):
            if view is self.projects_view or view.running or view.chat:
                continue
            self._recent_views.remove(view)
            for key, other in list(self.project_views.items()):
                if other is view:
                    del self.project_views[key]
            if view is self.new_view:
                self.new_view = None
            view.destroy()

    def _current_project_view(self):
        """The project page hand-offs (Editor, prompts) go to; a new project if none is open."""
        if self.projects_view is None:
            self.new_project()
        return self.projects_view

    def _on_project_used(self, view, folder):
        """A project's first message or Build: it gets its row in the sidebar and keeps this page."""
        key = self._key(folder)
        for other_key, other in list(self.project_views.items()):
            if other is view and other_key != key:  # a new project picked another folder before it started
                del self.project_views[other_key]
        previous = self.project_views.get(key)
        if previous is not None and previous is not view and not previous.running and not previous.chat:
            if previous in self._recent_views:
                self._recent_views.remove(previous)
            previous.destroy()  # the same folder had an older page open; this one replaces it
        self.project_views[key] = view
        if view is self.new_view:
            self.new_view = None
        self.sidebar.set_projects(remember_project(self.workspace_root, folder))
        if view is self.projects_view:
            self.selection = ("project", folder)
        self._sync_chrome()

    def _forget_project(self, folder):
        view = self.project_views.get(self._key(folder))
        if view is not None and (view.running or view.chat):
            messagebox.showinfo("Foundry", f"Foundry is still working on “{project_name(folder)}”. Stop it first.")
            return
        if not messagebox.askyesno("Remove from list?", f"Remove “{project_name(folder)}” from the project list?\n\n"
                                                        "The folder and its files are not touched."):
            return
        self.sidebar.set_projects(forget_project(self.workspace_root, folder))
        if view is not None:
            del self.project_views[self._key(folder)]
            if view in self._recent_views:
                self._recent_views.remove(view)
            if view is self.projects_view:
                self.projects_view = None
                self.switch_view("improve")
            view.destroy()

    def _show_folder(self, folder):
        self.improve_view.open_folder(folder)

    # ------------------------------------------------------------- prompts
    # The prompt studio: each conversation is its own page too, listed in the Prompts pane on the right.
    def _make_prompt_view(self, folder=None):
        cell = []
        view = PromptsView(self.center, workspace_root=self.workspace_root, config_manager=self.config_manager,
                           terminal=self.terminal, folder=folder,
                           on_prompt_used=lambda f: cell and self._on_prompt_used(cell[0], f),
                           on_use_prompt=self._use_prompt, on_start_job=self._start_job, on_send_step=self._send_step,
                           on_open_settings=lambda: self.switch_view("settings"),
                           on_show_details=self._show_details,
                           on_status=lambda **kw: cell and self._on_run_status(cell[0], **kw))
        cell.append(view)
        return view

    def new_prompt(self):
        view = self.new_prompt_view
        if view is None or view.chat or view.has_history(view.current_folder()):
            view = self.new_prompt_view = self._make_prompt_view()
        self._show_prompt(view, ("new_prompt", None))
        self.after_idle(view.task_box.focus_set)

    def open_prompt(self, folder):
        key = self._key(folder)
        view = self.prompt_views.get(key)
        if view is None:
            view = self.prompt_views[key] = self._make_prompt_view(folder)
        self._show_prompt(view, ("prompt", folder))

    def _show_prompt(self, view, selection):
        self.prompts_view = view
        self.selection = selection
        self.switch_view("prompts")

    def _on_prompt_used(self, view, folder):
        """A conversation's first message: it is saved, named, and listed in the Prompts pane."""
        self.prompt_views[self._key(folder)] = view
        if view is self.new_prompt_view:
            self.new_prompt_view = None
        if view is self.prompts_view:
            self.selection = ("prompt", folder)
        self._sync_chrome()

    def _use_prompt(self, text):
        """A prompt from the studio becomes the first message of a new project."""
        self.new_project()
        self.projects_view.set_task(text)

    def _send_step(self, step):
        """One step of a job plan goes into a project's message box, with its Thinking level and limit set."""
        view = self._current_project_view()
        if view.running or view.chat:
            messagebox.showinfo("Foundry", "That project is busy. Wait for it, or open another project first.")
            return
        view._apply_step_settings(step)
        view.set_task(step["build"])
        self.switch_view("projects")

    def _start_job(self, plan):
        """Runs a job plan's steps as Builds, one after another, in a new project or one from the list."""
        items = load_projects(self.workspace_root)
        if not items:
            return self._run_job_in(plan, None)
        entries = [("New project", lambda: self._run_job_in(plan, None)), None]
        entries += [(project_name(p["path"]), lambda f=p["path"]: self._run_job_in(plan, f)) for p in items[:10]]
        popup_menu(self, [("Run the job in:", lambda: None)] + entries)

    def _run_job_in(self, plan, folder):
        if folder is None:
            self.new_project()
        else:
            self.open_project(folder)
        view = self.projects_view
        if view.running or view.chat or view.job_in_progress():
            messagebox.showinfo("Foundry", "That project is busy. Wait for it, or pick another project.")
            return
        view.set_agent("claude")
        view.queue_job(plan)

    def _rename_prompt(self, folder, title):
        new_title = simpledialog.askstring("Rename prompt", "Name:", initialvalue=title, parent=self)
        if new_title and new_title.strip():
            rename_prompt(folder, new_title.strip())
            self._sync_chrome()

    def _delete_prompt(self, folder, title):
        view = self.prompt_views.get(self._key(folder))
        if view is not None and view.chat:
            messagebox.showinfo("Foundry", "A reply is still on its way. Stop it first.")
            return
        if not messagebox.askyesno("Delete prompt?", f"Delete “{title}” and its conversation? This can't be undone."):
            return
        remove_prompt(folder)
        if view is not None:
            del self.prompt_views[self._key(folder)]
            if view is self.prompts_view:
                self.prompts_view = None
            view.destroy()
        if self.current_view == "prompts" and self.prompts_view is None:
            self.new_prompt()
        else:
            self._sync_chrome()

    # =========================================================================
    # MAIN REGION: top bar, middle page, sash, right pane
    # =========================================================================
    def _create_main_region(self):
        self.main = ctk.CTkFrame(self, corner_radius=0, fg_color=theme.BG)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(1, weight=1)

        self.topbar = TopBar(self.main, on_show_sidebar=self.toggle_sidebar)
        self.topbar.grid(row=0, column=0, sticky="ew")
        for page_id, glyph, tip, _, _ in PANE_PAGES:
            self.topbar.add_button(page_id, glyph, tip, lambda p=page_id: self.toggle_pane(p))
        self.topbar.add_button("more", "⋮", "All pages", self._more_menu)

        self.body = ctk.CTkFrame(self.main, corner_radius=0, fg_color=theme.BG)
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)  # middle page
        self.body.grid_columnconfigure(1, weight=0)  # sash
        self.body.grid_columnconfigure(2, weight=0)  # right pane (fixed width, dragged with the sash)

        self.center = ctk.CTkFrame(self.body, corner_radius=0, fg_color=theme.BG)
        self.center.grid(row=0, column=0, sticky="nsew")
        self.center.grid_columnconfigure(0, weight=1)
        self.center.grid_rowconfigure(0, weight=1)

        self.sash = Sash(self.body, on_drag=self._drag_sash)
        self.pane = SidePane(self.body, on_expand=self.toggle_pane_expanded, on_close=self.close_pane)
        self.body.bind("<Configure>", lambda e: self._fit_pane(), add="+")

    def _scaling(self):
        return ctk.ScalingTracker.get_widget_scaling(self)

    def _body_width(self):
        return max(1, int(self.body.winfo_width() / self._scaling()))

    def _pane_width(self, page_id):
        default = 300 if page_id in ("overview", "context", "saved") else max(460, int(self._body_width() * 0.45))
        width = self.pane_widths.get(page_id, default)
        return max(MIN_PANE_WIDTH, min(width, self._body_width() - MIN_CENTER_WIDTH))

    def _fit_pane(self):
        if self.pane_page and not self.pane_expanded:
            self.pane.configure(width=self._pane_width(self.pane_page))

    def _drag_sash(self, x_root):
        if not self.pane_page or self.pane_expanded:
            return
        right_edge = self.body.winfo_rootx() + self.body.winfo_width()
        width = int((right_edge - x_root) / self._scaling())
        self.pane_widths[self.pane_page] = max(MIN_PANE_WIDTH, min(width, self._body_width() - MIN_CENTER_WIDTH))
        self._fit_pane()

    # =========================================================================
    # RIGHT PANE PAGES (opened from the top-right buttons)
    # =========================================================================
    def _create_pane_pages(self):
        host = self.pane.content
        self.groups = {}

        self.overview = OverviewPanel(host, self.config_manager,
                                      on_open_settings=lambda: self.switch_view("settings"),
                                      on_open_file=self._open_file_in_editor,
                                      on_open_folder=self._show_folder)
        self.pane.add_page("overview", "Overview", self.overview)
        self.context = ContextPanel(host, self.config_manager)
        self.pane.add_page("context", "Context", self.context)
        self.prompt_list = PromptListPanel(host, self.workspace_root, on_open=self.open_prompt, on_new=self.new_prompt,
                                           on_rename=self._rename_prompt, on_delete=self._delete_prompt, on_use_prompt=self._use_prompt,
                                           on_start_job=self._start_job)
        self.pane.add_page("saved", "Prompts", self.prompt_list)

        # The terminal must exist before the run views: they launch the engine through it.
        self.terminal = TerminalView(host, workspace_root=self.workspace_root,
                                     on_process_state_change=self._on_process_state_change,
                                     cwd_provider=self._working_folder)
        self.pane.add_page("terminal", "Terminal", self.terminal)

        for page_id, _, _, title, tabs in PANE_PAGES:
            if len(tabs) > 1:
                group = PageGroup(host)
                self.groups[page_id] = group
                self.pane.add_page(page_id, title, group)

        self.views = {"terminal": self.terminal}
        files = self.groups["files"].content
        self.explorer = FileExplorer(files, workspace_root=self.workspace_root, foundry_root=self.workspace_root,
                                     on_file_select=self._open_file_in_editor, on_open_project=self._open_folder_as_project,
                                     on_open_terminal=self._open_terminal_in, on_rules_changed=self._on_rules_changed)
        self.editor_view = EditorView(files, workspace_root=self.workspace_root,
                                      on_set_as_spec=self._on_editor_set_spec, on_send_to_context=self._on_editor_add_context)
        self.views.update(explorer=self.explorer, editor=self.editor_view)

        self.views["git"] = GitTimelineView(host, workspace_root=self.workspace_root)
        self.pane.add_page("changes", "Changes", self.views["git"])

        checks = self.groups["checks"].content
        self.views["sandbox"] = SandboxView(checks, workspace_root=self.workspace_root, terminal=self.terminal,
                                            config_manager=self.config_manager, cwd_provider=self._working_folder,
                                            on_show_terminal=lambda: self.open_pane("terminal"))
        self.views["ast"] = ASTGraphView(checks, workspace_root=self.workspace_root, cwd_provider=self._working_folder,
                                         on_open_file=self._open_file_in_editor)

        self.views["playground"] = PlaygroundView(host, config_manager=self.config_manager, workspace_root=self.workspace_root)
        self.pane.add_page("models", "Arena", self.views["playground"])

        engine = self.groups["engine"].content
        self.views["mcts"] = MCTSView(engine, workspace_root=self.workspace_root, terminal=self.terminal)
        self.views["memory"] = MemoryView(engine, workspace_root=self.workspace_root, config_manager=self.config_manager)
        self.views["ouroboros"] = OuroborosView(engine, workspace_root=self.workspace_root, config_manager=self.config_manager,
                                                terminal=self.terminal, on_force_restart=self.force_auto_restart,
                                                on_open_settings=lambda: self.switch_view("settings"))
        self.views["stats"] = EngineStatsView(engine, workspace_root=self.workspace_root, terminal=self.terminal)

        for page_id, _, _, _, tabs in PANE_PAGES:
            if page_id in self.groups:
                for tab, view_id in tabs:
                    self.groups[page_id].add(tab, self.views[view_id])

    def toggle_pane(self, page_id):
        if self.pane_page == page_id:
            self.close_pane()
        else:
            self.open_pane(page_id)

    def open_pane(self, page_id, tab=None):
        if page_id not in self.pane.pages:
            return
        self.pane.show(page_id)
        if tab and page_id in self.groups:
            self.groups[page_id].show(tab)
        self.pane_page = page_id
        if not self.pane.winfo_ismapped():
            self._layout_body()
        self._fit_pane()
        self.topbar.set_active(page_id)
        if page_id == "overview":
            self._refresh_overview()
        elif page_id == "context":
            self._refresh_context()
        elif page_id == "saved":
            self._refresh_prompt_list()
        elif page_id == "checks":   # the Tests and Code map pages follow the folder on screen
            self.views["sandbox"].refresh()
            self.views["ast"].refresh()
        elif page_id == "models":
            self.views["playground"].refresh()
        elif page_id == "engine":   # re-read the log, the memory file and the settings
            self.views["mcts"].refresh(force=True)
            self.views["memory"].refresh_records()
            self.views["ouroboros"].refresh()

    def close_pane(self):
        self.pane_page = None
        self.pane_expanded = False
        self.pane.set_expanded(False)
        self._layout_body()
        self.topbar.set_active(None)

    def toggle_pane_expanded(self):
        if not self.pane_page:
            return
        self.pane_expanded = not self.pane_expanded
        self.pane.set_expanded(self.pane_expanded)
        self._layout_body()
        self._fit_pane()

    def _layout_body(self):
        """Middle page | sash | right pane, or the pane alone when expanded."""
        if self.pane_page is None:
            self.sash.grid_remove()
            self.pane.grid_remove()
            self.center.grid(row=0, column=0, sticky="nsew")
            self.body.grid_columnconfigure(0, weight=1)
            self.body.grid_columnconfigure(2, weight=0)
        elif self.pane_expanded:
            self.center.grid_remove()
            self.sash.grid_remove()
            self.pane.grid(row=0, column=2, sticky="nsew")
            self.body.grid_columnconfigure(0, weight=0)
            self.body.grid_columnconfigure(2, weight=1)
        else:
            self.center.grid(row=0, column=0, sticky="nsew")
            self.sash.grid(row=0, column=1, sticky="ns")
            self.pane.grid(row=0, column=2, sticky="nsew")
            self.body.grid_columnconfigure(0, weight=1)
            self.body.grid_columnconfigure(2, weight=0)

    def _more_menu(self):
        btn = self.topbar.buttons["more"]
        items = []
        for page_id, _, _, title, tabs in PANE_PAGES:
            if len(tabs) > 1:
                items += [(f"{title} › {tab}", lambda p=page_id, t=tab: self.open_pane(p, t)) for tab, _ in tabs]
            else:
                items.append((title, lambda p=page_id: self.open_pane(p)))
        items += [None, ("Settings", lambda: self.switch_view("settings")), ("Help", lambda: self.switch_view("help")),
                  None, ("Restart Foundry", lambda: self.force_auto_restart("User Triggered Relaunch"))]
        popup_menu(btn, items, btn.winfo_rootx(), btn.winfo_rooty() + btn.winfo_height() + 4)

    # =========================================================================
    # MIDDLE PAGES
    # =========================================================================
    def _create_center_pages(self):
        # Improve: the old Build + Patch tabs, compacted into one autonomous self-learning,
        # self-patching, self-updating loop for the harness itself
        self.improve_view = SelfImproveView(self.center, workspace_root=self.workspace_root,
                                            config_manager=self.config_manager, terminal=self.terminal,
                                            on_open_settings=lambda: self.switch_view("settings"),
                                            on_show_details=self._show_details,
                                            on_status=lambda **kw: hasattr(self, "improve_view")
                                            and self._on_run_status(self.improve_view, **kw))
        # Projects and prompts get a page each, made when first opened (see _make_project_view / _make_prompt_view)
        self.settings_view = SettingsView(self.center, config_manager=self.config_manager, workspace_root=self.workspace_root,
                                          on_run_check=self._check_health,
                                          on_restart=lambda: self.force_auto_restart("User Triggered Relaunch"),
                                          on_rules_changed=lambda rules: self.explorer.refresh_tree(),
                                          on_open_help=lambda: self.switch_view("help"),
                                          on_open_file=self._open_file_in_editor)
        self.help_view = HelpView(self.center, on_switch_view=self.switch_view)
        self.views.update(improve=self.improve_view, settings=self.settings_view, help=self.help_view)

    def switch_view(self, view_id):
        """Shows a page: the middle pages directly, tool pages in the right pane."""
        view_id = VIEW_ALIASES.get(view_id, view_id)
        if view_id in PANE_ROUTES:
            self.open_pane(*PANE_ROUTES[view_id])
            return
        if view_id in self.pane.pages:
            self.open_pane(view_id)
            return
        if view_id not in CENTER_PAGES:
            return
        if view_id == "projects":
            if self.projects_view is None:  # nothing open yet: start a new project
                self.new_project()
                return
            if self.selection[0] not in ("project", "new"):
                view = self.projects_view
                self.selection = ("new", None) if view is self.new_view else ("project", view.current_folder())
            target = self.projects_view
        elif view_id == "prompts":
            if self.prompts_view is None:
                self.new_prompt()
                return
            if self.selection[0] not in ("prompt", "new_prompt"):
                view = self.prompts_view
                self.selection = ("new_prompt", None) if view is self.new_prompt_view else ("prompt", view.current_folder())
            target = self.prompts_view
        else:
            self.selection = (view_id, None)
            target = self.views[view_id]
        for page in [self.views[vid] for vid in CENTER_PAGES if vid not in ("projects", "prompts")] + self._all_chat_views():
            if page is not target:
                page.grid_forget()
        target.grid(row=0, column=0, sticky="nsew")
        previous = self.current_view
        self.current_view = view_id
        if view_id == "improve":
            self.improve_view.refresh()
        elif view_id == "settings":
            self.settings_view.refresh()
        if self.pane_expanded:  # a middle page was asked for; make room for it
            self.toggle_pane_expanded()
        # The prompt studio brings its list along, like a Claude page brings its panel; leaving it brings Overview back.
        if view_id == "prompts" and self.pane_page in (None, "overview", "context"):
            self.open_pane("saved")
        elif view_id != "prompts" and previous == "prompts" and self.pane_page == "saved":
            self.open_pane("overview")
        self._sync_chrome()

    def _sync_chrome(self):
        """Sidebar selection, top-bar title and the right pane follow the middle page."""
        kind, path = self.selection
        self.sidebar.select(kind, path)
        if self.current_view == "improve":
            self.topbar.set_title("Improve Foundry", self.workspace_root)
        elif self.current_view == "projects":
            if kind == "new":
                self.topbar.set_title("New project")
            else:
                folder = self.projects_view.current_folder()
                self.topbar.set_title(project_name(folder), folder, actions=self._project_actions(folder))
        elif self.current_view == "prompts":
            view = self.prompts_view
            self.topbar.set_title("New prompt" if kind == "new_prompt" else view.title(), "Prompt studio")
        else:
            self.topbar.set_title({"settings": "Settings", "help": "Help"}.get(self.current_view, ""))
        # Files shows the folder being worked on first, like the Files pane of a session
        if self.current_view == "projects":
            folder = self.projects_view.current_folder()
            if folder and os.path.isdir(folder):
                self.explorer.set_project(folder)
                self.editor_view.workspace_root = folder  # the editor shows paths relative to it
        elif self.current_view == "improve":
            self.explorer.set_project(self.workspace_root)
            self.editor_view.workspace_root = self.workspace_root
        self._refresh_overview()
        self._refresh_context()
        self._refresh_prompt_list()

    # ----------------------------------------------------------- the Launch button
    def _project_actions(self, folder):
        """The chips after a project's name: Launch (or Make a launcher)."""
        if not folder or not os.path.isdir(folder):
            return ()
        launcher = find_launcher(folder)
        if launcher:
            return (("▶  Launch", describe_launcher(launcher), lambda: self._launch(launcher), True),)
        return (("Make a launcher", "Ask Claude to add a one-click launch script, an app icon and a desktop shortcut, and build them",
                 lambda: self._make_launcher(folder), False),)

    def _launch(self, launcher):
        if launcher["source"] == "html":
            webbrowser.open(f"file:///{launcher['command'].replace(os.sep, '/')}")
            return
        self.open_pane("terminal")
        self.terminal.run_launch(launcher["command"], launcher["cwd"], launcher.get("name") or project_name(launcher["cwd"]))

    def _make_launcher(self, folder):
        view = self.projects_view
        if view is None or view.chat or view.running:
            messagebox.showinfo("Foundry", "Wait for the current reply or Build to finish first.")
            return
        view.set_agent("claude")  # only the architect starts Builds
        view.send_text(launcher_request(folder))

    def _overview_target(self):
        """(run view, name, folder) of the page on screen."""
        if self.current_view == "projects" and self.projects_view is not None:
            view = self.projects_view
            folder = view.current_folder()
            if view is self.new_view and not view.has_history(folder):
                return view, "New project", folder
            return view, project_name(folder), folder
        if self.current_view == "prompts" and self.prompts_view is not None:
            view = self.prompts_view
            return view, view.title(), view.current_folder()
        return self.improve_view, "Foundry (improving itself)", self.workspace_root

    def _refresh_overview(self):
        if not hasattr(self, "overview") or not hasattr(self, "improve_view") or self.pane_page != "overview":
            return
        view, name, folder = self._overview_target()
        entries = load_journal(folder) if folder else []
        chat_spent = chat_spend(load_chat(folder)) if folder and view is not self.improve_view else 0.0
        note = None
        if self.current_view == "prompts" and view is self.prompts_view:
            count = sum(1 for m in view.messages if m.get("role") in ("user", "assistant"))
            note = f"Prompt studio · {'job plan' if view.studio_mode == 'job' else 'prompt'} · {count} message{'s' if count != 1 else ''}"
        self.overview.update_target(name, folder, entries, live=self._live.get(view), chat_spent=chat_spent, note=note)

    def _refresh_context(self):
        if not hasattr(self, "context") or not hasattr(self, "improve_view") or self.pane_page != "context":
            return
        if self.current_view == "projects" and self.projects_view is not None:
            self.context.show_chat(self.projects_view, "project")
        elif self.current_view == "prompts" and self.prompts_view is not None:
            self.context.show_chat(self.prompts_view, "prompt")
        else:
            self.context.show_improve(self.improve_view)

    def _refresh_prompt_list(self):
        if not hasattr(self, "prompt_list") or self.pane_page != "saved":
            return
        view = self.prompts_view if self.current_view == "prompts" else None
        selected = view.current_folder() if view is not None and self.selection[0] == "prompt" else None
        self.prompt_list.refresh(selected_folder=selected)
        self.prompt_list.set_working(*(view.working_block() if view is not None else ("", "")))

    # =========================================================================
    # RUN STATUS
    # =========================================================================
    def _on_process_state_change(self, is_running, label):
        if is_running:
            self.after(50, self._update_running)  # the run view marks itself running right after this callback
        else:
            # Play chime sound on completion
            if winsound and _bool(self.config_manager.get("SOUND_NOTIFICATIONS", "true")):
                try:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass

    def _on_run_status(self, view, state=None, spent=None, limit=None, project=None):
        """Status from a run view (Improve, a project page or a prompt page), whether or not it is on screen."""
        if not hasattr(self, "sidebar") or not hasattr(self, "improve_view"):  # still building the window
            return
        if spent is not None and limit:
            self._live[view] = (spent, limit)
            target, _, folder = self._overview_target()
            if self.pane_page == "overview" and target is view:
                self.overview.update_spending(load_journal(folder), self._live[view])
        if project is not None and state is None and spent is None:
            # A chat reply (its cost shows in Overview) or a new folder picked: the chrome follows the page.
            if view is self.projects_view and self.current_view == "projects":
                self._sync_chrome()
            elif view is self.prompts_view and self.current_view == "prompts":
                self._sync_chrome()
            else:
                self._refresh_overview()
                self._refresh_context()
        if state == "Ready" and not view.running:
            self._live.pop(view, None)
            self._update_running()
            if view is not self.improve_view:  # the run added a history entry: refresh the row's subtitle
                self.sidebar.set_projects(load_projects(self.workspace_root))
            if view is self.projects_view and self.current_view == "projects":
                self._sync_chrome()  # a Build may have added a launcher
            else:
                self._refresh_overview()
                self._refresh_context()
        elif state is not None:
            self._update_running()

    def _running_rows(self):
        rows = [("improve", None)] if self.improve_view.running else []
        for view in self._all_project_views():
            if view.running and view.run_state.get("folder"):
                rows.append(("project", view.run_state["folder"]))
        return rows

    def _update_running(self):
        self.sidebar.set_running(self._running_rows())
        if self._running_rows():
            self._start_spinner()

    def _start_spinner(self):
        if self._spin_job is None:
            self._spin()

    def _spin(self):
        """Animates the ✻ on sidebar rows that are working, like Claude's spinner on a running session."""
        rows = self._running_rows()
        if rows != self.sidebar._running:
            self.sidebar.set_running(rows)
        if not rows:
            self._spin_job = None
            return
        self._spin_frame += 1
        self.sidebar.tick(self._spin_frame)
        self._spin_job = self.after(150, self._spin)

    def _show_details(self):
        self.open_pane("terminal")
        self.terminal.show_tab("foundry")

    def _working_folder(self):
        """Where a new terminal opens: the project on screen, or Foundry itself."""
        if self.current_view == "projects" and self.projects_view is not None:
            folder = self.projects_view.current_folder()
            if folder and os.path.isdir(folder):
                return folder
        return self.workspace_root

    def _run_ai_command(self, command, cwd, on_done, who="AI"):
        """A command a model proposed in a project chat, after the user clicked Run: it runs in its own terminal tab."""
        self.open_pane("terminal")
        self.terminal.run_ai_command(command, cwd, on_done=on_done, who=who)

    def _check_health(self):
        """Settings › Improve › Check now."""
        self.switch_view("improve")
        self.improve_view.check_health()

    # =========================================================================
    # COMMAND PALETTE & HOTKEYS
    # =========================================================================
    def _bind_hotkeys(self):
        self.bind("<Control-k>", lambda e: self.open_command_palette())
        self.bind("<Control-p>", lambda e: self.open_command_palette())
        self.bind("<Control-b>", lambda e: self.toggle_sidebar())
        self.bind("<Control-n>", lambda e: (self.new_project(), "break")[1])
        self.bind("<Control-N>", lambda e: (self.new_prompt(), "break")[1])  # Ctrl+Shift+N
        self.bind("<F1>", lambda e: self.switch_view("help"))

    def open_command_palette(self):
        actions = [
            ("Open Interactive Help & User Manual (F1)", "Help", lambda: self.switch_view("help")),
            ("Self-Improve Foundry (run now)", "Engine", lambda: (self.switch_view("improve"), self.improve_view._on_go_pressed())),
            ("Check Foundry's Health (no AI calls)", "Engine", self._check_health),
            ("Undo Last Self-Improvement", "Engine", lambda: (self.switch_view("improve"), self.improve_view._undo_last())),
            ("New Project (Ctrl N)", "Engine", self.new_project),
            ("New Prompt (Ctrl Shift N)", "Prompts", self.new_prompt),
            ("Build What We Discussed (without waiting for Claude)", "Engine",
             lambda: (self.switch_view("projects"), self._current_project_view().build_discussed())),
            ("Launch the Open Project", "Engine", self._launch_current),
            ("Run Sandbox Unit Tests (npm test)", "Tests", lambda: self.switch_view("sandbox")),
            ("Open Overview", "Navigation", lambda: self.open_pane("overview")),
            ("Open Context (what the models see)", "Navigation", lambda: self.open_pane("context")),
            ("Open Saved Prompts", "Navigation", lambda: self.open_pane("saved")),
            ("Open Terminal & Engine Log", "Navigation", lambda: self.open_pane("terminal")),
            ("Chat With the Models About a Project", "Navigation", lambda: self.switch_view("projects")),
            ("Open Files & Code Editor", "Navigation", lambda: self.switch_view("explorer")),
            ("Open AST Code Map", "Navigation", lambda: self.switch_view("ast")),
            ("Open Diagnostics (on Improve)", "Navigation", lambda: self.switch_view("improve")),
            ("Open Git Timeline & Rollbacks", "Navigation", lambda: self.switch_view("git")),
            ("Open Multi-Model Prompt Arena", "Navigation", lambda: self.switch_view("playground")),
            ("Open MCTS Search Tree", "Navigation", lambda: self.switch_view("mcts")),
            ("Open Episodic Vector Memory", "Navigation", lambda: self.switch_view("memory")),
            ("Open Ouroboros Monitor", "Navigation", lambda: self.switch_view("ouroboros")),
            ("Open Settings", "Navigation", lambda: self.switch_view("settings")),
            ("Settings › API Keys & Providers", "Navigation", lambda: (self.switch_view("settings"), self.settings_view.open_page("keys"))),
            ("Settings › AI Access", "Navigation", lambda: (self.switch_view("settings"), self.settings_view.open_page("access"))),
            ("Settings › Skills", "Navigation", lambda: (self.switch_view("settings"), self.settings_view.open_page("skills"))),
            ("Toggle Sidebar (Ctrl B)", "Layout", self.toggle_sidebar),
            ("Close Right Pane", "Layout", self.close_pane),
            ("Clear Terminal Logs", "Terminal", self.terminal.clear_terminal),
            ("Analyze Workspace AST", "AST", lambda: (self.switch_view("ast"), self.views["ast"]._analyze_workspace_ast())),
            ("Show Git Status", "Git", lambda: (self.switch_view("git"), self.views["git"]._show_git_status())),
            ("Force Auto-Restart & Relaunch IDE", "System", lambda: self.force_auto_restart("User Triggered Relaunch"))
        ]
        CommandPalette(self, actions)

    def _launch_current(self):
        if self.current_view != "projects" or self.projects_view is None:
            messagebox.showinfo("Foundry", "Open a project first.")
            return
        launcher = find_launcher(self.projects_view.current_folder())
        if launcher:
            self._launch(launcher)
        else:
            self._make_launcher(self.projects_view.current_folder())

    # =========================================================================
    # FORCE AUTO-RESTART & RELAUNCH CONTROLLER
    # =========================================================================
    def force_auto_restart(self, reason="Autonomous AI Self-Patch"):
        """Gracefully flushes configuration, saves state, and hot-relaunches the Foundry process."""
        try:
            self.config_manager.save()
        except Exception:
            pass

        import subprocess
        python_exe = sys.executable
        # argv[0] may be relative to wherever the GUI was started from; the child runs with cwd=workspace_root
        args = [python_exe, os.path.abspath(__file__)] + sys.argv[1:]
        try:
            subprocess.Popen(args, cwd=self.workspace_root)
        except Exception as e:
            messagebox.showerror("Relaunch Error", f"Failed to spawn replacement process: {e}")
            return

        self.destroy()
        sys.exit(0)

    # =========================================================================
    # HAND-OFFS FROM OTHER VIEWS
    # =========================================================================
    def _open_file_in_editor(self, file_path):
        self.editor_view.open_file(file_path)
        self.switch_view("editor")

    def _open_folder_as_project(self, folder):
        self.open_project(folder)

    def _open_terminal_in(self, folder):
        self.open_pane("terminal")
        self.terminal.new_shell(folder)

    def _on_rules_changed(self, rules):
        if self.current_view == "settings":
            self.settings_view.refresh()

    def _on_editor_set_spec(self, file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Error", f"Couldn't read {file_path}: {e}")
            return
        self._current_project_view().set_task(content)
        self.switch_view("projects")

    def _on_editor_add_context(self, file_path):
        """The Editor's "Add Context": the file goes into the project chat's message box."""
        self._current_project_view().attach_file(file_path)
        self.switch_view("projects")


if __name__ == "__main__":
    app = FoundryIDE()
    app.mainloop()
