---
name: prompt-engineering
description: Writing prompts for AI models: role, context, task, format, examples, constraints, and how to test a prompt.
---
# Prompt engineering

Apply in the prompt studio and whenever a prompt for a model is written or improved.

A strong prompt has, in this order:
1. Role: who the model is and for whom it works, in one sentence.
2. Context: the facts the model needs (data, audience, constraints); nothing it can infer.
3. Task: the single thing to do, in plain imperative sentences; break a big task into numbered steps.
4. Output format: exact shape (headings, JSON schema, length), and what to do when information is missing.
5. Examples: one or two input/output pairs when the format is unusual.
6. Constraints: what not to do, tone, language, length limits.

Rules: concrete words over vague ones ("under 120 words" not "short"); say what to do, not only what to avoid; one instruction per sentence; put the most important instruction first and repeat it at the end for long prompts; ask the model to say when it is unsure. Test a prompt with three inputs: a typical one, an edge case, and a malformed one; fix the prompt, not the output.
