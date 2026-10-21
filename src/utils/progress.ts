/**
 * Machine-readable progress for front ends (the GUI). When enabled, each event
 * is printed to stdout as one line: `@@FOUNDRY {"type": ...}`. Human-readable
 * logging is unaffected.
 */
export type ProgressEvent =
  | { type: "start"; mode: "build" | "improve" | "self-improve"; task: string; target: string; limitUsd: number; historyEntries: number; rounds?: number }
  | { type: "phase"; phase: "planning" | "designing" | "testing" | "coding" | "checking"; module?: string; moduleIndex?: number; moduleCount?: number; attempt?: number }
  | { type: "plan"; modules: string[] }
  | { type: "module-done"; module: string; moduleIndex: number; moduleCount: number; success: boolean }
  | { type: "spend"; spentUsd: number; limitUsd: number; inputTokens?: number; outputTokens?: number; cachedTokens?: number }
  | {
      type: "self-step";
      step: "health" | "evidence" | "continuing" | "scouting" | "chosen" | "improving" | "verifying" | "updating" | "reverting" | "saving";
      round: number;
      rounds: number;
      detail?: string;
    }
  | { type: "round-done"; round: number; rounds: number; kept: boolean; summary: string }
  | { type: "health"; typecheck: boolean; tests: boolean; detail?: string; problems?: string[] }
  | { type: "chat-thinking"; agent: "claude" | "gemini" | "openai" }
  | { type: "chat-delta"; agent: "claude" | "gemini" | "openai"; text: string; attempt: number }
  | {
      type: "chat-reply";
      agent: "claude" | "gemini" | "openai";
      text: string;
      model: string;
      spentUsd: number;
      inputTokens?: number;
      outputTokens?: number;
      cachedTokens?: number;
    }
  | { type: "chat-error"; agent: "claude" | "gemini" | "openai"; problem: string }
  | {
      type: "finished";
      outcome: "done" | "not-finished" | "spending-limit" | "error";
      summary: string;
      filesChanged: string[];
      spentUsd: number;
      limitUsd: number;
      problem?: string;
    };

export const PROGRESS_PREFIX = "@@FOUNDRY ";

let enabled = process.env.FOUNDRY_PROGRESS === "json";
let spendOffsetUsd = 0;
let spendLimitUsd: number | null = null;

export function enableProgressEvents(): void {
  enabled = true;
}

/**
 * Multi-round runs give each round its own budget engine; this makes spend events report the
 * running total against the overall limit instead of restarting at $0 every round.
 */
export function setSpendReporting(offsetUsd: number, overallLimitUsd: number | null): void {
  spendOffsetUsd = offsetUsd;
  spendLimitUsd = overallLimitUsd;
}

export function emitProgress(event: ProgressEvent): void {
  if (!enabled) return;
  const adjusted =
    event.type === "spend"
      ? { ...event, spentUsd: event.spentUsd + spendOffsetUsd, limitUsd: spendLimitUsd ?? event.limitUsd }
      : event;
  process.stdout.write(`${PROGRESS_PREFIX}${JSON.stringify(adjusted)}\n`);
}
