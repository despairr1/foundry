"""The Code map page's scan: which source files a folder has and which names each one exports.

This is a regular-expression scan of top-level declarations, not a parse: it finds `export function x`,
`export class Y`, `module.exports.z`, Python's top-level `def`/`class`, Go's capitalized `func`/`type` and Rust's
`pub fn`/`pub struct`. It does not see calls between them. The engine's Tree-sitter call graph
(src/ast/call_graph.ts) lives in the engine process and is not written anywhere this window can read."""
import os
import re
import time

SKIP_DIRS = {"node_modules", "dist", "build", "out", "coverage", "target", "vendor", "__pycache__", ".git", ".hg", ".svn",
             ".venv", "venv", "env", ".tox", ".mypy_cache", ".pytest_cache", ".idea", ".vscode", ".next", ".cache",
             ".foundry", ".foundry-ouroboros", "site-packages"}

LANGUAGES = {".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
             ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
             ".py": "python", ".go": "go", ".rs": "rust"}

_TS_EXPORT = re.compile(r"^export\s+(?:default\s+)?(?:declare\s+)?(?:abstract\s+)?(?:async\s+)?"
                        r"(function\*?|class|interface|type|enum|const|let|var|namespace)\s+([A-Za-z_$][\w$]*)")
_TS_EXPORT_DEFAULT = re.compile(r"^export\s+default\s+(?:async\s+)?(function\*?|class)\b(?!\s+[A-Za-z_$])")
_TS_EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}")
_CJS_EXPORT = re.compile(r"^(?:module\.)?exports\.([A-Za-z_$][\w$]*)\s*=")
_PY_DEF = re.compile(r"^(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")
_GO_DECL = re.compile(r"^(func|type)\s+(?:\([^)]*\)\s*)?([A-Z]\w*)")
_RUST_PUB = re.compile(r"^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(?:unsafe\s+)?(?:extern\s+\"[^\"]*\"\s+)?"
                       r"(fn|struct|enum|trait|type|mod|const|static|union)\s+([A-Za-z_]\w*)")

KIND_LABELS = {"function": "fn", "function*": "fn*", "class": "class", "interface": "interface", "type": "type", "enum": "enum",
               "const": "const", "let": "let", "var": "var", "namespace": "namespace", "def": "def", "func": "func",
               "struct": "struct", "trait": "trait", "mod": "mod", "static": "static", "union": "union", "fn": "fn", "export": "export"}


def symbols_in(source, language):
    """[{"kind", "name", "line"}] for the exported / top-level names declared in one file's source."""
    found = []
    for number, line in enumerate(source.splitlines(), start=1):
        if language in ("typescript", "javascript"):
            m = _TS_EXPORT.match(line)
            if m:
                found.append({"kind": m.group(1), "name": m.group(2), "line": number})
                continue
            if _TS_EXPORT_DEFAULT.match(line):
                found.append({"kind": "default", "name": "(default export)", "line": number})
                continue
            m = _TS_EXPORT_LIST.match(line)
            if m:
                for part in m.group(1).split(","):
                    name = part.strip().split(" as ")[-1].strip()
                    if name and name != "default":
                        found.append({"kind": "export", "name": name, "line": number})
                continue
            m = _CJS_EXPORT.match(line)
            if m:
                found.append({"kind": "export", "name": m.group(1), "line": number})
        elif language == "python":
            m = _PY_DEF.match(line)
            if m and not m.group(2).startswith("_"):
                found.append({"kind": m.group(1), "name": m.group(2), "line": number})
        elif language == "go":
            m = _GO_DECL.match(line)
            if m:
                found.append({"kind": m.group(1), "name": m.group(2), "line": number})
        elif language == "rust":
            m = _RUST_PUB.match(line)
            if m:
                found.append({"kind": m.group(1), "name": m.group(2), "line": number})
    return found


def scan_folder(root, max_files=2000, max_bytes=1_000_000):
    """{"root", "files": [{"path", "language", "lines", "symbols"}], "file_count", "symbol_count", "truncated", "seconds"}.
    Files are relative to root and sorted; folders in SKIP_DIRS are not entered."""
    started = time.time()
    files, truncated = [], False
    if root and os.path.isdir(root):
        for current, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(names):
                language = LANGUAGES.get(os.path.splitext(name)[1].lower())
                if not language or name.endswith(".d.ts") or name.endswith(".min.js"):
                    continue
                if len(files) >= max_files:
                    truncated = True
                    break
                path = os.path.join(current, name)
                try:
                    if os.path.getsize(path) > max_bytes:
                        continue
                    with open(path, "r", encoding="utf-8", errors="replace") as f:
                        source = f.read()
                except OSError:
                    continue
                files.append({"path": os.path.relpath(path, root).replace(os.sep, "/"), "language": language,
                              "lines": source.count("\n") + (1 if source and not source.endswith("\n") else 0),
                              "symbols": symbols_in(source, language)})
            if truncated:
                break
    files.sort(key=lambda f: f["path"].lower())
    return {"root": root, "files": files, "file_count": len(files), "symbol_count": sum(len(f["symbols"]) for f in files),
            "truncated": truncated, "seconds": time.time() - started}


def filter_files(files, query):
    """The files whose path or symbol names contain the query (case-insensitive); each file keeps only matching symbols
    unless its path matched."""
    query = (query or "").strip().lower()
    if not query:
        return files
    matched = []
    for entry in files:
        if query in entry["path"].lower():
            matched.append(entry)
            continue
        symbols = [s for s in entry["symbols"] if query in s["name"].lower()]
        if symbols:
            matched.append({**entry, "symbols": symbols})
    return matched
