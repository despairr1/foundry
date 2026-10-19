---
name: test-first
description: Tests that prove the contract: edge cases listed before coding, one behavior per test, failures that explain themselves.
---
# Test first

Apply when designing a module, writing its tests, or deciding whether code is done.

## Before writing code
List the acceptance criteria as test names. For every input, cover: empty, one item, many items, the largest realistic size, invalid types, malformed data, unicode and whitespace, duplicates, and the boundary values (0, -1, max, exactly-at-limit).

## Writing tests
- One behavior per test, named for the behavior: `rejects_empty_input`, `keeps_order_of_equal_keys`.
- Arrange, act, assert; no logic in tests (no loops that hide which case failed, no conditionals).
- Assert on values and error types, not on log text or timing.
- A failure must say what was expected and what happened: compare whole values, not `assert ok`.
- Tests must be deterministic: no network, no clock, no randomness without a fixed seed, no shared global state.
- Under Foundry's harness: TypeScript tests use only `node:assert/strict` and top-level code (no Jest globals); Python tests are `pytest` functions and may use `hypothesis` for property tests.

## Property tests
When a function has an invariant (sorting keeps length, encode/decode round-trips, idempotent normalization), write a property test for it instead of three examples.

## Done means
Every criterion has a test, every test fails before the change and passes after it, and the tests kill deliberately broken copies of the code (mutation testing): assert on exact values, not just that something was returned.
