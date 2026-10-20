"""A project's chat with the models: stored in <project>/.foundry/chat.json, answered by `foundry chat`.

Messages are {"role": "user" | "assistant" | "build" | "notice", "text", "timestamp", plus "agent", "model" and
"spentUsd" on replies}. "build" marks where Build was pressed, so the next Build only uses what was said since.
The engine reads only the user and assistant messages."""
import json
import os
import queue
import subprocess
import threading
from datetime import datetime, timezone

PROGRESS_PREFIX = "@@FOUNDRY "

AGENTS = [  # (engine name, label in the menu)
    ("claude", "Claude · architect"),
    ("gemini", "Gemini · auditor"),
    ("openai", "OpenAI · synthesizer"),
    ("all", "All three"),
]
AGENT_NAMES = {"claude": "Claude", "gemini": "Gemini", "openai": "OpenAI"}
AGENT_ROLES = {"claude": "architect", "gemini": "auditor", "openai": "synthesizer"}
SPEAKER_LABELS = {"claude": "Claude (architect)", "gemini": "Gemini (auditor)", "openai": "OpenAI (synthesizer)"}
ROLE_KEYS = (("FOUNDRY_ARCHITECT", "architect", "claude"), ("FOUNDRY_AUDITOR", "auditor", "gemini"), ("FOUNDRY_WRITER", "code writer", "openai"))
PROVIDER_CHOICES = ("claude", "gemini", "openai")


def role_mapping(config_manager):
    """{"architect": "claude", "auditor": "gemini", "code writer": "openai"} from the settings."""
    out = {}
    for key, role, default in ROLE_KEYS:
        value = str(config_manager.get(key, default) or default).strip().lower()
        out[role] = value if value in PROVIDER_CHOICES else default
    return out


def agent_roles(config_manager):
    """{"claude": "architect", "gemini": "auditor, code writer", "openai": ""} under the configured roles."""
    mapping = role_mapping(config_manager)
    return {agent: ", ".join(role for role, who in mapping.items() if who == agent) for agent in PROVIDER_CHOICES}


def refresh_roles(config_manager):
    """Re-labels the menus and headers after the roles changed in Settings (the lists are shared, so edit in place)."""
    roles = agent_roles(config_manager)
    for agent, text in roles.items():
        AGENT_ROLES[agent] = text or "helper"
        SPEAKER_LABELS[agent] = f"{AGENT_NAMES[agent]} ({AGENT_ROLES[agent]})"
    for idx, (name, _label) in enumerate(AGENTS):
        if name in roles:
            AGENTS[idx] = (name, f"{AGENT_NAMES[name]} · {AGENT_ROLES[name]}")
    return roles

MAX_TASK_TRANSCRIPT = 12000  # keeps `go --task` well under the Windows command-line limit


def missing_chat_keys(config_manager, agent):
    """The providers that still need a key before `agent` (claude, gemini, openai or all) can answer: [] in offline mode.
    OpenAI needs no key of its own: without one, Claude stands in for it (as the engine does)."""
    if str(config_manager.get("FOUNDRY_OFFLINE_MODE", "false")).lower() == "true" or \
            os.environ.get("FOUNDRY_OFFLINE_MODE", "").lower() == "true":
        return []
    get = lambda k: str(config_manager.get(k, "") or os.environ.get(k, "")).strip()
    missing = []
    if agent in ("gemini", "all") and not get("GEMINI_API_KEY"):
        missing.append("Google (Gemini)")
    if agent in ("claude", "all") and not get("ANTHROPIC_API_KEY"):
        missing.append("Anthropic (Claude)")
    if agent == "openai" and not get("ASTRA_API_KEY") and not get("OPENAI_API_KEY") and not get("ANTHROPIC_API_KEY"):
        missing.append("OpenAI")
    return missing


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def chat_path(folder):
    return os.path.join(folder, ".foundry", "chat.json")


def load_chat(folder):
    try:
        with open(chat_path(folder), "r", encoding="utf-8") as f:
            messages = json.load(f).get("messages", [])
        return [m for m in messages if isinstance(m, dict) and isinstance(m.get("text", ""), str)]
    except Exception:
        return []


def save_chat(folder, messages):
    path = chat_path(folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ignore = os.path.join(os.path.dirname(path), ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("*\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "messages": messages}, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def chat_spend(messages):
    return sum(float(m.get("spentUsd", 0) or 0) for m in messages)


def since_last_build(messages):
    """The user/assistant messages after the last Build."""
    start = 0
    for idx, m in enumerate(messages):
        if m.get("role") == "build":
            start = idx + 1
    return [m for m in messages[start:] if m.get("role") in ("user", "assistant")]


def compose_build_task(messages):
    """The `go --task` text for what was discussed since the last Build. Its first line is the user's first request,
    so the project list and history read naturally; the transcript follows."""
    # Command output is for the models' benefit in the chat; the Build gets the decisions.
    recent = [m for m in since_last_build(messages) if not m.get("tool")]
    user_messages = [m for m in recent if m.get("role") == "user"]
    if not user_messages:
        return ""
    if len(recent) == 1:
        return user_messages[0]["text"].strip()
    lines = []
    for m in recent:
        speaker = "User" if m["role"] == "user" else SPEAKER_LABELS.get(m.get("agent"), "Assistant")
        lines.append(f"{speaker}: {m['text'].strip()}")
    transcript = "\n\n".join(lines)
    if len(transcript) > MAX_TASK_TRANSCRIPT:
        transcript = "[Earlier messages left out]\n\n" + transcript[-MAX_TASK_TRANSCRIPT:]
    headline = " ".join(user_messages[0]["text"].split())[:160]
    return (f"{headline}\n\nThe user worked this out in a chat with Foundry's models (latest messages last; where they "
            f"disagree, the user's latest message wins):\n\n{transcript}")


class ChatProcess:
    """Runs `node dist/index.js chat ...` in the background and hands its progress events to the Tk thread."""

    def __init__(self, widget, cmd, cwd, env, on_event, on_exit):
        self.widget, self.cmd, self.cwd, self.env = widget, cmd, cwd, env
        self.on_event, self.on_exit = on_event, on_exit
        self.proc = None
        self._queue = queue.Queue()
        self._done = False

    def start(self):
        threading.Thread(target=self._worker, daemon=True).start()
        self._pump()

    def _worker(self):
        code = None
        try:
            self.proc = subprocess.Popen(
                self.cmd, cwd=self.cwd, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            for line in iter(self.proc.stdout.readline, ""):
                if line.startswith(PROGRESS_PREFIX):
                    try:
                        self._queue.put(("event", json.loads(line[len(PROGRESS_PREFIX):])))
                    except ValueError:
                        pass
            self.proc.stdout.close()
            code = self.proc.wait()
        except Exception as e:
            self._queue.put(("event", {"type": "chat-error", "agent": "claude", "problem": f"Couldn't start the engine: {e}"}))
        finally:
            self._queue.put(("exit", code))

    def _pump(self):
        try:
            while True:
                kind, value = self._queue.get_nowait()
                if kind == "event":
                    self.on_event(value)
                else:
                    self._done = True
                    self.on_exit(value)
                    return
        except queue.Empty:
            pass
        if self.widget.winfo_exists():
            self.widget.after(60, self._pump)

    def stop(self):
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    proc.terminate()
            except Exception:
                pass
