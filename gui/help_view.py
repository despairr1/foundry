import os
import tkinter as tk
import customtkinter as ctk

HELP_TOPICS = [
    {
        "id": "overview",
        "icon": "🌟",
        "title": "Foundry Architecture & The Tri-Model Swarm",
        "subtitle": "How the tri-model consensus swarm and autonomous engine function",
        "view_target": None,
        "keywords": ["architecture", "overview", "swarm", "the 3", "consensus", "mcts", "fable", "gemini", "astra", "concept"],
        "content": """
## ⚡ Welcome to Foundry Autonomous Engineering Engine (v3.0)

Foundry is an autonomous software engineering engine built around a **Tri-Model Consensus Swarm**, **Monte Carlo Tree Search (MCTS)**, **Language Server Protocol (LSP) Multiplexing**, and **Semantic AST Patching**.

---

### 🤖 The Tri-Model Consensus Swarm ("The 3")
Traditional AI coding tools rely on a single model attempting to design, code, and test simultaneously, resulting in hallucinations and boundary regressions. Foundry splits software engineering into three adversarial, specialized roles:

1. **🏛️ Lead Architect — Anthropic Claude (Claude Fable 5.1 / Opus 5.5)**
   - **Role**: Deconstructs requirements and specifications into formal **ModuleArchitectureContracts**.
   - **Deliverables**: Invariant schemas, interface boundaries, type specifications, and state machine transition rules.
   - **Why Fable?**: Claude Fable 5.1 is Anthropic's flagship autonomous coding and reasoning model, providing deep architectural reasoning with zero hallucinated interfaces.

2. **🛡️ Invariant Auditor — Google Gemini (Gemini 3.1 Pro / Flash)**
   - **Role**: Operates adversarially against the generated code by designing mutation-lethal property tests.
   - **Deliverables**: Boundary condition assertions, property tests, concurrency race condition guards, and memory leak detectors.
   - **Why Gemini?**: Gemini 3.1 Pro features massive context analysis and mathematical reasoning, ideal for discovering subtle edge cases and generating strict assertion suites.

3. **⚡ Elite Code Synthesizer — OpenAI Astra (GPT-5 / o3-mini)**
   - **Role**: Synthesizes type-safe semantic Abstract Syntax Tree (AST) patches that satisfy the Architect's contracts and pass the Auditor's tests.
   - **Deliverables**: Exact Tree-sitter byte-range function replacements (`REPLACE_FUNCTION`, `INSERT_IMPORT`, `REPLACE_CLASS`).
   - **Why Astra?**: Powered by OpenAI's frontier reasoning and coding models, Astra specializes in high-correctness AST transformations and algorithmic implementations.

---

### 🔄 The Autonomous Engineering Loop
1. **Specification Ingestion**: The engine reads your feature requirements from `spec.md`.
2. **Contract Synthesis**: The Architect designs the formal state and interface contracts.
3. **Property Test Generation**: The Auditor generates adversarial unit tests before code is written.
4. **MCTS Branch Exploration**: The engine branches into concurrent paths (Alpha, Beta, Gamma) explored by the Synthesizer.
5. **AST Patching & LSP Verification**: Code is spliced using byte offsets (no line numbers) and checked in milliseconds against live LSP diagnostics (`typescript-language-server`, `pyright`, etc.).
6. **Sandbox Execution & Mutation Killing**: Tests execute in an isolated container. Code only converges when mutation kill ratio exceeds the target threshold (default: 85%).
7. **Episodic Memory Retention**: Successful patterns are indexed into the SQLite vector memory (`.foundry-memory.sqlite`) for future self-learning.
"""
    },
    {
        "id": "layout",
        "icon": "🧭",
        "title": "Finding your way around",
        "subtitle": "Sidebar on the left, your page in the middle, tools on the right",
        "view_target": None,
        "keywords": ["layout", "sidebar", "pane", "buttons", "top right", "where", "navigation", "tabs", "find", "projects", "settings"],
        "content": """
## 🧭 Finding your way around

Foundry is laid out like the Claude desktop app: three columns.

---

### Left: the sidebar
- **↻ Improve**: Foundry improves itself (the self-learning, self-patching harness).
- **+ New project**: opens a chat with Claude about what to build; when it sounds right, say go and Claude starts the Build. Point it at an empty folder for something new, or at a folder that already has code.
- **Projects**: every folder Foundry has worked on, newest first, with its latest request underneath. Click one to open it; right-click to show its folder or remove it from the list (the files are never touched). A spinning ✻ means Foundry is working on it right now.
- **⚙ Settings** at the bottom, plus **?** for this help and **⌕** for the command search (Ctrl K).
- **◧** hides the sidebar (Ctrl B); the same button at the top left brings it back.

### Middle: the page you're on
Improve, a project, Settings or Help. While Foundry works, its live activity feed shows here.

### Right: tools, opened from the buttons at the top right
- **◨ Overview**: what Foundry is working on, how much of the spending limit is used, which AI models are connected, and the files the last run changed.
- **›_ Terminal**: real terminals. **+** opens a PowerShell in the folder you're working on (type anything, as in any terminal; select text and Ctrl+C copies, Ctrl+V pastes). The **Foundry** tab shows what Foundry's AI is running (Builds, Improve rounds), commands a model proposed in a chat run in their own tab after you click Run, and **Log** is the engine's log file.
- **✎ Files** and the **Editor** (click a file to edit it).
- **± Changes**: the Git timeline and rollbacks.
- **✓ Checks**: Tests (run the folder's tests, read the result) and the Code map (the files and what they export).
- **✦ Arena**: one question to all three models, their replies and costs side by side. (Chatting with them happens in each project's page.)
- **◎ Engine internals**: the Search tree (what the three branches did, from the log), Memory (the episodes the engine stored), Ouroboros (its settings and log), and Spend (spending and the engine process).
- **⋮**: everything above by name, plus Settings, Help and Restart.

Click a button again to close its pane. Drag the line between the middle and the pane to resize it, or press **⤢** to give the pane the whole width.
"""
    },
    {
        "id": "improve",
        "icon": "♻️",
        "title": "Self-Improve: Foundry upgrades itself",
        "subtitle": "One click: self-learning, self-patching, self-updating harness",
        "view_target": "improve",
        "keywords": ["improve", "self", "self-improve", "learning", "patch", "update", "harness", "evolve", "undo", "rollback", "memory", "history", "budget", "build", "patch"],
        "content": """
## ♻️ Self-Improve (`improve` View)

**Improve**, at the top of the sidebar, replaces the old Build and Patch tabs with one autonomous loop that improves **Foundry itself**: the harness that plans, tests and writes code.

---

### What one round does
1. **Health check**: runs Foundry's type-check and its full test suite.
2. **Learns from evidence**: reads recent warnings and errors in `.foundry.log` (failed model calls, bad JSON, patches that didn't apply, and so on) plus the history of every earlier round.
3. **Patches itself**: the tri-model swarm designs one focused improvement to `src/`, writes a regression check for it (`tests/self-checks/*.check.ts`), and converges on a patch using the normal Architect → Auditor → Synthesizer loop.
4. **Verifies**: the change is kept only if the type-check **and the full test suite** still pass. It may not edit existing tests or touch anything outside `src/` and `tests/`.
5. **Updates itself**: Foundry is rebuilt with the change, so the next run already uses the improved engine.
6. **Remembers**: every round, kept or not, is written to `.foundry/journal.json` with what changed, what failed and why. The next round reads it, so improvements stack up and dead ends aren't repeated.

---

### Controls
- **What to improve (optional)**: type a focus such as "make JSON parsing of model output more reliable", or leave it empty (or write "improve yourself"). With no real focus the round **scouts first**: Claude proposes up to three small, checkable improvements from the evidence (the Diagnostics card, TODO notes, the source map, the history), Gemini picks the one most likely to be finished correctly, and that becomes the round's focus and its name in the history. Two short calls, instead of a whole budget spent wandering.
- **Diagnostics**: the card at the top; see the Diagnostics topic.
- **Improvements to try in a row**: 1, 3 or 5 rounds. It stops early after two rounds in a row that don't pass.
- **Spending limit**: the whole session stops before it spends more than this. Improvements that already passed are kept.
- **↶ Undo last improvement**: restores the files from before the most recent kept improvement, rebuilds, and records that you rejected it so it isn't redone.
- **Stop**: kills the run; the unfinished round's changes are rolled back automatically (also after a crash or reboot, on the next run).
- **Unfinished rounds are not wasted**: when a round runs out of budget, doesn't converge, or breaks a test, its best partial code, contract, check and failure reason are saved in `.foundry/carryover/` before the rollback. The next round with the same focus (or no focus) restores that code and finishes it instead of starting over. If those files changed in the meantime (for example another improvement was kept), the old code is only used as a reference, never written over newer code. A line of work is dropped after two failed continuations; **Start fresh instead** drops it right away.

### Command line
`node dist/index.js self-improve [--focus "..."] [--rounds 3] [--budget-usd 5]`, plus `--undo` and `--recover`.
"""
    },
    {
        "id": "projects",
        "icon": "🚀",
        "title": "Projects: build or improve any folder",
        "subtitle": "+ New project opens a chat with the models; Build makes it",
        "view_target": "projects",
        "keywords": ["projects", "go", "build", "new project", "folder", "spec", "target", "budget", "spending limit", "sidebar",
                     "list", "chat", "send", "launch", "launcher", "icon", "logo", "shortcut"],
        "content": """
## 🚀 Projects

Uses Foundry on a folder other than Foundry itself. A project's page works like a Claude session: the conversation and everything Foundry built scroll by, oldest first, with the message box at the bottom.

1. Click **+ New project** in the sidebar (or click an existing project in the list under it).
2. **Type what you want** in the box at the bottom (or pick an example).
3. Press **Send** (or Enter). Claude asks what's unclear and suggests a plan. Shift+Enter starts a new line.
4. When it sounds right, say so ("go ahead", "build it"): **Claude starts the Build itself**, within the Build limit. It shows as a Build card, with its progress right below it. While Claude answers or a Build runs, the Send button is **■ Stop**. (To build without waiting for Claude, use the command search, Ctrl K: "Build What We Discussed".)

Under the box, on the right: a live **token counter** for the chat (↑ tokens sent, ↓ tokens received including thinking, and the cost), which counts up while a reply is written and shows the Build's tokens while one runs. On the left: who answers when you press Send (Claude, Gemini, OpenAI, or all three in turn), **Thinking** (how hard the models think: Low, Medium, High, Extra high or Max; higher is more careful and slower, and costs more; it applies to chats, Builds and Improve, and Improve has the same setting), the project's folder (a new project gets a fresh folder under `FoundryProjects` in your home folder; before its first message you can pick an empty folder or one that already has code), and the **Build limit**. The **+** in the box adds a file to your message.

Every project is a separate chat, like separate Claude sessions: switch to another project at any time, and each page keeps its own conversation, half-typed message and live Build. One Build (or Improve round) runs at a time; you can keep chatting in other projects meanwhile. A spinning ✻ in the sidebar shows which project is building.

**The models can use the terminal, with your OK.** A model can propose a command, such as running the tests or listing the files; it shows as a card with **▶ Run**. Nothing runs until you click it. It then runs in the project's folder in its own Terminal tab, its output goes back into the chat, and the model carries on from there. A command's output stays out of what Build hands to Foundry.

**Launch**, next to the project's name at the top, starts the program: a `launch` script, `npm start`, `main.py`, `cargo run`, `go run`, an `index.html`, or whatever `.foundry/launcher.json` says. If there is nothing to launch yet, the button reads **Make a launcher**: it asks Claude to add a one-click launch script, an app icon (logo), a desktop shortcut and `launcher.json`, and to build them.

Claude's replies appear as they are written. If a reply fails, **↻ Try again** under it asks again without retyping. A new project's folder is named after its first message (for example `command-line-weather-app`), and the project appears in the sidebar's list as soon as you send that message. Each folder keeps its conversation in `.foundry/chat.json` and its runs in `.foundry/journal.json`, so later chats and Builds build on the earlier work. Right-click a message to copy it; right-click a project in the list to show its folder or remove it from the list (its files are never touched).

Command line: `node dist/index.js go --task "..." --target <folder> --budget-usd 5`, and `node dist/index.js chat --conversation <file.json> --agent all --target <folder>`.
"""
    },
    {
        "id": "chat",
        "icon": "💬",
        "title": "Chatting with the models ('The 3')",
        "subtitle": "Every project has a chat with Claude, Gemini and OpenAI",
        "view_target": "projects",
        "keywords": ["chat", "the 3", "studio", "prompt", "collaborate", "context", "debate", "talk", "send", "cost"],
        "content": """
## 💬 Chatting with the models ('The 3')

The chat lives in each project's page (+ New project, or a project in the sidebar). It makes real model calls, so each reply costs a little; the cost shows under the reply, and Overview (◨) adds it up as "Chat here". Each reply is capped by the chat reply limit (Settings › Spending limits; $1 to start).

---

### Who answers (the menu under the message box)
- **Claude · architect**: pins down what you want and proposes a plan. The default.
- **Gemini · auditor**: points out edge cases, risks, and what the tests should check.
- **OpenAI · synthesizer**: suggests how to build it: structure, language, libraries. Without an OpenAI key, Claude stands in.
- **All three**: Claude, then Gemini, then OpenAI, each seeing the replies before it.

The models know the project's folder and what earlier Builds did. When you press **Build**, Foundry gets everything said since the last Build, and your latest messages win where they disagree.

### Adding files
In the Editor (✎ at the top right), **Add Context** puts the open file into the message box, so you can ask about it.
"""
    },
    {
        "id": "prompts",
        "icon": "✎",
        "title": "Prompt studio: + New prompt",
        "subtitle": "Write and refine prompts with the models; each conversation is saved and remembered",
        "view_target": "prompts",
        "keywords": ["prompt", "prompts", "studio", "new prompt", "prompt engineering", "memory", "saved", "copy", "use in a new project",
                     "job", "job plan", "steps", "break down", "queue", "start this job"],
        "content": """
## ✎ Prompt studio

**+ New prompt** in the sidebar opens a chat whose product is a prompt, in one of two modes (the **For:** menu under the message box):

### Foundry job (the default)
For a job that is too big or too vague for one Build. Say what you want made; Claude, who knows how Foundry builds (one implementation file plus one test file per module, tests written first, mutation testing, no network in tests, the 12,000-character request cap, one Build at a time), splits it into an ordered list of steps, each a complete Build request with its own Thinking level and spending limit, so the whole job runs faster and cheaper than one sprawling Build. The plan shows as a **Job plan** card:
- **▶ Start this job**: runs the steps as Builds, one after another, in a new project or one from your list. Each step sets its Thinking level and Build limit, and shows as a line in the project's chat. A step that doesn't finish pauses the job: tell Claude what to fix, then **Continue job** (or **Skip step**).
- **Send to project**: puts one step into a project's message box, settings included, for you to edit or send.
- **Folder: none ⌄** (under the box): attach a folder. The models then see what is in it (new or existing project, its earlier runs) and can propose commands that run there once you click **Run**, so a plan can be built from the real files; **Start this job** runs the plan in that folder. The folder is saved with the conversation, and AI-access rules apply.
- Switch the model under the box to **Gemini** for a review of the plan (steps that are too big, untestable, out of order) or **OpenAI** for a different split.

### Any model
A prompt for Claude, GPT, Gemini, an image or voice model, or a coding agent. Claude asks what's missing, then writes the prompt in a **Prompt** card with **Copy** and **Use in a new project**; Gemini critiques, OpenAI offers an alternative.

The **Prompts** pane on the right (✧ at the top right) lists every saved conversation, newest first, and shows the open one's working prompt or job plan. Right-click a conversation to rename or delete it. Each conversation is the prompt's **memory**: reopen it a week later and the models still have everything you told them. Conversations live in Foundry's folder under `.foundry/prompts/`. Replies cost like any chat reply, within the chat reply limit.
"""
    },
    {
        "id": "context",
        "icon": "◔",
        "title": "Context: what the models see",
        "subtitle": "How full the conversation window is, and what goes into each reply",
        "view_target": "context",
        "keywords": ["context", "window", "tokens", "characters", "what the models see", "memory", "transcript", "cap"],
        "content": """
## ◔ Context

The **Context** pane (◔ at the top right) is Foundry's version of the context view in Claude Code. For the page you are on it shows:

- **Conversation window**: how much of the 60,000-character window the conversation uses, and roughly how many tokens each reply sends. When the window is full, the oldest messages drop out (the pane says how many).
- **What the models see**: the fixed instructions (roles, how to propose a command, how Claude starts a Build), the project notes (new or existing project, and up to 10 earlier runs from the folder's history), the conversation itself, attached files and command results.
- **What the next Build gets**: the messages since the last Build (command output is left out), against the 12,000-character cap.
- **Settings that apply**: who answers, the Thinking level, the chat reply limit, the Build limit, and the models in use.

On the Improve page it shows the evidence a round starts from: the health check, the log problems, the history and the source map.
"""
    },
    {
        "id": "files",
        "icon": "✎",
        "title": "Files: the whole computer, with AI access control",
        "subtitle": "Browse any drive; right-click a folder to allow or block Foundry's models",
        "view_target": "explorer",
        "keywords": ["files", "explorer", "drive", "folder", "access", "block", "allow", "private", "permission", "right-click"],
        "content": """
## ✎ Files

The **Files** pane (✎ at the top right) shows the whole computer: the project you're working on first, then Foundry's folder, your home folder, and every drive. Folders open on click; files open in the **Editor** tab next to it.

**Right-click a folder** for:
- **Block AI access** / **Allow AI access**: whether Foundry's models may read, change or run anything there. Blocked folders show **⊘**. Everything is allowed unless you block it, and the most specific rule wins, so you can block `Documents` and allow `Documents\\my-app`. The engine honours this too: a Build, chat or command in a blocked folder is refused, and blocked subfolders of a project are never read. Rules live in `.foundry/access.json` and are listed in Settings › AI access.
- **Open as a project**, **Open a terminal here**, **New file here…**, **Show in Explorer**.

Foundry's own folder can't be blocked; Improve needs it.
"""
    },
    {
        "id": "skills",
        "icon": "✦",
        "title": "Skills: instructions the models follow",
        "subtitle": "SKILL.md files for naming, tests, security, languages, prompts; enable them in Settings › Skills",
        "view_target": "settings",
        "keywords": ["skills", "skill", "SKILL.md", "instructions", "rules", "style", "enable", "add a skill", "clean code", "test first"],
        "content": """
## ✦ Skills

A skill is a folder under Foundry's `skills/` with a `SKILL.md`: a short header (`name`, `description`) and the instructions the models follow when the skill applies. Enabled skills are added to every model call in chats, the prompt studio and Builds (Improve rounds keep their own rules).

**Settings › Skills** lists them with a switch each, **Open** (edits the file in the Editor), **Remove**, **New skill…** (a template to fill in), **Add a folder…** / **Add a file…** (bring in a skill from elsewhere), and **Show folder**. The page says how many characters the enabled set adds to each call; keep it small, since you pay for it on every reply.

Built in: clean-code, test-first, error-handling (enabled to start), security-review, cli-design, python-style, typescript-style, readme-and-docs, performance, data-files, refactoring, prompt-engineering, git-hygiene. Edit them freely; they are plain text. The **Context** pane shows the skills line for the page you're on.
"""
    },
    {
        "id": "editor",
        "icon": "📝",
        "title": "Integrated Code & Spec Editor",
        "subtitle": "Syntax-aware code editor with direct swarm integration",
        "view_target": "editor",
        "keywords": ["editor", "code", "edit", "save", "spec", "syntax", "file"],
        "content": """
## 📝 Integrated Code & Spec Editor (`editor` View)

A responsive, syntax-highlighted editor directly wired into the Foundry autonomous pipeline.

---

### 🌟 Key Features
- **File Explorer Binding**: Open **Files** with the ✎ button at the top right; clicking any file there opens it in the **Editor** tab next to it. Files shows the whole computer, the project first (see the Files topic).
- **Set as Build Spec**: One-click button to declare the current markdown file as the active build specification (`spec.md`) for the Autonomous Build Engine.
- **Add Context**: Puts the open file into the project chat's message box, so you can ask the models about it.
- **Disk Synchronization**: Hotkey `Ctrl+S` or the Save button writes changes directly to disk and triggers an automatic background LSP diagnostics update.
"""
    },
    {
        "id": "ast",
        "icon": "🧬",
        "title": "Code map: the files and what they export",
        "subtitle": "Under Checks: every source file in the folder on screen and the names it declares",
        "view_target": "ast",
        "keywords": ["ast", "code map", "symbols", "exports", "files", "functions", "classes", "tree-sitter", "semantic patch", "call graph"],
        "content": """
## 🧬 Code map

**Checks › Code map** lists the source files in the folder on screen (the open project, or Foundry itself) and the names each one exports or declares at top level: functions, classes, interfaces, types, enums and constants in TypeScript and JavaScript, top-level `def` and `class` in Python, exported `func` and `type` in Go, `pub` items in Rust. Click a file to open it in the Editor; the filter box narrows the list by file or symbol name; **Rescan** reads the folder again.

What it is: a scan of top-level declarations with regular expressions, done in this window. What it is not: a parse, and not a call graph. It tells you what each file declares, not who calls it.

The engine's own view of code is different and lives in the engine process: Tree-sitter parses files into syntax trees, the patches the models write are applied to syntax-tree nodes by byte offset (`REPLACE_FUNCTION`, `REPLACE_CLASS`, `INSERT_AFTER`, `UPSERT_IMPORT` …), and a call graph of the workspace is summarized into the models' context before a module is designed. None of that is written to disk for this window to show; the Memory page shows the patches that won.
"""
    },
    {
        "id": "lsp",
        "icon": "🩺",
        "title": "Diagnostics: Foundry's real health",
        "subtitle": "The type-check, the test suite and the log, on the Improve page",
        "view_target": "improve",
        "keywords": ["lsp", "diagnostics", "typecheck", "tests", "health", "log", "warnings", "errors", "fix this", "check now", "auto-patch"],
        "content": """
## 🩺 Diagnostics

The **Diagnostics** card at the top of the Improve page shows Foundry's real health: whether its type-check passes, whether its full test suite passes, and the recent warnings and errors from its engine log (`.foundry.log`). Every Improve round refreshes it; **Check now** refreshes it on its own, without any AI call (it runs `self-improve --check`).

**Fix this** next to a problem puts it in the "What to improve" box as the round's focus. Press **Improve Foundry** and the round works on exactly that.

The older Diagnostics tab under Checks showed sample data, not real problems; it is gone. If you ever ran Improve on "TS6133 in semantic_patcher.ts" or "TS2304 in engine.test.ts", those came from the samples.

The right-hand **Context** pane (◔) shows the same evidence from the models' side: what a round starts from.
"""
    },
    {
        "id": "git",
        "icon": "🌿",
        "title": "Git Timeline & Rollback Engine",
        "subtitle": "Micro-commit version control and automatic rollback upon test failure",
        "view_target": "git",
        "keywords": ["git", "timeline", "commit", "rollback", "branch", "history", "vcs"],
        "content": """
## 🌿 Git Timeline & Rollback Engine (`git` View)

Foundry treats Git as an immutable, transactional rollback ledger.

---

### ⚡ Transactional Safety
- **Branch Sandboxes**: Each MCTS candidate branch (Alpha, Beta, Gamma) operates in its own isolated Git working tree.
- **Atomic Rollbacks**: If a synthesized patch fails mutation testing or produces LSP errors, the branch is rolled back instantaneously to the last known green commit.
- **Convergence Commits**: When an iteration successfully converges, Foundry creates an annotated micro-commit with telemetry metadata (fitness score, token spend, mutation kill ratio).
"""
    },
    {
        "id": "playground",
        "icon": "⚔️",
        "title": "Arena: one question to all three models",
        "subtitle": "Their real replies and what each one cost, side by side",
        "view_target": "playground",
        "keywords": ["playground", "arena", "compare", "models", "claude", "gemini", "openai", "cost", "ask all three"],
        "content": """
## ⚔️ Arena

Type a question and press **Ask all three** (Ctrl+Enter). The page runs the engine's own chat command with the agent set to all three, the same way a project chat set to “All three” does, so the replies come from the same models, keys and thinking level, under the same per-run spending limit (**Settings › Spending limits › chat**). Each reply shows the model that answered and what the reply cost; the line under the question shows the total.

- The engine asks the models **in order**: Claude first, then Gemini, who sees Claude's reply, then OpenAI, who sees both. It is a conversation about one question, not three isolated runs. Without an OpenAI key, Claude stands in for OpenAI and the model line says so.
- Without keys the page refuses before anything runs, and nothing is spent. In offline mode (**Settings › Builds**) the engine answers with deterministic stand-ins, labeled as such.
- Each run is logged in `.foundry.log` (Terminal › Log) like a chat; it is not saved as a conversation.
"""
    },
    {
        "id": "sandbox",
        "icon": "🧪",
        "title": "Tests: run the folder's tests and read the result",
        "subtitle": "Under Checks: the test command of the folder on screen, run in the Terminal pane",
        "view_target": "sandbox",
        "keywords": ["sandbox", "docker", "tests", "vitest", "jest", "pytest", "unittest", "cargo test", "go test", "npm test", "container", "isolation"],
        "content": """
## 🧪 Tests

**Checks › Tests** finds the test command of the folder on screen (the open project, or Foundry itself): `npm test` from package.json's test script, `cargo test`, `go test ./...`, `python -m pytest` or `python -m unittest discover`. You can edit the command before pressing **Run tests**. It runs in its own tab of the **Terminal pane** (**Show terminal** takes you there), and when it ends the page reads the runner's own summary: how many tests passed, failed and were skipped, which ones failed, the exit code and the last lines of output. It recognizes vitest, Jest, pytest, unittest, cargo and go output; a run that prints no summary it recognizes is reported exactly that way, with its exit code.

For Foundry itself the command is `npm test` (vitest, the engine's suite). The window's own tests run with `python -m unittest discover -s gui/tests`; type that in to run them.

### 🐳 Docker
The Docker card reports what the `docker` command says: whether Docker is installed, whether its daemon is running, and whether the sandbox image (**Settings › Builds**, default `foundry-sandbox:latest`) is built. This matters for Builds: the engine runs the tests it generates inside that image, with no network, only when the image exists; otherwise they run directly on this machine without isolation, and the engine says so in its log. The tests you run from this page always run on this machine, in the Terminal.
"""
    },
    {
        "id": "mcts",
        "icon": "🌳",
        "title": "Search tree: what the three branches did",
        "subtitle": "Under Engine internals: each module's Alpha, Beta and Gamma, from the engine log",
        "view_target": "mcts",
        "keywords": ["mcts", "search tree", "tree", "branches", "alpha", "beta", "gamma", "convergence", "pruned", "iterations", "log"],
        "content": """
## 🌳 Search tree

For every module of a Build or an Improve round, the engine designs, tests and codes **three candidate branches** (Alpha, Beta and Gamma), prunes the ones that fall behind, and keeps the first whose tests pass, whose diagnostics are clean and whose tests kill enough mutants. **Engine internals › Search tree** shows that for the latest run, or for an earlier run picked from the list, read from the engine log (`.foundry.log`):

- the run: Build, Improve or Self-improve, its folder, spending limit, what it spent, and the engine's own closing line;
- each planned module: converged on which branch after how many iterations, did not converge (with the reason and cost), skipped, or in progress;
- each branch: converged, pruned (with the reason the engine gave: a truncated reply, failed test generation, fitness too far behind the leader), or no event logged; plus mutation-test results, test regeneration, model fallbacks and retries.

While Foundry is running, the page re-reads the log every two seconds. Each branch row also shows the branch's latest logged step: its number, fitness score, LSP error count, mutation kill rate (once the tests pass) and whether the tests passed, exactly as the engine wrote them to the log. Runs logged by an engine built before it wrote those lines show the branch's state only. Before the first run the page says **No run yet**.
"""
    },
    {
        "id": "memory",
        "icon": "🧠",
        "title": "Memory: what the engine remembers",
        "subtitle": "Under Engine internals: the episodes in the SQLite memory file, with the patches that won",
        "view_target": "memory",
        "keywords": ["memory", "sqlite", "vector", "embeddings", "episodic", "episodes", "patches", "lessons", "history", "delete"],
        "content": """
## 🧠 Memory

Every time a module converges in a Build or an Improve round, the engine stores one **episode** in its SQLite memory file (`FOUNDRY_SQLITE_PATH`, default `.foundry-memory.sqlite` in the Foundry folder): the module and language, what the run had to get past (the failure pattern), the stack trace it saw, and the semantic patches that made the module converge. The pattern is embedded (Gemini's embedding model) and, before the architect designs something similar or the synthesizer fixes a similar failure, the nearest episodes are retrieved into the models' context.

**Engine internals › Memory** lists exactly what is in that file, newest first: filter by module, language or problem; click an episode to read its pattern, stack trace, embedding model and dimensions, and each winning patch (action, file, target and the code). Until the first module converges there is no file, and the page says so. **Delete all…** empties the file after confirmation; the engine then finds no earlier fixes until new episodes are stored.
"""
    },
    {
        "id": "ouroboros",
        "icon": "♾️",
        "title": "Ouroboros: the engine's self-optimization",
        "subtitle": "Under Engine internals: its settings, what it logged, what it staged, and the spending freeze file",
        "view_target": "ouroboros",
        "keywords": ["ouroboros", "self-healing", "self-patch", "bottleneck", "lag", "heap", "parse time", "rebuild", "relaunch", "freeze", "circuit breaker"],
        "content": """
## ♾️ Ouroboros

Ouroboros is the engine module (`src/singularity/ouroboros.ts`) that watches the engine's **own** performance while a run goes on: the mean lag of its event loop, how long one AST parse takes, and how much heap it uses. When one of them passes its limit and self-modification is on, it asks the architect model for a performance patch to a specific file of Foundry's own source, applies it, and gates it on the type-check, the full test suite and a complete build. A patch that passes is **staged**: the originals are kept under `.foundry-ouroboros/` and the new code takes effect on the next run. A patch that fails is reverted and Foundry is rebuilt from the originals. It never restarts a run in progress.

**Engine internals › Ouroboros** shows:
- **Settings** it runs with (`OUROBOROS_AUTO_HEAL`, the three limits, `OUROBOROS_AUTO_RESTART`), as saved in **Settings › Improve**, with the engine's default marked when a setting is unset.
- **What it logged**: the `[Ouroboros]` lines of `.foundry.log`: bottlenecks detected, patches generated, staged, rejected or reverted. If none has happened, the page says so.
- **Staged self-modifications** found on disk, one folder per generation, with the files it kept.
- **The spending circuit breaker's freeze file** (`FOUNDRY_FREEZE_PATH`): written when a run stops at its spending limit, with the ledger of every paid model call. It is a record, not a lock.

The measurements themselves (lag, parse time, heap) live in the engine process during a run and are not reported to this window; this page never shows a gauge it cannot back with data. **Rebuild engine** runs `npm run build` in the Terminal pane; **Relaunch window** restarts this window.
"""
    },
    {
        "id": "settings",
        "icon": "⚙️",
        "title": "Settings",
        "subtitle": "Models, keys and providers, spending limits, Builds, Improve, AI access, terminal, shortcuts",
        "view_target": "settings",
        "keywords": ["settings", "config", "api key", "anthropic", "gemini", "openai", "provider", "deepseek", "openrouter", "groq",
                     "ollama", "env", "budget", "theme", "shell", "shortcuts", "access", "custom", "model"],
        "content": """
## ⚙️ Settings

Settings (bottom of the sidebar) is laid out like the Claude app: pages on the left, the page on the right, and every change saved to Foundry's `.env` the moment you make it. Only settings that something actually reads are shown.

---

- **General**: theme (Dark, Light, or match Windows), what opens on start, where new projects go, the chime, whether Stop asks first.
- **AI models**: the default Thinking level; the model and longest reply for the architect (Claude), the auditor (Gemini) and the code writer (the synthesizer). The code writer can be OpenAI or any provider you added.
- **API keys & providers**: the three built-in keys, each with **Save & Test** (which also fetches that provider's model list for the AI models page). **+ Add a provider** adds any OpenAI-compatible service or local server: DeepSeek, OpenRouter, Groq, Mistral, xAI, Together, Fireworks, Ollama, LM Studio, or your own endpoint. Give it a name, its endpoint, a model and a key (local servers need none), test it, and pick it as the code writer. Its per-million-token prices, if you enter them, feed the spending limit.
- **Spending limits**: the default Build limit, the Improve limit, the chat reply limit, and an optional per-part cap.
- **Builds**: attempts per part, test strength (mutation kill ratio), git commits, where tests run (this computer or the Docker sandbox), timeouts and retries.
- **Improve**: rounds per session, **Check now** (a free health check), and Ouroboros, Foundry's self-healing while it runs.
- **AI access**: the folders you blocked or allowed (also set by right-clicking a folder in the Files pane).
- **Terminal**: the shell new tabs run, font and size.
- **Keyboard shortcuts**: the list.
- **Advanced**: offline mode, the Node.js program, the memory database, the raw `.env` editor, export/import, reset, restart.
- **About**: version, folder, whether the engine build is up to date with the source, Node and Python versions.

Keys stay in `.env` on this computer and are only ever sent to their own provider.
"""
    },
    {
        "id": "hotkeys",
        "icon": "⌨️",
        "title": "Keyboard Shortcuts & Hotkeys",
        "subtitle": "Command Palette and productivity keybindings",
        "view_target": None,
        "keywords": ["hotkeys", "shortcuts", "keyboard", "ctrl+k", "ctrl+p", "commands", "palette"],
        "content": """
## ⌨️ Keyboard Shortcuts & Productivity Hotkeys

Foundry includes full keyboard accessibility and instant command execution.

---

### 🎯 Global Hotkeys
- **`Ctrl + K` or `Ctrl + P`**: Open the **Command Palette**: every command and page by name.
- **`Ctrl + S`** (in Editor): Save active file to disk and trigger background LSP sweep.
- **`Enter`** (in a project's message box): Send the message to the chosen model(s). **`Shift + Enter`** starts a new line.

---

- **`Ctrl + B`**: Hide or show the sidebar.
- **`Ctrl + N`**: Start a new project. **`Ctrl + Shift + N`**: a new prompt.
- **`F1`**: Open this help.

---

### 🔘 Panels
- The **buttons at the top right** open the right-hand pane: ◨ Overview, ◔ Context, ✧ Prompts, ›_ Terminal, ✎ Files, ± Changes, ✓ Checks, ✦ Arena, ◎ Engine internals, ⋮ everything by name. Click the same button again to close it.
- **⌕** at the bottom of the sidebar opens the Command Palette if you prefer clicking to the keyboard.
"""
    },
    {
        "id": "faq",
        "icon": "❓",
        "title": "Troubleshooting & FAQs",
        "subtitle": "Solutions to common setup issues, API errors, and budget freezes",
        "view_target": None,
        "keywords": ["faq", "troubleshooting", "error", "404", "401", "429", "freeze", "offline", "help"],
        "content": """
## ❓ Frequently Asked Questions & Troubleshooting

---

### Q: Why did Anthropic return HTTP 404 or HTTP 400?
- **HTTP 404**: Occurred when older deprecated 2024 model IDs were queried. In 2026, Anthropic uses `claude-fable-5-1`, `claude-sonnet-5-5`, etc.
- **HTTP 400**: Anthropic's 5.x models deprecate the `temperature` parameter. Foundry automatically omits `temperature` for all 5.x and Fable models.

### Q: Are Astra and OpenAI keys the same?
**Yes!** Astra is OpenAI's code synthesis model line. In Foundry, `ASTRA_API_KEY` and `OPENAI_API_KEY` are unified. Entering your OpenAI API key powers both the OpenAI provider and the Astra Synthesizer.

### Q: What is a Token Circuit Breaker Freeze?
If a swarm session reaches your configured `MAX_BUDGET_USD` (default: $5.00), the economics engine immediately halts execution, saves the complete AST progress, token ledger, and context to `.foundry-freeze.json`, and stops to protect your budget. You can increase the budget ceiling in the **Settings** tab.

### Q: Can I run Foundry completely offline without API keys?
**Yes!** Toggle **Offline Deterministic Mode** in the Settings tab or select the **Offline / Air-Gapped** preset profile. Foundry will run local deterministic synthesis and connect to local Ollama endpoints (`http://127.0.0.1:11434`) with zero external API calls.
"""
    }
]

