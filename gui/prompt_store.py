"""Saved prompt conversations (the prompt studio behind + New prompt).

Each conversation is a folder under <foundry>/.foundry/prompts/<id>/ with meta.json (title, dates) and the
conversation in .foundry/chat.json inside it, the same layout a project uses, so the chat page machinery
is shared. The conversation is the prompt's memory: reopening it gives the models everything said before."""
import json
import os
import re
import secrets
from datetime import datetime, timezone

from project_chat import load_chat

MAX_TITLE = 48
STUDIO_MODES = [  # (engine --mode, label, what it is for)
    ("job", "Foundry job", "break a job into Build requests Foundry runs well, one after another"),
    ("prompt", "Any model", "a prompt for Claude, GPT, Gemini, an image model or an agent"),
]
DEFAULT_MODE = "job"
THINKING_VALUES = ("low", "medium", "high", "xhigh", "max")
TITLE_STOPWORDS = {"a", "an", "the", "please", "can", "you", "could", "would", "i", "want", "need", "me", "to", "for", "my",
                   "write", "make", "create", "help", "with", "that", "this", "some", "just", "of"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def prompts_root(root):
    return os.path.join(root, ".foundry", "prompts")


def meta_path(folder):
    return os.path.join(folder, "meta.json")


def load_meta(folder):
    try:
        with open(meta_path(folder), "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def save_meta(folder, meta):
    os.makedirs(folder, exist_ok=True)
    tmp = meta_path(folder) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    os.replace(tmp, meta_path(folder))


def new_prompt_folder(root):
    """A fresh conversation folder (created on disk only when the first message is sent)."""
    base = prompts_root(root)
    while True:
        folder = os.path.join(base, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}")
        if not os.path.exists(folder):
            return folder


def is_prompt_folder(root, folder):
    try:
        return os.path.normcase(os.path.abspath(os.path.dirname(folder))) == os.path.normcase(os.path.abspath(prompts_root(root)))
    except Exception:
        return False


def list_prompts(root):
    """[{"folder", "title", "createdAt", "updatedAt", "messages"}], newest first."""
    base = prompts_root(root)
    items = []
    if not os.path.isdir(base):
        return items
    for name in os.listdir(base):
        folder = os.path.join(base, name)
        if not os.path.isdir(folder):
            continue
        meta = load_meta(folder)
        chat = load_chat(folder)
        if not meta and not chat:
            continue
        items.append({"folder": folder, "title": meta.get("title") or "Untitled prompt", "createdAt": meta.get("createdAt", ""),
                      "updatedAt": meta.get("updatedAt") or meta.get("createdAt", ""), "mode": meta.get("mode", DEFAULT_MODE),
                      "messages": sum(1 for m in chat if m.get("role") in ("user", "assistant"))})
    return sorted(items, key=lambda item: item["updatedAt"], reverse=True)


def touch(folder, title=None, mode=None):
    """Creates or updates the conversation's meta.json; returns it."""
    meta = load_meta(folder)
    meta.setdefault("createdAt", now_iso())
    meta["updatedAt"] = now_iso()
    if title:
        meta["title"] = title
    if mode:
        meta["mode"] = mode
    meta.setdefault("title", "New prompt")
    meta.setdefault("mode", DEFAULT_MODE)
    save_meta(folder, meta)
    return meta


def set_title(folder, title):
    return touch(folder, title=title.strip()[:MAX_TITLE] or "Untitled prompt")


def delete_prompt(folder):
    import shutil
    shutil.rmtree(folder, ignore_errors=True)


def title_for(text):
    """A short title from the first message: "write me a prompt for summarizing meeting notes" -> "Summarizing meeting notes"."""
    first = (text.strip().splitlines() or [""])[0]
    # "Write me a prompt for X", "A system prompt that X", "I need a prompt to X" -> "X"
    first = re.sub(r"^\W*((write|make|create|draft|build|give|i need|i want|i'd like)( me)?( a| an)?|a|an)?\s*"
                   r"(\w+ )?prompt (for|that|to|which|about)\s+", "", first, flags=re.I)
    words = [w for w in re.findall(r"[A-Za-z0-9'][A-Za-z0-9'\-]*", first) if w.lower() not in TITLE_STOPWORDS] or first.split()
    title = " ".join(words[:7]).strip(" .,:;!?")
    if not title:
        return "New prompt"
    title = title[0].upper() + title[1:]
    return title[:MAX_TITLE].rstrip() + ("…" if len(title) > MAX_TITLE else "")


def latest_prompt(messages):
    """The newest ```prompt block a model wrote: the working prompt of the conversation, or ""."""
    return latest_block(messages, ("prompt",))[1]


def latest_block(messages, kinds=("prompt", "foundry")):
    """(kind, body) of the newest block of one of `kinds` a model wrote, or ("", "")."""
    from projects_view import split_code  # local import: projects_view imports this module's neighbours
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        blocks = [(kind, body) for kind, body in split_code(str(m.get("text", ""))) if kind in kinds]
        if blocks:
            return blocks[-1]
    return "", ""


def parse_job_plan(text):
    """A ```foundry block's JSON as {"job", "project", "language", "steps": [{"title", "thinking", "limit", "build", "optional"}],
    "notes"}, with every field normalized, or None when it isn't a usable plan."""
    raw = text.strip()
    if raw.lower().startswith("json"):  # a ```json fence whose tag ended up inside the body
        raw = raw[4:].lstrip()
    try:
        data = json.loads(raw, strict=False)  # models put raw newlines inside the build strings
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        return None
    steps = []
    for raw in data["steps"]:
        if not isinstance(raw, dict):
            continue
        build = str(raw.get("build") or "").strip()
        title = " ".join(str(raw.get("title") or build.splitlines()[0] if build else "").split())[:100]
        if not build or not title:
            continue
        thinking = str(raw.get("thinking") or "").strip().lower().replace(" ", "")
        thinking = "xhigh" if thinking == "extrahigh" else thinking
        try:
            limit = float(str(raw.get("limit", "")).strip().lstrip("$"))
        except ValueError:
            limit = 0.0
        steps.append({"title": title, "build": build, "thinking": thinking if thinking in THINKING_VALUES else "",
                      "limit": limit if limit > 0 else 0.0, "optional": bool(raw.get("optional"))})
    if not steps:
        return None
    project = str(data.get("project") or "new").strip().lower()
    return {"job": " ".join(str(data.get("job") or steps[0]["title"]).split())[:100],
            "project": project if project in ("new", "existing") else "new",
            "language": " ".join(str(data.get("language") or "").split())[:60],
            "steps": steps, "notes": " ".join(str(data.get("notes") or "").split())[:400]}
