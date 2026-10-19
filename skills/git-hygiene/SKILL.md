---
name: git-hygiene
description: Commits that explain themselves: small, one change each, imperative subject, nothing generated or secret committed.
---
# Git hygiene

Apply when the project is a git repository.

- One logical change per commit; subject in the imperative, under 72 characters ("Add CSV export"), body says why when it is not obvious.
- `.gitignore` covers build output, dependencies, caches, editor files, `.env` and secrets before the first commit.
- Never commit generated files, large binaries, or credentials; never rewrite shared history.
- Keep the working tree clean: no stray debug files, no commented-out code.
