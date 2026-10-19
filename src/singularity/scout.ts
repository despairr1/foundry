import * as fs from "fs";
import * as path from "path";
import { FoundryConfig } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { completeWithClaude, completeWithGemini, extractJson, extractJsonArray } from "../swarm/providers";
import { logger } from "../utils/logger";
import { toPosixRelative } from "../utils/paths";

/**
 * Scouting: what a self-improvement round does when it is told "improve yourself" and nothing more.
 *
 * Without a concrete focus, the convergence loop (plan, three branches, up to eight iterations each,
 * mutation testing) used to wander until the whole budget was gone. Scouting spends two short calls
 * first: the architect (Claude) proposes a few small, concrete, checkable improvements from the
 * evidence (health, log problems, TODOs, the source map and the history of earlier rounds), the
 * auditor (Gemini) picks the one most likely to be finished correctly, and that choice becomes the
 * round's focus and its name in the history, so the next round does not try the same dead end.
 */

export interface ImprovementCandidate {
  /** Imperative, under 100 characters: the round's name in the history. */
  title: string;
  /** Files under src/ the change touches (one or two). */
  files: string[];
  change: string;
  why: string;
  /** What the regression check in tests/self-checks/ must prove. */
  verify: string;
}

export interface ScoutEvidence {
  health: { typecheck: boolean; tests: boolean; detail?: string };
  problems: string[];
  history: string;
  sourceMap: string;
  todos: string[];
}

export interface ScoutChoice {
  candidate: ImprovementCandidate;
  reason: string;
  candidates: ImprovementCandidate[];
}

const MAX_CANDIDATES = 3;

// "Improve yourself", "make it better", "your choice"...: a request, not a focus.
const GENERIC_FOCUS = [
  /^(please |just |go |now |and )*(improve|better|upgrade|enhance|optimi[sz]e|refine|fix|polish|tune)( yourself| itself| foundry| the (engine|harness|code|app)| things| stuff| something| anything| whatever| it| everything)?( please)?$/,
  /^(make|get) (yourself|itself|foundry|it|things|the (engine|harness)) (better|smarter|faster|stronger|more reliable|more robust)$/,
  /^(do your thing|your (choice|call|pick)|you (choose|pick|decide)|surprise me|anything|whatever|whatever you (want|think|like|see fit)|self[- ]?improve(ment)?|improve|improvement|general improvements?|something useful|whatever (is|s) best)$/
];

export function isGenericFocus(focus: string | undefined): boolean {
  const text = (focus ?? "")
    .toLowerCase()
    .replace(/[^a-z\s-]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return true;
  if (text.split(" ").length > 8) return false;
  return GENERIC_FOCUS.some((re) => re.test(text));
}

function sourceFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile() && /\.ts$/.test(entry.name) && !/\.d\.ts$/.test(entry.name)) out.push(full);
    }
  };
  walk(path.join(root, "src"));
  return out;
}

/** Every src/ file with its length, so the architect proposes changes to files that exist. */
export function sourceMap(root: string, max = 160): string {
  const files = sourceFiles(root);
  const lines = files.slice(0, max).map((full) => {
    const text = fs.readFileSync(full, "utf8");
    return `${toPosixRelative(root, full)} (${text.split(/\r?\n/).length} lines)`;
  });
  if (files.length > max) lines.push(`... and ${files.length - max} more`);
  return lines.join("\n");
}

/** TODO / FIXME / HACK notes in src/: improvements the code already asks for. */
export function findTodos(root: string, max = 20): string[] {
  const found: string[] = [];
  for (const full of sourceFiles(root)) {
    const rel = toPosixRelative(root, full);
    fs.readFileSync(full, "utf8")
      .split(/\r?\n/)
      .forEach((line, idx) => {
        if (found.length < max && /\b(TODO|FIXME|HACK|XXX)\b/.test(line)) {
          found.push(`${rel}:${idx + 1}: ${line.trim().slice(0, 160)}`);
        }
      });
    if (found.length >= max) break;
  }
  return found;
}

