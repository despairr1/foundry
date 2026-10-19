"""Reads the engine's episodic memory (the SQLite file at FOUNDRY_SQLITE_PATH) for the Memory page.

The engine (src/memory/db.ts) keeps one row per converged module in `episodic_memory`: language, module_name,
failure_pattern (what the run had to get past), stack_trace, winning_patches_json (the semantic patches that made
it converge), embedding_blob (float32 vector), embedding_model and created_at. This module only reads it; the one
write it offers, delete_all(), is the Memory page's explicit "Delete all" button."""
import json
import os
import sqlite3

DEFAULT_FILE = ".foundry-memory.sqlite"


def memory_path(workspace_root, config_manager=None):
    """The database file the engine uses: FOUNDRY_SQLITE_PATH (relative paths are inside the Foundry folder)."""
    configured = ""
    if config_manager is not None:
        configured = str(config_manager.get("FOUNDRY_SQLITE_PATH", "") or "").strip()
    configured = configured or os.environ.get("FOUNDRY_SQLITE_PATH", "").strip() or DEFAULT_FILE
    return configured if os.path.isabs(configured) else os.path.normpath(os.path.join(workspace_root, configured))


def read_episodes(path):
    """{"exists", "episodes", "error", "journal_mode"}; episodes newest first, each a plain dict (no embedding vector)."""
    result = {"exists": os.path.isfile(path), "episodes": [], "error": "", "journal_mode": ""}
    if not result["exists"]:
        return result
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(path)
        except sqlite3.Error as e:
            result["error"] = str(e)
            return result
    try:
        try:
            result["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        except sqlite3.Error:
            pass
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "episodic_memory" not in tables:
            result["error"] = "The file has no episodic_memory table."
            return result
        columns = {row[1] for row in conn.execute("PRAGMA table_info(episodic_memory)")}
        model_column = "embedding_model" if "embedding_model" in columns else "'legacy'"
        rows = conn.execute(
            f"SELECT id, language, module_name, failure_pattern, stack_trace, winning_patches_json, "
            f"length(embedding_blob), {model_column}, created_at FROM episodic_memory ORDER BY id DESC").fetchall()
    except sqlite3.Error as e:
        result["error"] = str(e)
        return result
    finally:
        conn.close()
    for row in rows:
        patches_raw = row[5] or ""
        try:
            patches = json.loads(patches_raw)
            if not isinstance(patches, list):
                patches = None
        except ValueError:
            patches = None
        result["episodes"].append({
            "id": row[0], "language": row[1] or "", "module": row[2] or "", "pattern": row[3] or "", "stack": row[4] or "",
            "patches": patches, "patches_raw": patches_raw, "dims": (row[6] or 0) // 4, "embedding_model": row[7] or "legacy",
            "created_at": row[8] or "",
        })
    return result


def filter_episodes(episodes, query):
    query = (query or "").strip().lower()
    if not query:
        return episodes
    return [e for e in episodes if query in f"{e['id']} {e['module']} {e['language']} {e['pattern']} {e['stack']}".lower()]


def describe_patch(patch):
    """One semantic patch as text: its action, file, target and content."""
    if not isinstance(patch, dict):
        return json.dumps(patch, indent=2)
    head = " · ".join(str(patch.get(key)) for key in ("action", "filePath") if patch.get(key))
    target = patch.get("target")
    lines = [head or "(patch)"]
    if target:
        lines.append(f"target: {target}")
    content = patch.get("content")
    if content:
        lines.append(str(content).rstrip())
    extra = {k: v for k, v in patch.get("metadata", {}).items()} if isinstance(patch.get("metadata"), dict) else {}
    if extra:
        lines.append(json.dumps(extra, indent=2))
    return "\n".join(lines)


def delete_all(path):
    """Deletes every episode. Returns the number of rows removed."""
    conn = sqlite3.connect(path)
    try:
        count = conn.execute("SELECT count(*) FROM episodic_memory").fetchone()[0]
        conn.execute("DELETE FROM episodic_memory")
        conn.commit()
        return int(count)
    finally:
        conn.close()
