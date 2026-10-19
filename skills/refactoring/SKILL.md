---
name: refactoring
description: Behavior-preserving changes in small steps: public API kept, tests green between steps, no drive-by rewrites.
---
# Refactoring

Apply when changing existing code without changing what it does.

- Keep the public API and behavior exactly; add a test around the current behavior first if none exists.
- Small steps: rename, extract, move, each one leaving the tests green. Do not combine a refactor with a feature.
- Do not rewrite files the task does not touch; do not reformat untouched lines.
- Remove duplication by extracting one function, not by adding a flag to an existing one.
- Keep names and structure that the user already knows unless the task is about them.
- Summarize what moved where, so the user can review the diff.
