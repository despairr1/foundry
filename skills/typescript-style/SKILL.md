---
name: typescript-style
description: Strict TypeScript: no any, narrow types, named exports, errors as values where it helps, Node built-ins first.
---
# TypeScript style

Apply to TypeScript and JavaScript code.

- `strict` mode; no `any` (use `unknown` and narrow); no non-null assertions unless proven by a check beside them.
- Named exports; one concept per file; `readonly` and `as const` for data that does not change.
- Model states with discriminated unions, not optional fields that must agree.
- `async`/`await` with explicit error handling; never an unawaited promise.
- Node built-ins (`node:fs`, `node:path`, `node:assert`) before packages; packages only when they save real work.
- Tests under Foundry's harness use `node:assert/strict` and plain top-level code, no Jest/Vitest globals.
- Format like Prettier (2 spaces, double quotes, semicolons, 120 columns).