function toCandidate(raw: unknown): ImprovementCandidate | null {
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const text = (key: string): string => (typeof record[key] === "string" ? (record[key] as string).trim() : "");
  const files = Array.isArray(record.files)
    ? record.files
        .filter((f): f is string => typeof f === "string")
        .map((f) => f.trim().replace(/\\/g, "/").replace(/^\.\//, ""))
        .filter((f) => f.startsWith("src/"))
    : [];
  const title = text("title");
  const change = text("change");
  if (!title || !change || files.length === 0) return null;
  return {
    title: title.replace(/\s+/g, " ").slice(0, 160),
    files: files.slice(0, 4),
    change: change.slice(0, 1200),
    why: text("why").slice(0, 600),
    verify: text("verify").slice(0, 600)
  };
}

export function parseCandidates(text: string): ImprovementCandidate[] {
  const items = extractJsonArray(text, "candidates")
    .map(toCandidate)
    .filter((c): c is ImprovementCandidate => c !== null);
  if (items.length === 0) {
    throw new Error("No usable candidate: each needs a title, a change and files under src/.");
  }
  return items.slice(0, MAX_CANDIDATES);
}

export function parseChoice(text: string, count: number): { index: number; reason: string } {
  const value = extractJson(text) as Record<string, unknown> | null;
  const n = Number(value?.choice ?? value?.index ?? value?.best);
  if (!Number.isInteger(n) || n < 1 || n > count) {
    throw new Error(`The auditor's choice must be a number from 1 to ${count}`);
  }
  return { index: n - 1, reason: typeof value?.reason === "string" ? value.reason.trim().slice(0, 400) : "" };
}

function describeEvidence(evidence: ScoutEvidence): string {
  const health = [
    `Type-check: ${evidence.health.typecheck ? "passing" : "FAILING"}`,
    `Test suite: ${evidence.health.tests ? "passing" : "FAILING"}`,
    evidence.health.detail ? `Failing: ${evidence.health.detail}` : ""
  ]
    .filter(Boolean)
    .join("\n");
  return [
    health,
    evidence.problems.length > 0
      ? `Recent warnings and errors from Foundry's log (oldest first):\n${evidence.problems.map((p) => `- ${p}`).join("\n")}`
      : "Recent warnings and errors from Foundry's log: none recorded.",
    evidence.todos.length > 0 ? `TODO/FIXME notes in the source:\n${evidence.todos.map((t) => `- ${t}`).join("\n")}` : "TODO/FIXME notes in the source: none.",
    `Source files (src/):\n${evidence.sourceMap || "(none found)"}`,
    evidence.history
  ]
    .filter(Boolean)
    .join("\n\n");
}

export function scoutPrompt(evidence: ScoutEvidence): string {
  return [
    `You are the architect of Foundry, an autonomous coding engine (TypeScript on Node.js, CommonJS) that is about to improve its own source code. Propose the improvements most worth making right now.`,
    `Evidence:\n${describeEvidence(evidence)}`,
    [
      `Rules for a candidate:`,
      `- One small, self-contained change: one or two files under src/, roughly under 120 changed lines, finished in a single pass.`,
      `- It must make Foundry more reliable, more useful or cheaper to run, and it must be provable with a standalone check script (tests/self-checks/<name>.check.ts, using node:assert) that fails before the change and passes after it.`,
      `- Prefer, in this order: a failing check; a problem the log shows; a TODO/FIXME; a robustness gap in how model output is parsed, patches are applied or tests are run; a clear simplification.`,
      `- Do not propose what the history shows was already tried and failed, or what the user undid. Do not propose edits to existing tests, the GUI (gui/), package.json or the build setup.`,
      `- Be concrete: name the functions and the exact behavior to change.`
    ].join("\n"),
    `Respond with ONLY a JSON array of up to ${MAX_CANDIDATES} candidates, best first, each of this shape (no prose, no comments):\n` +
      `[{"title": "<imperative, under 100 characters>", "files": ["src/..."], "change": "<what to change, concretely>", "why": "<the evidence it addresses>", "verify": "<what the check proves>"}]`
  ].join("\n\n");
}

export function rankPrompt(candidates: ImprovementCandidate[], evidence: ScoutEvidence): string {
  const list = candidates
    .map((c, idx) => `${idx + 1}. ${c.title}\n   Files: ${c.files.join(", ")}\n   Change: ${c.change}\n   Why: ${c.why}\n   Check proves: ${c.verify}`)
    .join("\n");
  return [
    `You are the auditor of Foundry, an autonomous coding engine that is about to improve its own source code. The architect proposed these improvements:`,
    list,
    `Evidence they were drawn from:\n${describeEvidence(evidence)}`,
    `Pick the one that is most likely to be completed correctly in one short round AND most worth having. Reject anything vague, risky (could break other modules, changes exported signatures, touches many files) or already tried according to the history.`,
    `Respond with ONLY JSON of this shape: {"choice": <1-${candidates.length}>, "reason": "<one sentence>"}`
  ].join("\n\n");
}

const OFFLINE_CANDIDATE: ImprovementCandidate = {
  title: "Add a regression check for JSON extraction from model output",
  files: ["src/swarm/providers.ts"],
  change: "Keep extractJson's behavior and add a check that covers fenced, prose-wrapped and bare JSON.",
  why: "Offline demo: no evidence was gathered from the models.",
  verify: "extractJson returns the first balanced JSON value in each case."
};

/** Two short model calls: the architect proposes, the auditor picks. Offline: a fixed demo candidate. */
export async function chooseImprovement(config: FoundryConfig, economics: EconomicsEngine, evidence: ScoutEvidence): Promise<ScoutChoice> {
  if (config.offlineMode) {
    return { candidate: OFFLINE_CANDIDATE, reason: "offline demo", candidates: [OFFLINE_CANDIDATE] };
  }
  const candidates = await completeWithClaude<ImprovementCandidate[]>(config, economics, {
    label: "Scout[propose]",
    prompt: scoutPrompt(evidence),
    parse: parseCandidates
  });
  let index = 0;
  let reason = "";
  if (candidates.length > 1) {
    if (!config.geminiApiKey) {
      reason = "no Gemini key, so the architect's first choice stands";
    } else {
      try {
        ({ index, reason } = await completeWithGemini<{ index: number; reason: string }>(config, economics, {
          label: "Scout[rank]",
          prompt: rankPrompt(candidates, evidence),
          parse: (text) => parseChoice(text, candidates.length)
        }));
      } catch (err) {
        if (err instanceof Error && err.name === "BudgetExceededError") throw err;
        logger.warn("Scout", `The auditor could not rank the candidates (${err instanceof Error ? err.message : String(err)}); the architect's first choice stands.`);
        reason = "the auditor could not rank them, so the architect's first choice stands";
      }
    }
  }
  return { candidate: candidates[index], reason, candidates };
}

/** The chosen candidate as the round's focus text. */
export function focusFromCandidate(candidate: ImprovementCandidate): string {
  return [
    candidate.title,
    `Files: ${candidate.files.join(", ")}`,
    `Change: ${candidate.change}`,
    candidate.why ? `Why: ${candidate.why}` : "",
    candidate.verify ? `The check must prove: ${candidate.verify}` : ""
  ]
    .filter(Boolean)
    .join("\n");
}
