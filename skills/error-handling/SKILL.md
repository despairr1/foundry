---
name: error-handling
description: Explicit failures: typed errors, no silent catches, messages that say what to do, exit codes for command-line programs.
---
# Error handling

Apply to every function that can fail.

- Validate inputs at the boundary (user input, files, network, environment) and fail there with a clear message; trust inputs inside.
- Raise or return typed errors (`ValueError`, a custom `ConfigError`, a `Result`); never `except Exception: pass`, never swallow an error into a default value silently.
- Error messages name the thing and the fix: `Config file not found: ./settings.json (copy settings.example.json to create one)`.
- Never expose secrets, stack traces or internal paths in messages meant for users; keep them in logs.
- Command-line programs: exit code 0 on success, 1 on a usage or input error, 2 on an internal failure; print errors to stderr.
- Resources (files, sockets, processes) are closed in `finally` or context managers / `using`, including on error.
- Retries only for transient failures (network, locks), with a bounded count and backoff, and never for programming errors.
- Partial writes are not left behind: write to a temporary file and rename, or roll back.
