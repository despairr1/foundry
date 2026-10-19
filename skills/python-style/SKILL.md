---
name: python-style
description: Modern Python: PEP 8, type hints, dataclasses, pathlib, no bare except, a real entry point.
---
# Python style

Apply to Python code.

- Python 3.10+; type hints on every public function; `from __future__ import annotations` when helpful.
- `dataclass` (or `TypedDict`/`NamedTuple`) for records; no dicts with magic keys.
- `pathlib.Path` for paths; `open(..., encoding="utf-8")` always; `json` with `ensure_ascii=False`.
- f-strings; no `%` formatting; no mutable default arguments.
- `if __name__ == "__main__": raise SystemExit(main())` as the entry point; logic lives in importable functions.
- No bare `except:`; catch the specific exception; re-raise with context (`raise X from e`).
- Standard library first; a third-party package only when it saves real work, and then pinned in `requirements.txt`.
- Tests with `pytest` functions named `test_*`; property tests with `hypothesis` where an invariant exists.
- Black-compatible formatting, 120 columns; imports grouped stdlib / third-party / local.
