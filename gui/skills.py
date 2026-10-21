"""Skills: instruction files (<foundry>/skills/<name>/SKILL.md) the models follow when they apply. The engine reads the
same folder and state (src/skills.ts); this module is what Settings › Skills and the Context pane use."""
import json
import os
import re
import shutil

DEFAULT_ENABLED = ["clean-code", "test-first", "error-handling"]
BUILTIN = {"clean-code", "test-first", "error-handling", "security-review", "cli-design", "python-style", "typescript-style",
           "readme-and-docs", "performance", "data-files", "refactoring", "prompt-engineering", "git-hygiene",
           "rea", "gef", "tca"}
MAX_SKILL_CHARS = 6000
TEMPLATE = """---
name: {name}
description: One line on when this skill applies and what it makes the models do.
---
# {title}

Apply when ...

- The first rule, concrete and checkable.
- The second rule.

## Done means
- What must be true before the work is finished.
"""


def skills_dir(root):
    return os.environ.get("FOUNDRY_SKILLS_DIR", "").strip() or os.path.join(root, "skills")


def state_path(root):
    return os.path.join(root, ".foundry", "skills.json")


def slug(name):
    return re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-") or "skill"


def parse_skill_file(text, fallback_name):
    text = text.lstrip("﻿")
    header, body = {}, text
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
            if kv:
                header[kv.group(1).lower()] = kv.group(2).strip().strip("\"'")
        body = m.group(2)
    name = slug(header.get("name") or fallback_name)
    description = (header.get("description") or (body.strip().splitlines() or [""])[0])[:300]
    return {"name": name, "description": description, "body": body.strip()}


def load_skills(root):
    """[{"name", "description", "body", "path", "builtin"}], sorted by name."""
    folder = skills_dir(root)
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for entry in names:
        file = os.path.join(folder, entry, "SKILL.md")
        if not os.path.isfile(file):
            continue
        try:
            with open(file, "r", encoding="utf-8") as f:
                skill = parse_skill_file(f.read(), entry)
        except OSError:
            continue
        skill.update(path=file, builtin=entry in BUILTIN)
        out.append(skill)
    return sorted(out, key=lambda s: s["name"])


def enabled_names(root, skills=None):
    skills = load_skills(root) if skills is None else skills
    names = {s["name"] for s in skills}
    try:
        with open(state_path(root), "r", encoding="utf-8") as f:
            enabled = json.load(f).get("enabled")
        if isinstance(enabled, list):
            return [n for n in enabled if isinstance(n, str) and n in names]
    except Exception:
        pass
    return [n for n in DEFAULT_ENABLED if n in names]


def save_enabled(root, names):
    path = state_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "enabled": list(names)}, f, indent=2)
    os.replace(tmp, path)


def set_enabled(root, name, on):
    names = [n for n in enabled_names(root) if n != name]
    if on:
        names.append(name)
    save_enabled(root, names)
    return names


def prompt_size(root):
    """(enabled count, characters the engine adds to each model call)."""
    skills = load_skills(root)
    enabled = set(enabled_names(root, skills))
    chars = sum(len(s["body"][:MAX_SKILL_CHARS]) + len(s["description"]) + 20 for s in skills if s["name"] in enabled)
    return len(enabled), chars


def new_skill(root, name):
    """Creates skills/<slug>/SKILL.md from the template; returns its path (or the existing file's path)."""
    folder = os.path.join(skills_dir(root), slug(name))
    file = os.path.join(folder, "SKILL.md")
    if not os.path.exists(file):
        os.makedirs(folder, exist_ok=True)
        with open(file, "w", encoding="utf-8") as f:
            f.write(TEMPLATE.format(name=slug(name), title=name.strip() or slug(name)))
    return file


def import_skill(root, source):
    """Copies a skill in: a folder holding SKILL.md (with its other files), or a single .md file. Returns the new SKILL.md path."""
    source = os.path.abspath(source)
    if os.path.isdir(source):
        file = os.path.join(source, "SKILL.md")
        if not os.path.isfile(file):
            raise ValueError("That folder has no SKILL.md.")
        with open(file, "r", encoding="utf-8") as f:
            name = parse_skill_file(f.read(), os.path.basename(source))["name"]
        target = os.path.join(skills_dir(root), name)
        if os.path.exists(target):
            raise ValueError(f"A skill named {name} already exists.")
        shutil.copytree(source, target)
        return os.path.join(target, "SKILL.md")
    if not source.lower().endswith((".md", ".txt")):
        raise ValueError("Pick a folder with a SKILL.md, or a .md file.")
    with open(source, "r", encoding="utf-8") as f:
        text = f.read()
    name = parse_skill_file(text, os.path.splitext(os.path.basename(source))[0])["name"]
    target = os.path.join(skills_dir(root), name)
    if os.path.exists(target):
        raise ValueError(f"A skill named {name} already exists.")
    os.makedirs(target)
    file = os.path.join(target, "SKILL.md")
    with open(file, "w", encoding="utf-8") as f:
        f.write(text if text.lstrip("﻿").startswith("---") else f"---\nname: {name}\ndescription: {text.strip().splitlines()[0][:200] if text.strip() else name}\n---\n{text}")
    return file


def remove_skill(root, name):
    folder = os.path.join(skills_dir(root), slug(name))
    shutil.rmtree(folder, ignore_errors=True)
    save_enabled(root, [n for n in enabled_names(root) if n != name])
