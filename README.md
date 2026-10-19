# Foundry

Foundry is an autonomous software-engineering engine with a desktop app. You describe what you want in plain English; three AI models plan, test and write the code, one module at a time, and keep a change only when its tests pass. Foundry can also improve its own engine the same way.

- **Claude** is the architect: it turns a request into a plan of modules, each one implementation file plus one test file.
- **Gemini** is the auditor: it writes the tests first, from the module's contract.
- **OpenAI** (or any OpenAI-compatible provider you add) is the code writer: it implements until the tests pass. Without an OpenAI key, Claude writes the code too.

A module passes only when its tests pass, catch at least 85% of deliberately broken copies of the code (mutation testing), and the language server reports no errors. Languages it can test: TypeScript, Python, Rust, Go, C++.

## Install

You need Node.js 20+ and Python 3.11+ (3.14 is what it is developed on). On Windows the terminal pane uses ConPTY, which needs Windows 10 or later.

```bash
npm install
npm run build
pip install -r gui/requirements.txt
```

Copy `.env.example` to `.env` and add your keys, or start the app and add them under **Settings › API keys & providers**. The `.env` file is yours; it is ignored by git.

## Run

```bash
python gui/main.py
```

(On Windows, `launch-gui.bat` does the same.) The window is laid out like the Claude desktop app: a sidebar on the left (**Improve**, **+ New project**, **+ New prompt**, your projects, **Settings**), the page you are on in the middle, and tool pages behind the icon buttons at the top right (Overview, Context, Prompts, Terminal, Files, Changes, Checks, Arena, Engine internals). **F1** opens the built-in help, which describes every page.

### Projects
**+ New project** opens a chat. Tell Claude what you want to make; when it sounds right, say "go ahead" and Claude starts the Build itself, within the spending limit under the message box. Each project is a separate chat that remembers its conversation and every run (`<project>/.foundry/`). Models can propose commands, which run only when you click **Run**. **Launch**, next to the project's name, starts the finished program.

### Improve
**Improve** runs the same loop on Foundry's own engine: it checks its health (type-check, test suite, recent log problems), scouts for one small, checkable improvement, builds it, and keeps it only if the full test suite still passes. Every round is remembered; **Undo** rolls one back.

### The prompt studio
**+ New prompt** opens a chat whose product is a prompt. In **Foundry job** mode, Claude splits a big job into Build-sized steps with a thinking level and budget each, and **Start this job** runs them one after another. In **Any model** mode it writes prompts for Claude, GPT, Gemini, image models or agents.

### Skills
`skills/<name>/SKILL.md` files hold instructions the models follow when they apply (clean code, test first, error handling, security review, a language's style, prompt writing...). Enable them, add your own or write new ones under **Settings › Skills**.

### Command line
The engine works without the app:

```bash
node dist/index.js go --task "A command-line to-do app" --target ./todo --budget-usd 5
node dist/index.js chat --conversation chat.json --agent all --target ./todo
node dist/index.js self-improve --rounds 1 --budget-usd 5
node dist/index.js self-improve --check     # health only, no AI calls
```

Add `--offline` to any command for a free, deterministic demo run.

## Costs and safety

Every run has a spending limit and stops before passing it; the Overview pane shows what was spent. Keys stay in `.env` on your computer and are sent only to their own provider. The **Files** pane lets you block the models from any folder (right-click → Block AI access); the engine refuses to work there.

## Development

```bash
npm test                                    # the engine (vitest)
python -m unittest discover -s gui/tests    # the app's helpers and terminal emulator
```

Engine source is `src/` (TypeScript, compiled to `dist/`), the app is `gui/` (Python, customtkinter). Run `npm run build` after changing `src/`; the app runs `dist/`.

## License

MIT; see `LICENSE`.
