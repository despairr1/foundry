---
name: tca
description: Token and Context Arbiter: send the models only what they need (slices, stubs, folded logs), keep the fixed part of a prompt stable and cached, budget each attempt, and stop an attempt that thrashes.
---
# TCA: spend tokens where they change the outcome

Apply when composing anything that goes to a model, when a conversation or a build grows long, and when an attempt keeps failing.

## Only what the task needs
- Give the model the slice that matters: the symbols the change touches and what they call or are called by, not the whole repository. Large files you only depend on go in as signature stubs (exports and their types), expanded on request.
- Fold logs before they go in: keep the assertion and error lines, the diff of expected and actual, the first frame in the project's own code; drop repeated lines, framework frames and color codes. A 300-line log is usually a 10-line counterexample.
- Say what was left out ("4 files not shown; ask for one by name") so the model can ask instead of guessing.

## Stable prefix, changing suffix
- Order a prompt so that the part that stays the same across calls comes first (role, contract, harness, tests, dependencies, instructions) and the part that changes comes last (current source, the last failure, memory). The stable prefix is cached by the provider; every repeat call then reads it at a fraction of the price.
- Do not touch the prefix between attempts: no timestamps, no attempt counters, no reordering. Change only the suffix.

## Budget every hypothesis
- Each attempt (a branch, a fix, a plan) gets its own allowance of calls and money. When it is spent, the attempt ends; the budget is not borrowed from the others.
- Route by difficulty: a cheap model or a deterministic check for what can be checked, the strong model for the decision that needs it.

## Detect thrashing
- The same failure signature (ignoring line numbers, paths and timings) coming back two or three times, or the same patch resent, means the approach is wrong, not the details. Stop editing: re-read the contract and the failing assertion, question the assumption the current code rests on, and change the design. If that does not work either, abandon the attempt and let another branch or a fresh plan take over.

## Long conversations
- When the window fills, fold the oldest messages to one line each rather than dropping them, so the thread survives. Keep decisions and open questions in full; fold chatter first.

## Done means
- Every model call carried a stable, cached prefix and a short, specific suffix; no log went in unfolded; every attempt had a budget and a stop rule; and the saved tokens are visible in the counter.
