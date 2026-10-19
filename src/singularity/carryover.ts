import { createHash } from "crypto";
import * as fs from "fs";
import * as path from "path";
import { PartialProgress } from "../engine/convergence";

/**
 * Carryover: the best partial work of a self-improvement round that was not kept
 * (spending limit, no convergence, or a change that failed verification), saved
 * under `.foundry/carryover/<id>/` so the next round can continue it instead of
 * starting over. Only `src/` files are carried as code; the round's check is kept
 * as reference text, since an unfinished check must not become part of `npm test`.
 *
 * Each carried file records a fingerprint of the file it was based on. A carryover
 * is only applied when every one of those files is still unchanged, so stale
 * partial work can never overwrite a newer kept improvement.
 */

export type CarryoverStatus = "pending" | "consumed" | "superseded" | "abandoned" | "discarded";
export type CarryoverReason = "spending-limit" | "not-converged" | "verification-failed" | "other";

export interface CarryoverFile {
  path: string;
  /** sha256 of the file the partial work was based on; null when the round created the file. */
  baseHash: string | null;
}

export interface CarryoverManifest {
  version: 1;
  id: string;
  createdAt: string;
  /** Normalized focus of the round that produced it ("" when it had none). */
  focus: string;
  label: string;
  reason: CarryoverReason;
  problem: string;
  /** Spend that produced this work, summed along the chain of continuations. */
  spentUsd: number;
  plan: string[];
  moduleName: string | null;
  contract: unknown;
  testFilePath: string | null;
  testCode: string | null;
  lastFeedback: string | null;
  files: CarryoverFile[];
  status: CarryoverStatus;
  /** How many rounds already continued this line of work without a kept result. */
  failedContinuations: number;
  parentId: string | null;
  resolvedAt?: string;
  resolvedBy?: string;
}

export interface ApplyResult {
  applied: boolean;
  files: string[];
  reason?: string;
}

/** After this many failed continuations a line of work is retired instead of offered again. */
export const MAX_FAILED_CONTINUATIONS = 2;
const CARRYOVER_DIR = path.join(".foundry", "carryover");

export function hashContent(content: Buffer | string | null | undefined): string | null {
  if (content === null || content === undefined) return null;
  return createHash("sha256").update(content).digest("hex");
}

export function normalizeFocus(focus?: string): string {
  return (focus ?? "").trim().replace(/\s+/g, " ").toLowerCase();
}

function carryoverDir(root: string, id: string): string {
  return path.join(root, CARRYOVER_DIR, id);
}

function writeManifest(root: string, manifest: CarryoverManifest): void {
  const dir = carryoverDir(root, manifest.id);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(manifest, null, 2), "utf8");
}

