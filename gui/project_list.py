"""The sidebar's project list: folders Foundry has built or improved, newest first.

Stored in <foundry>/.foundry/projects.json. Self-improvement never snapshots or rolls back `.foundry*`
entries, so the list survives a reverted round."""
import json
import os
from datetime import datetime, timezone

from engine_run_view import load_journal
from project_chat import load_chat

MAX_PROJECTS = 50


def _list_path(root):
    return os.path.join(root, ".foundry", "projects.json")


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def load_projects(root):
    """[{"path", "lastUsed"}], newest first. Folders that were deleted are still listed (the user can remove them)."""
    try:
        with open(_list_path(root), "r", encoding="utf-8") as f:
            items = json.load(f).get("projects", [])
        return [p for p in items if isinstance(p, dict) and isinstance(p.get("path"), str) and p["path"]]
    except Exception:
        return []


def _save(root, items):
    path = _list_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ignore = os.path.join(os.path.dirname(path), ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("*\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "projects": items[:MAX_PROJECTS]}, f, indent=2)
    os.replace(tmp, path)


def remember_project(root, folder):
    """Adds the folder (or moves it to the top) and returns the new list."""
    folder = os.path.abspath(folder)
    items = [p for p in load_projects(root) if not _same(p["path"], folder)]
    items.insert(0, {"path": folder, "lastUsed": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    try:
        _save(root, items)
    except Exception:
        pass  # the list is a convenience; never block a run on it
    return items


def forget_project(root, folder):
    items = [p for p in load_projects(root) if not _same(p["path"], folder)]
    try:
        _save(root, items)
    except Exception:
        pass
    return items


def is_same_folder(a, b):
    return bool(a) and bool(b) and _same(a, b)


def project_name(folder):
    return os.path.basename(os.path.normpath(folder)) or folder


def project_subtitle(folder):
    """Second line under a project in the sidebar: its latest request, like a session's subtitle."""
    if not os.path.isdir(folder):
        return "Folder not found"
    entries = load_journal(folder)
    if entries:
        lines = str(entries[-1].get("task", "")).strip().splitlines()  # first line: the request itself
        if lines:
            return " ".join(lines[0].split())
    chat = [m for m in load_chat(folder) if m.get("role") == "user"]
    return " ".join(str(chat[-1].get("text", "")).split()) if chat else "No runs yet"
