---
name: data-files
description: Safe files and formats: atomic writes, UTF-8, versioned schemas, validation on load, no data loss on crash.
---
# Data files

Apply when the program stores or reads JSON, CSV, SQLite or other files.

- Write atomically: write to `<file>.tmp`, flush, then rename over the original. Never leave a half-written file.
- UTF-8 everywhere, explicit newline handling, BOM-free.
- Put a `version` field in every JSON file you own; validate on load and give a clear error for an unknown or corrupt file instead of crashing later.
- Keep the schema in one place (a dataclass or type) and convert at the boundary.
- CSV: use the standard csv module with explicit delimiter and quoting; handle headers; never split on commas by hand.
- SQLite: one connection per thread, parameterized queries, a migrations table with versions.
- Back up before destructive changes when the file is the user's data.
