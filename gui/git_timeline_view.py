import os
import subprocess
import tkinter as tk
from tkinter import simpledialog, messagebox
import customtkinter as ctk
import theme

class GitTimelineView(ctk.CTkFrame):
    """
    Autonomous Git Timeline & Rollback Engine:
    - Inspects real git commit histories from Foundry convergence cycles
    - Color-coded git diff inspector (+ green, - red)
    - Automated rollback to previous commits
    - Branch creation, git status, and stash management
    """

    def __init__(self, master, workspace_root, **kwargs):
        super().__init__(master, corner_radius=10, fg_color=("gray95", "gray14"), **kwargs)
        self.workspace_root = workspace_root
        self.target_repo = os.path.join(self.workspace_root, "demo-output")

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._create_header()
        self._create_repo_selector()
        self._create_commit_list()
        self._create_diff_viewer()
        self.refresh_commits()

    def _create_header(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 6))
        hdr.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(hdr, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(title_box, text="Autonomous Git Timeline & Rollback", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(title_box, text="Inspect automated Swarm commits, branch convergence history, and semantic diffs",
                     font=ctk.CTkFont(size=12), text_color="gray60").pack(anchor="w")

        btn_box = ctk.CTkFrame(hdr, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        btn_status = ctk.CTkButton(btn_box, text="📊 Git Status", height=32, command=self._show_git_status)
        btn_status.pack(side="left", padx=4)

        btn_ref = ctk.CTkButton(btn_box, text="🔄 Refresh", height=32, command=self.refresh_commits)
        btn_ref.pack(side="left", padx=4)

    def _create_repo_selector(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 10))
        bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(bar, text="Repository Path:", font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=(0, 8), sticky="w")

        self.entry_repo = ctk.CTkEntry(bar, height=32)
        self.entry_repo.insert(0, self.target_repo)
        self.entry_repo.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.entry_repo.bind("<Return>", lambda e: self.refresh_commits())

        btn_browse = ctk.CTkButton(bar, text="Browse", width=75, height=32, command=self._browse_repo)
        btn_browse.grid(row=0, column=2, sticky="e")

    def _create_commit_list(self):
        left_frame = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "gray17"))
        left_frame.grid(row=2, column=0, sticky="nsew", padx=(20, 10), pady=(0, 16))
        left_frame.grid_columnconfigure(0, weight=1)
        left_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(left_frame, text="COMMIT LOG (Latest 25 Commits)", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="gray60").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.commits_scroll = ctk.CTkScrollableFrame(left_frame, fg_color="transparent")
        self.commits_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

    def _create_diff_viewer(self):
        right_frame = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "gray17"))
        right_frame.grid(row=2, column=1, sticky="nsew", padx=(10, 20), pady=(0, 16))
        right_frame.grid_columnconfigure(0, weight=1)
        right_frame.grid_rowconfigure(1, weight=1)

        top_r = ctk.CTkFrame(right_frame, fg_color="transparent")
        top_r.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        top_r.grid_columnconfigure(0, weight=1)

        self.lbl_selected_commit = ctk.CTkLabel(top_r, text="COMMIT DIFF & ARTIFACTS", font=ctk.CTkFont(size=11, weight="bold"),
                                               text_color="gray60")
        self.lbl_selected_commit.grid(row=0, column=0, sticky="w")

        self.btn_rollback = ctk.CTkButton(top_r, text="⏮️ Rollback to Commit", width=140, height=24, font=ctk.CTkFont(size=11, weight="bold"),
                                          fg_color="#E74C3C", hover_color="#C0392B", state="disabled",
                                          command=self._rollback_action)
        self.btn_rollback.grid(row=0, column=1, sticky="e")

        self.diff_box = ctk.CTkTextbox(
            right_frame,
            font=ctk.CTkFont(family=theme.MONO_FAMILY, size=11),
            wrap="none",
            corner_radius=6,
            fg_color=("gray98", "#0C0D10")
        )
        self.diff_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

    def refresh_commits(self):
        for w in self.commits_scroll.winfo_children():
            w.destroy()

        repo = self.entry_repo.get().strip()
        if not os.path.exists(repo):
            ctk.CTkLabel(self.commits_scroll, text="Target repository directory not found.", text_color="gray60").pack(pady=20)
            return

        try:
            res = subprocess.run(
                ["git", "-C", repo, "log", "-n", "25", "--pretty=format:%h|%an|%ar|%s"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )

            if res.returncode != 0:
                ctk.CTkLabel(self.commits_scroll, text=f"Not a git repo or no commits:\n{res.stderr}", text_color="gray60").pack(pady=20)
                return

            lines = res.stdout.strip().splitlines()
            if not lines:
                ctk.CTkLabel(self.commits_scroll, text="No commits found in this repository.", text_color="gray60").pack(pady=20)
                return

            for line in lines:
                parts = line.split("|")
                if len(parts) >= 4:
                    h, author, when, msg = parts[0], parts[1], parts[2], "|".join(parts[3:])
                else:
                    h, author, when, msg = parts[0], "Swarm", "", line

                card = ctk.CTkFrame(self.commits_scroll, corner_radius=6, fg_color=("gray85", "gray22"))
                card.pack(fill="x", pady=4, padx=4)

                top = ctk.CTkFrame(card, fg_color="transparent")
                top.pack(fill="x", padx=10, pady=(6, 2))

                ctk.CTkLabel(top, text=f"commit {h}", font=ctk.CTkFont(family=theme.MONO_FAMILY, size=12, weight="bold"),
                             text_color="#007AFF").pack(side="left")
                ctk.CTkLabel(top, text=when, font=ctk.CTkFont(size=10), text_color="gray60").pack(side="right")

                m_lbl = ctk.CTkLabel(card, text=msg, font=ctk.CTkFont(size=12, weight="bold"), anchor="w")
                m_lbl.pack(fill="x", padx=10, pady=2)

                btn = ctk.CTkButton(
                    card,
                    text="Inspect Diff ❯",
                    height=22,
                    font=ctk.CTkFont(size=11),
                    command=lambda ch=h, cm=msg: self._load_commit_diff(ch, cm)
                )
                btn.pack(fill="x", padx=10, pady=(2, 6))

        except Exception as e:
            ctk.CTkLabel(self.commits_scroll, text=f"Error executing git: {e}", text_color="#E74C3C").pack(pady=20)

    def _load_commit_diff(self, commit_hash, msg):
        self.selected_commit = commit_hash
        self.lbl_selected_commit.configure(text=f"COMMIT: {commit_hash} — {msg[:30]}...")
        self.btn_rollback.configure(state="normal")

        repo = self.entry_repo.get().strip()
        try:
            res = subprocess.run(
                ["git", "-C", repo, "show", "--stat", "-p", commit_hash],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            self.diff_box.delete("1.0", "end")
            self.diff_box.insert("end", res.stdout if res.stdout else "No diff content.")
        except Exception as e:
            self.diff_box.delete("1.0", "end")
            self.diff_box.insert("end", f"Error showing commit: {e}")

    def _show_git_status(self):
        repo = self.entry_repo.get().strip()
        try:
            res = subprocess.run(
                ["git", "-C", repo, "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            messagebox.showinfo("Git Status", res.stdout if res.stdout else "Clean working directory.")
        except Exception as e:
            messagebox.showerror("Git Error", str(e))

    def _rollback_action(self):
        if not hasattr(self, "selected_commit") or not self.selected_commit:
            return
        if messagebox.askyesno("Confirm Rollback", f"Are you sure you want to rollback repository to commit {self.selected_commit}?"):
            repo = self.entry_repo.get().strip()
            try:
                res = subprocess.run(
                    ["git", "-C", repo, "checkout", self.selected_commit],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                if res.returncode != 0:
                    messagebox.showerror("Rollback Failed", res.stderr.strip() or f"git checkout exited with code {res.returncode}")
                    return
                # git prints checkout results on stderr
                messagebox.showinfo("Rollback Complete", f"Checked out commit {self.selected_commit}.\n{res.stdout or res.stderr}")
                self.refresh_commits()
            except Exception as e:
                messagebox.showerror("Rollback Failed", str(e))

    def _browse_repo(self):
        d = ctk.filedialog.askdirectory(initialdir=self.workspace_root)
        if d:
            self.entry_repo.delete(0, "end")
            self.entry_repo.insert(0, d)
            self.refresh_commits()
