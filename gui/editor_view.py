import os
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import theme

SPEC_TEMPLATE = """# Specification: Core Feature Contract

## 1. Domain Overview
Define the core service, invariants, and expected behavior.

## 2. Interface Contracts
- **Symbol:** `execute_core_pipeline(x: number): number`
- **Preconditions:** `x >= 0`
- **Postconditions:** `returns 2 * x + 1`
- **Complexity Bound:** `O(1)`

## 3. Boundary Invariants
- Negative inputs must throw strict Error(`non-negative input required`).
- Memory allocations must remain constant under 1,000 cyclic calls.
"""

MODULE_TEMPLATE = """/**
 * Autonomously Synthesized Service
 * Synthesized via Astra & Verified by Gemini Auditor
 */

export function execute_core_pipeline(x: number): number {
  if (x < 0) {
    throw new Error("non-negative input required");
  }
  return 2 * x + 1;
}
"""

TEST_TEMPLATE = """import { execute_core_pipeline } from "../src/module";

describe("Core Pipeline Invariant Tests", () => {
  it("kills boundary mutant for zero and positive numbers", () => {
    expect(execute_core_pipeline(0)).toBe(1);
    expect(execute_core_pipeline(5)).toBe(11);
  });

  it("enforces non-negative input invariant", () => {
    expect(() => execute_core_pipeline(-1)).toThrow("non-negative input required");
  });
});
"""

class EditorView(ctk.CTkFrame):
    """Integrated Code, Spec, and Config Editor with templates and actions."""

    def __init__(self, master, workspace_root, on_set_as_spec=None, on_send_to_context=None, **kwargs):
        super().__init__(master, corner_radius=10, fg_color=("gray95", "gray14"), **kwargs)
        self.workspace_root = workspace_root
        self.on_set_as_spec = on_set_as_spec
        self.on_send_to_context = on_send_to_context

        self.current_file_path = None
        self.is_modified = False

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._create_toolbar()
        self._create_editor_area()
        self._create_status_bar()

        # Load default spec.md if present
        default_spec = os.path.join(self.workspace_root, "spec.md")
        if os.path.exists(default_spec):
            self.open_file(default_spec)

    def _create_toolbar(self):
        tb = ctk.CTkFrame(self, fg_color="transparent")
        tb.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        tb.grid_columnconfigure(0, weight=1)

        # File path label
        self.lbl_path = ctk.CTkLabel(
            tb,
            text="No file opened",
            font=ctk.CTkFont(family=theme.MONO_FAMILY, size=13, weight="bold"),
            text_color="#007AFF"
        )
        self.lbl_path.grid(row=0, column=0, sticky="w")

        # Action buttons
        btn_box = ctk.CTkFrame(tb, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        btn_save = ctk.CTkButton(btn_box, text="💾 Save", width=70, height=28, font=ctk.CTkFont(size=11, weight="bold"),
                                 command=self.save_file)
        btn_save.pack(side="left", padx=3)

        btn_spec = ctk.CTkButton(btn_box, text="🚀 Send to Projects", width=120, height=28, font=ctk.CTkFont(size=11),
                                 fg_color="gray30", hover_color="gray20", command=self._set_as_spec)
        btn_spec.pack(side="left", padx=3)

        btn_ctx = ctk.CTkButton(btn_box, text="📎 Add Context", width=95, height=28, font=ctk.CTkFont(size=11),
                                fg_color="gray30", hover_color="gray20", command=self._add_to_context)
        btn_ctx.pack(side="left", padx=3)

        # Template menu
        self.tpl_var = ctk.StringVar(value="Templates ▾")
        tpl_menu = ctk.CTkOptionMenu(
            btn_box,
            values=["Insert Spec Template", "Insert Module Template", "Insert Test Template"],
            variable=self.tpl_var,
            width=140,
            height=28,
            font=ctk.CTkFont(size=11),
            command=self._insert_template
        )
        tpl_menu.pack(side="left", padx=3)

    def _create_editor_area(self):
        self.editor = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family=theme.MONO_FAMILY, size=13),
            wrap="none",
            corner_radius=8,
            fg_color=("gray98", "#0F1116"),
            text_color=("#1A1A1A", "#E6EDF3")
        )
        self.editor.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        self.editor.bind("<KeyRelease>", self._on_key_release)
        self.editor.bind("<Control-s>", lambda e: self.save_file())

    def _create_status_bar(self):
        sb = ctk.CTkFrame(self, fg_color="transparent")
        sb.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 10))

        self.lbl_status = ctk.CTkLabel(sb, text="Ready", font=ctk.CTkFont(size=11), text_color="gray60")
        self.lbl_status.pack(side="left")

        self.lbl_metrics = ctk.CTkLabel(sb, text="Lines: 0 | Chars: 0", font=ctk.CTkFont(size=11), text_color="gray60")
        self.lbl_metrics.pack(side="right")

    def open_file(self, full_path):
        if not os.path.exists(full_path):
            return

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            self.current_file_path = full_path
            rel = os.path.relpath(full_path, self.workspace_root)
            self.lbl_path.configure(text=rel)

            self.editor.delete("1.0", "end")
            self.editor.insert("end", content)
            self.is_modified = False
            self.lbl_status.configure(text="Saved", text_color="#34C759")
            self._update_metrics()
        except Exception as e:
            messagebox.showerror("Read Error", f"Failed to read file:\n{e}")

    def save_file(self):
        if not self.current_file_path:
            return

        try:
            content = self.editor.get("1.0", "end-1c")
            with open(self.current_file_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.is_modified = False
            self.lbl_status.configure(text="Saved successfully", text_color="#34C759")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save file:\n{e}")

    def _on_key_release(self, event=None):
        if not self.is_modified:
            self.is_modified = True
            self.lbl_status.configure(text="● Modified (unsaved)", text_color="#FF9500")
        self._update_metrics()

    def _update_metrics(self):
        text = self.editor.get("1.0", "end-1c")
        lines = len(text.splitlines()) if text else 0
        chars = len(text)
        self.lbl_metrics.configure(text=f"Lines: {lines} | Chars: {chars}")

    def _set_as_spec(self):
        if self.current_file_path and self.on_set_as_spec:
            self.on_set_as_spec(self.current_file_path)
            messagebox.showinfo("Spec Set", f"Active build specification set to:\n{self.current_file_path}")

    def _add_to_context(self):
        if self.current_file_path and self.on_send_to_context:
            self.on_send_to_context(self.current_file_path)
            messagebox.showinfo("Context Attached", f"Added to Chat Context:\n{self.current_file_path}")

    def _insert_template(self, choice):
        self.tpl_var.set("Templates ▾")
        if choice == "Insert Spec Template":
            self.editor.insert("insert", SPEC_TEMPLATE)
        elif choice == "Insert Module Template":
            self.editor.insert("insert", MODULE_TEMPLATE)
        elif choice == "Insert Test Template":
            self.editor.insert("insert", TEST_TEMPLATE)
        self._on_key_release()