export function listCarryovers(root: string): CarryoverManifest[] {
  const base = path.join(root, CARRYOVER_DIR);
  if (!fs.existsSync(base)) return [];
  const manifests: CarryoverManifest[] = [];
  for (const id of fs.readdirSync(base)) {
    try {
      manifests.push(JSON.parse(fs.readFileSync(path.join(base, id, "manifest.json"), "utf8")) as CarryoverManifest);
    } catch {
      // A half-written or hand-edited carryover is ignored rather than breaking the run.
    }
  }
  return manifests.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

/**
 * The carryover the next round should continue: the newest pending one with the same focus,
 * or the newest pending one of any focus when the round has no focus.
 */
export function selectCarryover(root: string, focus?: string): CarryoverManifest | null {
  const wanted = normalizeFocus(focus);
  const pending = listCarryovers(root).filter((c) => c.status === "pending");
  const candidates = wanted ? pending.filter((c) => c.focus === wanted) : pending;
  return candidates[candidates.length - 1] ?? null;
}

export function resolveCarryover(root: string, manifest: CarryoverManifest, status: CarryoverStatus, resolvedBy?: string): void {
  manifest.status = status;
  manifest.resolvedAt = new Date().toISOString();
  if (resolvedBy) manifest.resolvedBy = resolvedBy;
  writeManifest(root, manifest);
}

/** A continuation failed without producing new partial work: count it, and retire the line after the limit. */
export function recordFailedContinuation(root: string, manifest: CarryoverManifest): void {
  manifest.failedContinuations += 1;
  if (manifest.failedContinuations >= MAX_FAILED_CONTINUATIONS) {
    resolveCarryover(root, manifest, "abandoned");
  } else {
    writeManifest(root, manifest);
  }
}

export function discardPendingCarryovers(root: string): number {
  const pending = listCarryovers(root).filter((c) => c.status === "pending");
  for (const manifest of pending) resolveCarryover(root, manifest, "discarded");
  return pending.length;
}

/**
 * Saves a not-kept round's partial work. `diskFiles` are the round's changed files as they were on disk
 * before rollback (modules that converged but failed verification); `partial` is the engine's best attempt
 * at the unfinished module. Returns null when there is nothing worth carrying.
 */
export function saveCarryover(
  root: string,
  params: {
    before: Map<string, Buffer>;
    diskFiles: Map<string, string>;
    partial: PartialProgress | null;
    label: string;
    focus?: string;
    reason: CarryoverReason;
    problem: string;
    spentUsd: number;
    parent: CarryoverManifest | null;
  }
): CarryoverManifest | null {
  const files = new Map<string, string>();
  for (const [rel, content] of params.diskFiles) files.set(rel, content);
  for (const [rel, content] of params.partial?.workspace ?? []) {
    if (params.before.get(rel)?.toString("utf8") !== content) files.set(rel, content);
  }
  const carried = Array.from(files.entries()).filter(([rel]) => rel.startsWith("src/"));
  if (carried.length === 0 && !params.partial) return null;

  const failedContinuations = params.parent ? params.parent.failedContinuations + 1 : 0;
  const id = `${new Date().toISOString().replace(/[:.]/g, "-")}-${Math.random().toString(36).slice(2, 6)}`;
  const manifest: CarryoverManifest = {
    version: 1,
    id,
    createdAt: new Date().toISOString(),
    focus: normalizeFocus(params.focus),
    label: params.label,
    reason: params.reason,
    problem: params.problem.slice(0, 2000),
    spentUsd: (params.parent?.spentUsd ?? 0) + params.spentUsd,
    plan: params.partial?.plan ?? params.parent?.plan ?? [],
    moduleName: params.partial?.moduleName ?? params.parent?.moduleName ?? null,
    contract: params.partial?.contract ?? params.parent?.contract ?? null,
    testFilePath: params.partial?.testFilePath ?? params.parent?.testFilePath ?? null,
    testCode: params.partial?.testCode ?? params.parent?.testCode ?? null,
    lastFeedback: params.partial?.lastFeedback?.slice(0, 4000) ?? null,
    files: carried.map(([rel]) => ({ path: rel, baseHash: hashContent(params.before.get(rel)) })),
    status: failedContinuations >= MAX_FAILED_CONTINUATIONS ? "abandoned" : "pending",
    failedContinuations,
    parentId: params.parent?.id ?? null
  };

  const dir = carryoverDir(root, id);
  for (const [rel, content] of carried) {
    const dest = path.join(dir, "files", rel);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, content, "utf8");
  }
  writeManifest(root, manifest);
  if (params.parent) resolveCarryover(root, params.parent, "superseded", id);
  return manifest;
}

/** Writes the carried files into the workspace, but only if none of their base files changed since. */
export function applyCarryover(root: string, manifest: CarryoverManifest): ApplyResult {
  const files = manifest.files.map((f) => f.path);
  if (files.length === 0) return { applied: false, files, reason: "it had no code to restore" };
  for (const file of manifest.files) {
    const abs = path.join(root, file.path);
    const current = fs.existsSync(abs) ? fs.readFileSync(abs) : null;
    if (hashContent(current) !== file.baseHash) {
      return { applied: false, files, reason: `${file.path} has changed since` };
    }
  }
  const dir = carryoverDir(root, manifest.id);
  for (const rel of files) {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.copyFileSync(path.join(dir, "files", rel), abs);
  }
  return { applied: true, files };
}

const REASON_TEXT: Record<CarryoverReason, string> = {
  "spending-limit": "ran out of budget before it finished",
  "not-converged": "did not get its change to pass the sandbox tests",
  "verification-failed": "produced a change that broke Foundry's type-check or test suite",
  other: "was not kept"
};

/** The task-text section that tells the models to finish the earlier attempt. */
export function carryoverPrompt(manifest: CarryoverManifest, result: ApplyResult): string {
  const contract = manifest.contract ? JSON.stringify(manifest.contract, null, 2).slice(0, 3000) : "(none recorded)";
  return [
    `Continue an unfinished earlier attempt instead of starting over.`,
    `On ${manifest.createdAt.slice(0, 10)} a self-improvement round ${REASON_TEXT[manifest.reason]} (about $${manifest.spentUsd.toFixed(2)} of work so far).` +
      (manifest.moduleName ? ` It was working on module "${manifest.moduleName}"${manifest.plan.length > 1 ? ` of the plan ${manifest.plan.join(", ")}` : ""}.` : ""),
    result.applied
      ? `Its partial code is already in the workspace: ${result.files.join(", ")}. Keep what works and finish it.`
      : `Its partial code could not be restored (${result.reason}); use this description as reference only. Files it had changed: ${result.files.join(", ") || "none"}.`,
    `Contract it was implementing:\n${contract}`,
    `Why it stopped:\n${(manifest.lastFeedback || manifest.problem || "unknown").slice(0, 2500)}`,
    manifest.testCode ? `The check it had written (${manifest.testFilePath ?? "test file"}); reuse or improve it:\n${manifest.testCode.slice(0, 4000)}` : ""
  ]
    .filter(Boolean)
    .join("\n\n");
}
