---
name: clean-code
description: Readable, small, honest code: names that say what, functions that do one thing, no dead code, comments that say why.
---
# Clean code

Apply when writing or changing code in any language.

## Names
- Name things for what they are or do: `unpaid_invoices`, `parse_header`, `is_expired`. No `data`, `tmp`, `obj`, `thing`, `helper`.
- Booleans read as questions (`is_`, `has_`, `can_`); functions that change state read as verbs; collections are plural.
- One name per concept across the codebase. Do not rename things the user did not ask to rename.

## Shape
- A function does one thing and fits on a screen. Extract a function when you need a comment to explain a block.
- Three or fewer parameters; pass a small typed object when more are needed.
- Return early for the trivial cases; keep the happy path unindented.
- No dead code, commented-out code, unused imports or variables, or "just in case" branches.
- No cleverness that needs a comment to be understood; prefer the obvious version.

## Comments and docs
- Comments explain why, not what. If the what needs explaining, change the code.
- Every public function has a one-line docstring or doc comment saying what it does and what it returns on bad input.

## Checklist before finishing
- Could a new teammate read each function top to bottom without stopping? If not, split or rename.
- Is every path tested, including the failure path?