class HelpView(ctk.CTkFrame):
    """
    Comprehensive Interactive Help & User Manual View:
    - Categorized explanations for all 13 IDE views and Swarm agents
    - Real-time search filter across all help topics and keywords
    - Direct 'Jump to View' action buttons
    - Architecture diagrams, hotkey guides, and troubleshooting FAQs
    """

    def __init__(self, master, on_switch_view=None, **kwargs):
        super().__init__(master, corner_radius=10, fg_color=("gray95", "gray14"), **kwargs)
        self.on_switch_view = on_switch_view
        self.current_topic_id = "overview"

        self.grid_columnconfigure(0, weight=0)  # Left topic list
        self.grid_columnconfigure(1, weight=1)  # Right content viewer
        self.grid_rowconfigure(1, weight=1)

        self._create_header()
        self._create_topic_sidebar()
        self._create_content_area()

        # Display initial topic
        self._display_topic("overview")

    def _create_header(self):
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 10))
        hdr.grid_columnconfigure(1, weight=1)

        title_box = ctk.CTkFrame(hdr, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(title_box, text="❓ Foundry Knowledge Base & User Manual", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="How Foundry works, page by page",
            font=ctk.CTkFont(size=12),
            text_color="gray60"
        ).pack(anchor="w")

        # Search Bar
        search_box = ctk.CTkFrame(hdr, fg_color="transparent")
        search_box.grid(row=0, column=2, sticky="e")

        self.search_entry = ctk.CTkEntry(
            search_box,
            placeholder_text="🔍 Search help (e.g. fable, mcts, patch, astra, budget, hotkeys)...",
            width=340,
            height=34,
            font=ctk.CTkFont(size=12)
        )
        self.search_entry.pack(side="left", padx=4)
        self.search_entry.bind("<KeyRelease>", self._on_search)

        btn_clear = ctk.CTkButton(search_box, text="Clear", width=55, height=34, fg_color="gray30", hover_color="gray20",
                                  command=self._clear_search)
        btn_clear.pack(side="left", padx=2)

    def _create_topic_sidebar(self):
        self.sidebar_frame = ctk.CTkScrollableFrame(self, width=280, corner_radius=8, fg_color=("gray90", "gray17"))
        self.sidebar_frame.grid(row=1, column=0, sticky="nsew", padx=(20, 10), pady=(0, 16))

        self.topic_buttons = {}
        self._render_topic_list(HELP_TOPICS)

    def _render_topic_list(self, topics):
        # Clear existing buttons
        for btn in self.topic_buttons.values():
            btn.destroy()
        self.topic_buttons.clear()
        if getattr(self, "lbl_no_topics", None) is not None:
            self.lbl_no_topics.destroy()
            self.lbl_no_topics = None

        if not topics:
            self.lbl_no_topics = ctk.CTkLabel(self.sidebar_frame, text="No topics match search.", font=ctk.CTkFont(size=12), text_color="gray60")
            self.lbl_no_topics.pack(pady=20)
            return

        for topic in topics:
            btn = ctk.CTkButton(
                self.sidebar_frame,
                text=f"{topic['icon']}  {topic['title']}",
                anchor="w",
                height=38,
                font=ctk.CTkFont(size=12, weight="bold" if topic["id"] == self.current_topic_id else "normal"),
                fg_color=["#3B8ED0", "#1F6AA5"] if topic["id"] == self.current_topic_id else "transparent",
                text_color=("gray10", "gray85"),
                hover_color=("gray75", "gray25"),
                command=lambda tid=topic["id"]: self._display_topic(tid)
            )
            btn.pack(fill="x", pady=2, padx=4)
            self.topic_buttons[topic["id"]] = btn

    def _create_content_area(self):
        self.content_container = ctk.CTkFrame(self, corner_radius=8, fg_color=("gray90", "gray17"))
        self.content_container.grid(row=1, column=1, sticky="nsew", padx=(0, 20), pady=(0, 16))
        self.content_container.grid_columnconfigure(0, weight=1)
        self.content_container.grid_rowconfigure(1, weight=1)

        # Content Card Header
        self.card_header = ctk.CTkFrame(self.content_container, fg_color="transparent")
        self.card_header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        self.card_header.grid_columnconfigure(0, weight=1)

        self.lbl_topic_title = ctk.CTkLabel(self.card_header, text="", font=ctk.CTkFont(size=18, weight="bold"))
        self.lbl_topic_title.grid(row=0, column=0, sticky="w")

        self.lbl_topic_sub = ctk.CTkLabel(self.card_header, text="", font=ctk.CTkFont(size=12), text_color="gray60")
        self.lbl_topic_sub.grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.btn_jump_view = ctk.CTkButton(
            self.card_header,
            text="🚀 Open This View",
            height=32,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#007AFF",
            hover_color="#0062CC",
            command=self._on_jump_to_view
        )
        self.btn_jump_view.grid(row=0, column=1, rowspan=2, sticky="e", padx=4)

        # Scrollable Content Text
        self.content_box = ctk.CTkTextbox(
            self.content_container,
            font=ctk.CTkFont(family="Segoe UI", size=13),
            wrap="word",
            corner_radius=6,
            fg_color=("gray98", "#0C0D10")
        )
        self.content_box.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))

    def _display_topic(self, topic_id):
        self.current_topic_id = topic_id
        topic = next((t for t in HELP_TOPICS if t["id"] == topic_id), None)
        if not topic:
            return

        # Update sidebar highlight
        for tid, btn in self.topic_buttons.items():
            if tid == topic_id:
                btn.configure(fg_color=["#3B8ED0", "#1F6AA5"], text_color=("gray10", "gray85"), font=ctk.CTkFont(size=12, weight="bold"))
            else:
                btn.configure(fg_color="transparent", text_color=("gray10", "gray85"), font=ctk.CTkFont(size=12, weight="normal"))

        # Update card header
        self.lbl_topic_title.configure(text=f"{topic['icon']} {topic['title']}")
        self.lbl_topic_sub.configure(text=topic["subtitle"])

        # Update jump button
        if topic.get("view_target") and self.on_switch_view:
            self.btn_jump_view.configure(text=f"🚀 Open {topic['title'].split('(')[0].strip()}", state="normal")
            self.btn_jump_view.grid()
        else:
            self.btn_jump_view.grid_remove()

        # Render content text cleanly
        self.content_box.configure(state="normal")
        self.content_box.delete("1.0", "end")

        clean_text = topic["content"].strip()
        self.content_box.insert("end", clean_text)
        self.content_box.configure(state="disabled")
        self.content_box.yview_moveto(0)

    def _on_jump_to_view(self):
        topic = next((t for t in HELP_TOPICS if t["id"] == self.current_topic_id), None)
        if topic and topic.get("view_target") and self.on_switch_view:
            self.on_switch_view(topic["view_target"])

    def _on_search(self, event=None):
        query = self.search_entry.get().strip().lower()
        if not query:
            self._render_topic_list(HELP_TOPICS)
            return

        filtered = []
        for t in HELP_TOPICS:
            match = False
            if query in t["title"].lower() or query in t["subtitle"].lower():
                match = True
            elif any(query in kw.lower() for kw in t.get("keywords", [])):
                match = True
            elif query in t["content"].lower():
                match = True

            if match:
                filtered.append(t)

        self._render_topic_list(filtered)
        if filtered and self.current_topic_id not in [f["id"] for f in filtered]:
            self._display_topic(filtered[0]["id"])

    def _clear_search(self):
        self.search_entry.delete(0, "end")
        self._render_topic_list(HELP_TOPICS)
        self._display_topic("overview")
