---
name: gef
description: Greenfield Engineering Framework: contracts first, tests written apart from the code, a hidden acceptance suite, gates that must all pass, and only the shrunken counterexample fed back.
---
# GEF: build new systems that stay right

Apply when designing or building something new (a module, a service, a tool), and when deciding whether a part is finished.

## Contracts before code
- Start from a domain contract: the types, the invariants that must always hold, the preconditions each operation rejects and the postconditions it guarantees, and the failure modes with their visible effect. If it cannot be written down, it cannot be tested.
- Split the system into parts that can each be built, tested and accepted alone, ordered so that each part depends only on the parts before it. Name the dependency, never assume it.

## Tests apart from the code
- The tests come from the contract, not from the implementation. Whoever writes the code must not write the tests that accept it, and must not see every test: keep a hidden acceptance suite (realistic workloads, end-to-end use of the public API, values that are not the textbook boundary numbers) that the implementation is checked against last.
- The visible tests kill mutants: exact values at every boundary, every precondition rejected, every invariant asserted. The hidden tests catch an implementation that fits the visible cases without meeting the contract.

## The gates
A part is accepted only when all of these hold, in this order: it compiles with no diagnostics; the visible tests pass; the tests of every earlier part still pass; the hidden acceptance tests pass; the tests kill the required share of mutants. A part that fails a gate is not "mostly done".

## Counterexamples, not logs
- When a gate fails, feed back the counterexample, shrunk: the failing assertion, the expected and actual values, the input that produced them, and the first frame in the project's own code. Not the whole log, not repeated lines, not framework frames.
- A hidden test that fails is reported as its assertion only; the implementation is fixed by re-reading the contract, never by guessing the test.

## Cost and distortion
- Prefer the design that is simplest to verify over the one that is cleverest; every extra path is a gate that can fail.
- Watch for fitting the tests instead of meeting the contract: special-casing the tested values, catching exceptions to make an assertion pass, or weakening a check. These are rejected on sight.

## Done means
- Every gate passed on the final code, the hidden suite was never shown to the writer, and the contract, the tests and the counterexamples that were fixed are kept with the code.
