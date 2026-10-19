---
name: performance
description: Measure before optimizing, then fix the big-O: no quadratic loops on large inputs, stream large files, cache what is reused.
---
# Performance

Apply when inputs can be large, the program runs often, or the user mentions speed.

- Measure first (a timer around the suspect, realistic input sizes); state the numbers in the summary.
- Fix complexity before constants: a set or dict lookup instead of a nested loop; sort once; avoid repeated string concatenation in loops.
- Stream large files line by line; never read a multi-gigabyte file into memory.
- Reuse expensive objects (compiled regexes, connections, parsed configs); cache pure results that are requested again.
- Do not optimize what the measurement shows is already fast; keep the code readable.
- Add a test with a large input that must finish in a bounded time only when the requirement is explicit.
