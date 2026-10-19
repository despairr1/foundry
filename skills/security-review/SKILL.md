---
name: security-review
description: Treat every input as hostile: validation, path and injection safety, secrets handling, least privilege.
---
# Security review

Apply when code reads input, touches files, runs commands, talks to a network, or handles credentials.

## Inputs
- Validate type, length and format before use; allow-list rather than block-list.
- File paths from input are resolved and checked to stay inside the intended folder (no `..`, no absolute paths, no symlink escape).
- Never build shell commands or SQL by string concatenation; pass argument arrays and parameterized queries.
- Decode once, validate once, then treat the value as trusted inside.

## Secrets
- Secrets come from environment variables or a secrets store, never from source code, logs, error messages, URLs or test fixtures.
- Mask secrets in any output (`sk-…1234`).

## Least privilege
- Open files read-only unless writing is needed; create files with restrictive permissions; do not run as administrator.
- Network calls use HTTPS, verify certificates, time out, and limit response size.

## Output
- Escape or encode output for its destination (HTML, shell, SQL, JSON).
- Do not log personal data; keep logs free of request bodies with credentials.

Note in the summary any risk you could not remove.
