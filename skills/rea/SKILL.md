---
name: rea
description: Reverse Engineering Agents: on existing code, gather evidence before planning, treat current behavior as the spec, and prove changes by differential testing.
---
# REA: evidence before hypothesis

Apply whenever the folder already contains code: a change, a fix, a port, or a reconstruction of a system from its artifacts (binaries, logs, traces, old sources).

## Recon first
- Before planning, read what is there: manifests (name, scripts, dependencies), the entry points, the existing tests, and what each source file exports. Say in one paragraph what the system does and how it is run, from the evidence alone.
- Keep evidence and hypothesis apart. "The handler returns 404 on a missing id" is evidence when a test or the code shows it; "it probably validates the id" is a hypothesis until a test proves it. Never let a hypothesis into a contract without marking it.
- When reconstructing from artifacts: decompile or trace, then understand (name the components, their boundaries and the data that crosses them), then recreate. Write the understanding down before writing code.

## Current behavior is the specification
- Unless the task says otherwise, every existing public symbol, signature, output format and passing test is a requirement. A change that breaks one needs the task to ask for it.
- Prefer the smallest change that completes the task over a rewrite. Do not reformat, rename or "improve" code the task does not touch.
- Where the task leaves a choice open, choose the reading that preserves existing behavior, and state the assumption where a reviewer will see it (the contract's invariants, a comment at the call site, the commit message).

## Differential parity
- Prove a recreation or a port by running the original and the new code on the same inputs and comparing outputs, not by reading the code. Include the awkward inputs: empty, huge, unicode, malformed, concurrent.
- Record every discrepancy with its category: a real bug in the original (keep the old behavior unless told to fix it), an undocumented feature (keep it), an environment difference (document it), a mistake in the recreation (fix it).
- Spend effort where it pays: prioritize the parts whose behavior most callers depend on, and whose uncertainty is highest, over the parts that are clear or rarely used.

## Done means
- The evidence summary, the assumptions and the discrepancy list exist in writing; all existing tests still pass; and the new behavior is covered by tests that were derived from the task, not from the new code.
