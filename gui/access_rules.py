"""AI access rules: which folders Foundry's models may read, change or run things in.

Written by the Files pane (right-click a folder: Allow / Block AI access) and Settings › AI access to
<foundry>/.foundry/access.json; read by the engine too (src/utils/access.ts), which refuses to work on a
blocked folder and leaves blocked subfolders of a project unread. Everything is allowed unless a rule
says otherwise; the most specific rule (the longest matching path) wins."""
import json
import os

ACCESS_KINDS = ("allow", "block")


def rules_path(root):
    return os.environ.get("FOUNDRY_ACCESS_RULES", "").strip() or os.path.join(root, ".foundry", "access.json")


def load_rules(root):
    try:
        with open(rules_path(root), "r", encoding="utf-8") as f:
            rules = json.load(f).get("rules", [])
    except Exception:
        return []
    return [{"path": r["path"].strip(), "access": r["access"]} for r in rules
            if isinstance(r, dict) and isinstance(r.get("path"), str) and r["path"].strip() and r.get("access") in ACCESS_KINDS]


def save_rules(root, rules):
    path = rules_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ignore = os.path.join(os.path.dirname(path), ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("*\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "rules": rules}, f, indent=2)
    os.replace(tmp, path)


def _canon(path):
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _covers(rule_path, target):
    rule, folder = _canon(rule_path), _canon(target)
    return folder == rule or folder.startswith(rule + os.sep)


def ruling_for(target, rules):
    """The rule that decides a folder's access (the most specific one), or None."""
    best = None
    for rule in rules:
        if _covers(rule["path"], target) and (best is None or len(_canon(rule["path"])) > len(_canon(best["path"]))):
            best = rule
    return best


def access_for(target, rules):
    rule = ruling_for(target, rules)
    return rule["access"] if rule else "allow"


def own_rule(target, rules):
    """The rule set on exactly this folder, if any (not an inherited one)."""
    return next((r for r in rules if _canon(r["path"]) == _canon(target)), None)


def set_rule(root, target, access):
    """access: "allow", "block", or None to clear the folder's own rule. Returns the new rules."""
    rules = [r for r in load_rules(root) if _canon(r["path"]) != _canon(target)]
    if access in ACCESS_KINDS:
        rules.append({"path": os.path.abspath(target), "access": access})
    rules.sort(key=lambda r: _canon(r["path"]))
    save_rules(root, rules)
    return rules


def is_blocked(root, target):
    return access_for(target, load_rules(root)) == "block"


def blocked_message(target):
    return (f"AI access to this folder is blocked:\n{target}\n\nTo allow it, right-click the folder in the Files pane "
            "and choose “Allow AI access”, or change it in Settings › AI access.")
