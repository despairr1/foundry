import { exec } from "child_process";
import { promisify } from "util";
import * as fs from "fs";
import * as path from "path";
import { FoundryConfig } from "../config";
import { ConvergenceEngine, ConvergenceRunReport } from "../engine/convergence";
import { JournalEntry, ProjectJournal } from "../engine/journal";
import { rebuildFoundry } from "./ouroboros";
import {
  applyCarryover,
  CarryoverManifest,
  CarryoverReason,
  carryoverPrompt,
  recordFailedContinuation,
  resolveCarryover,
  saveCarryover,
  selectCarryover
} from "./carryover";
import { emitProgress, setSpendReporting } from "../utils/progress";
import { EconomicsEngine } from "../engine/economics";
import { chooseImprovement, findTodos, focusFromCandidate, isGenericFocus, sourceMap } from "./scout";
import { logger } from "../utils/logger";
import { toPosixRelative } from "../utils/paths";

const execAsync = promisify(exec);

/**
 * Self-improvement: Foundry runs its own convergence loop on its own repository.
 * Each round gathers evidence (type-check and test results, recent warnings and
 * errors from .foundry.log, and the history of earlier rounds). Without a real
 * focus it first scouts for one (scout.ts: the architect proposes, the auditor
 * picks), then asks the swarm for that one improvement to src/ and tests/, and keeps it only if the
 * type-check, the full test suite and the build all pass. Rejected rounds are
 * rolled back. Every round, kept or not, is recorded in `.foundry/journal.json`
 * and fed into the next round, so improvements stack and dead ends are not repeated.
 */

const SNAPSHOT_IGNORED_DIRS = new Set([".git", "node_modules", "dist", "__pycache__", ".venv", "venv", "coverage"]);
// Files that change during every run and are not part of the code.
const VOLATILE_FILES = /(^|\/)(\.foundry\.log|\.foundry-freeze\.json)$|\.sqlite(-wal|-shm|-journal)?$/;
const MAX_SNAPSHOT_FILE_BYTES = 4 * 1024 * 1024;
const SELF_CHECKS_DIR = "tests/self-checks/";
const MIN_ROUND_BUDGET_USD = 0.25;
const MAX_CONSECUTIVE_FAILURES = 2;
const ANSI = /\x1b\[[0-9;]*m/g;

export interface SelfImproveOptions {
  focus?: string;
  budgetUsd: number;
  rounds: number;
}

export interface SelfImproveSummary {
  outcome: "done" | "not-finished" | "spending-limit" | "error";
  summary: string;
  kept: number;
  attempted: number;
  filesChanged: string[];
  spentUsd: number;
  problem?: string;
}

interface CheckResult {
  ok: boolean;
  output: string;
}

interface TreeDiff {
  created: string[];
  modified: string[];
  deleted: string[];
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function snapshotTree(root: string): Map<string, Buffer> {
  const out = new Map<string, Buffer>();
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (SNAPSHOT_IGNORED_DIRS.has(entry.name) || entry.name.startsWith(".foundry")) continue;
        walk(full);
      } else if (entry.isFile()) {
        const rel = toPosixRelative(root, full);
        if (VOLATILE_FILES.test(rel) || fs.statSync(full).size > MAX_SNAPSHOT_FILE_BYTES) continue;
        out.set(rel, fs.readFileSync(full));
      }
    }
  };
  walk(root);
  return out;
}

export function diffTree(root: string, before: Map<string, Buffer>): TreeDiff {
  const after = snapshotTree(root);
  const diff: TreeDiff = { created: [], modified: [], deleted: [] };
  for (const [rel, content] of after) {
    const old = before.get(rel);
    if (!old) diff.created.push(rel);
    else if (!old.equals(content)) diff.modified.push(rel);
  }
  for (const rel of before.keys()) {
    if (!after.has(rel)) diff.deleted.push(rel);
  }
  return diff;
}

function allChanged(diff: TreeDiff): string[] {
  return [...diff.created, ...diff.modified, ...diff.deleted];
}

/** tsc never deletes outputs, so removing a source file must also remove what it compiled to in dist/. */
function removeCreatedFiles(root: string, created: string[]): void {
  for (const rel of created) {
    fs.rmSync(path.join(root, rel), { force: true });
    const compiled = /^src\/(.+)\.ts$/.exec(rel);
    if (compiled) {
      for (const ext of [".js", ".js.map", ".d.ts"]) {
        fs.rmSync(path.join(root, "dist", `${compiled[1]}${ext}`), { force: true });
      }
    }
  }
}

function restoreTree(root: string, before: Map<string, Buffer>, diff: TreeDiff): void {
  for (const rel of [...diff.modified, ...diff.deleted]) {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.writeFileSync(abs, before.get(rel)!);
  }
  removeCreatedFiles(root, diff.created);
}

/** The harness may only change its own engine code and add regression checks; it may not weaken existing tests. */
export function scopeViolation(diff: TreeDiff): string | undefined {
  const outside = allChanged(diff).filter((rel) => !rel.startsWith("src/") && !rel.startsWith("tests/"));
  if (outside.length > 0) return `Changed files outside src/ and tests/: ${outside.join(", ")}`;
  const editedTests = [...diff.modified, ...diff.deleted].filter(
    (rel) => rel.startsWith("tests/") && !rel.startsWith(SELF_CHECKS_DIR)
  );
  if (editedTests.length > 0) return `Edited or deleted existing tests, which is not allowed: ${editedTests.join(", ")}`;
  return undefined;
}

const ROUND_IN_PROGRESS_DIR = path.join(".foundry", "round-in-progress");

/**
 * Saves the pre-round tree to disk. If the process is killed mid-round (Stop button, crash, reboot),
 * recoverInterruptedRound() uses it to put Foundry's source back the way it was.
 */
function persistRoundSnapshot(root: string, before: Map<string, Buffer>): void {
  const dir = path.join(root, ROUND_IN_PROGRESS_DIR);
  fs.rmSync(dir, { recursive: true, force: true });
  for (const [rel, content] of before) {
    const dest = path.join(dir, "files", rel);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, content);
  }
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ startedAt: new Date().toISOString(), files: [...before.keys()] }), "utf8");
}

function clearRoundSnapshot(root: string): void {
  fs.rmSync(path.join(root, ROUND_IN_PROGRESS_DIR), { recursive: true, force: true });
}

/**
 * Rolls back a round that never finished. Only files a round may create (under src/ and tests/)
 * are deleted, so anything the user added elsewhere in the meantime is left alone.
 * Returns the restored paths, or null when there was nothing to recover.
 */
export async function recoverInterruptedRound(root: string): Promise<string[] | null> {
  const dir = path.join(root, ROUND_IN_PROGRESS_DIR);
  const manifestPath = path.join(dir, "manifest.json");
  if (!fs.existsSync(manifestPath)) return null;
  const { files } = JSON.parse(fs.readFileSync(manifestPath, "utf8")) as { files: string[] };
  const before = new Map(files.map((rel) => [rel, fs.readFileSync(path.join(dir, "files", rel))] as [string, Buffer]));
  const diff = diffTree(root, before);
  diff.created = diff.created.filter((rel) => rel.startsWith("src/") || rel.startsWith("tests/"));
  const changed = allChanged(diff);
  restoreTree(root, before, diff);
  if (changed.length > 0) await rebuildFoundry(root).catch(() => undefined);
  clearRoundSnapshot(root);
  if (changed.length > 0) {
    logger.warn("SelfImprove", `Rolled back an interrupted round (${changed.length} file(s) restored).`);
    ProjectJournal.load(root).append({
      timestamp: new Date().toISOString(),
      task: "Interrupted self-improvement round",
      mode: "improve",
      outcome: "stopped",
      summary: `The round was interrupted before it was verified, so its changes were rolled back (${changed.join(", ")}).`,
      filesChanged: [],
      modules: [],
      spentUsd: 0,
      limitUsd: 0,
      commits: []
    });
  }
  return changed;
}

function writeBackup(root: string, before: Map<string, Buffer>, diff: TreeDiff): string {
  const id = new Date().toISOString().replace(/[:.]/g, "-");
  const dir = path.join(root, ".foundry", "backups", id);
  for (const rel of [...diff.modified, ...diff.deleted]) {
    const dest = path.join(dir, "files", rel);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.writeFileSync(dest, before.get(rel)!);
  }
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "manifest.json"), JSON.stringify({ createdAt: new Date().toISOString(), ...diff }, null, 2), "utf8");
  return id;
}

async function typecheck(root: string): Promise<CheckResult> {
  try {
    await rebuildFoundry(root, true);
    return { ok: true, output: "" };
  } catch (err) {
    return { ok: false, output: errorMessage(err) };
  }
}

async function runTestSuite(root: string): Promise<CheckResult> {
  if (!fs.existsSync(path.join(root, "node_modules", "vitest"))) {
    return { ok: true, output: "(vitest is not installed; test suite skipped)" };
  }
  try {
    await execAsync("npm test", {
      cwd: root,
      timeout: 15 * 60 * 1000,
      maxBuffer: 20 * 1024 * 1024,
      windowsHide: true,
      env: { ...process.env, FOUNDRY_DASHBOARD: "false" }
    });
    return { ok: true, output: "" };
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message?: string };
    const output = `${e.stdout ?? ""}\n${e.stderr ?? ""}`.replace(ANSI, "").trim() || e.message || "npm test failed";
    return { ok: false, output: output.slice(-4000) };
  }
}

/** The lines that say what actually failed (failing suites/tests, assertion and compiler errors), without log noise. */
export function failureHighlights(output: string, max = 6): string {
  const picked: string[] = [];
  for (const raw of output.split(/\r?\n/)) {
    const line = raw.trim();
    if (/^(FAIL|×)\s|^(Assertion)?Error[:\s]|error TS\d+/.test(line) && !picked.includes(line)) {
      picked.push(line.slice(0, 240));
      if (picked.length >= max) break;
    }
  }
  return picked.join("; ");
}

async function healthCheck(root: string): Promise<{ typecheck: CheckResult; tests: CheckResult }> {
  const tc = await typecheck(root);
  const tests = tc.ok ? await runTestSuite(root) : { ok: false, output: "(not run: the type-check failed)" };
  return { typecheck: tc, tests };
}

function emitHealth(health: { typecheck: CheckResult; tests: CheckResult }, problems?: string[]): void {
  const failing = !health.typecheck.ok ? health.typecheck.output : !health.tests.ok ? health.tests.output : "";
  emitProgress({
    type: "health",
    typecheck: health.typecheck.ok,
    tests: health.tests.ok,
    detail: failing ? failureHighlights(failing, 3) : undefined,
    ...(problems ? { problems } : {})
  });
}

/** The latest health check, kept at .foundry/health.json for the GUI's Improve page (its Diagnostics card). */
export interface HealthReport {
  checkedAt: string;
  typecheck: { ok: boolean; detail: string; output: string };
  tests: { ok: boolean; detail: string; output: string };
  problems: string[];
}

export function healthReportPath(root: string): string {
  return path.join(root, ".foundry", "health.json");
}

export function writeHealthReport(root: string, health: { typecheck: CheckResult; tests: CheckResult }, problems: string[]): HealthReport {
  const describe = (check: CheckResult): HealthReport["typecheck"] => ({
    ok: check.ok,
    detail: check.ok ? "" : failureHighlights(check.output, 6),
    output: check.ok ? "" : check.output.slice(-3000)
  });
  const report: HealthReport = {
    checkedAt: new Date().toISOString(),
    typecheck: describe(health.typecheck),
    tests: describe(health.tests),
    problems
  };
  try {
    const file = healthReportPath(root);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(report, null, 2), "utf8");
  } catch (err) {
    logger.warn("SelfImprove", `Could not write the health report: ${errorMessage(err)}`);
  }
  return report;
}

/** `self-improve --check`: the health check and the log scan only. No model is called; nothing is changed. */
export async function checkHealthOnly(root: string): Promise<HealthReport> {
  emitProgress({ type: "self-step", step: "health", round: 1, rounds: 1 });
  const health = await healthCheck(root);
  const problems = recentLogProblems(root);
  emitHealth(health, problems);
  logger.info(
    "SelfImprove",
    `Health: type-check ${health.typecheck.ok ? "passing" : "FAILING"}, tests ${health.tests.ok ? "passing" : "FAILING"}, ` +
      `${problems.length} recent warning(s)/error(s) in the log.`
  );
  return writeHealthReport(root, health, problems);
}

/** Recent distinct warnings and errors from Foundry's own log: the main evidence of what to improve. */
export function recentLogProblems(root: string, max = 25): string[] {
  const logPath = process.env.FOUNDRY_LOG_PATH ?? path.join(root, ".foundry.log");
  let text: string;
  try {
    const size = fs.statSync(logPath).size;
    const fd = fs.openSync(logPath, "r");
    const length = Math.min(size, 512 * 1024);
    const buffer = Buffer.alloc(length);
    fs.readSync(fd, buffer, 0, length, size - length);
    fs.closeSync(fd);
    text = buffer.toString("utf8");
  } catch {
    return [];
  }
  const seen = new Set<string>();
  const problems: string[] = [];
  const lines = text.split(/\r?\n/).filter((l) => /\] \[(WARN|ERROR)\] /.test(l) && !l.includes("[SelfImprove]"));
  for (let i = lines.length - 1; i >= 0 && problems.length < max; i -= 1) {
    const message = lines[i].replace(/^\[[\d:.]+\] /, "").slice(0, 300);
    if (seen.has(message)) continue;
    seen.add(message);
    problems.push(message);
  }
  return problems.reverse();
}

type FocusSource = "user" | "scout" | "none";

function composeTask(
  focus: string | undefined,
  focusSource: FocusSource,
  health: { typecheck: CheckResult; tests: CheckResult },
  problems: string[],
  history: string
): string {
  const evidence = [
    `Type-check: ${health.typecheck.ok ? "passing" : `FAILING (${failureHighlights(health.typecheck.output)})\n${health.typecheck.output.slice(0, 2000)}`}`,
    `Test suite: ${health.tests.ok ? "passing" : `FAILING (${failureHighlights(health.tests.output)})\n${health.tests.output.slice(-2000)}`}`,
    problems.length > 0
      ? `Recent warnings and errors from Foundry's log (oldest first):\n${problems.map((p) => `- ${p}`).join("\n")}`
      : "Recent warnings and errors from Foundry's log: none recorded."
  ].join("\n\n");

  return [
    `Improve Foundry itself. This repository is Foundry, the autonomous coding engine (TypeScript on Node.js, CommonJS) that is running this task: you are improving the harness that plans, tests and writes code, not a user project.`,
    [
      `Rules:`,
      `- Make ONE focused, low-risk improvement. Prefer fixing a real problem shown in the evidence below (failing checks first, then recurring warnings/errors). If there is no evidence of problems, improve how reliably Foundry parses model output, applies patches, or runs tests.`,
      `- Plan exactly ONE module: one implementation file under src/ (the file the focus names, when it names one) plus its check file. Keep the change small and finish it in one pass.`,
      `- Change only files under src/. Keep every CLI command and flag working, and keep exported names and signatures compatible.`,
      `- Put the module's test at ${SELF_CHECKS_DIR}<module_name>.check.ts: a standalone node:assert script, not a *.test.ts file.`,
      `- Do not edit or delete any existing test.`
    ].join("\n"),
    focus?.trim()
      ? `${focusSource === "scout" ? "The improvement chosen for this round (Foundry scouted the evidence and its auditor picked this one):" : "What the user wants improved:"}\n${focus.trim()}`
      : `The user gave no specific focus; choose based on the evidence.`,
    `Evidence:\n${evidence}`,
    history
  ]
    .filter(Boolean)
    .join("\n\n");
}

function carryoverReason(budgetHit: boolean, report: ConvergenceRunReport | null, problem?: string): CarryoverReason {
  if (budgetHit) return "spending-limit";
  if (!report?.converged) return "not-converged";
  if (problem?.startsWith("The change broke") || problem?.startsWith("The rebuild failed")) return "verification-failed";
  return "other";
}

export async function runSelfImprovement(baseConfig: FoundryConfig, options: SelfImproveOptions): Promise<SelfImproveSummary> {
  const root = baseConfig.foundryRootDir;
  // "Improve yourself" and the like are a request, not a focus: such rounds scout for one (see scout.ts).
  const explicitFocus = isGenericFocus(options.focus) ? undefined : options.focus?.trim();
  const label = explicitFocus || "Automatic self-improvement";
  const rounds = Math.max(1, options.rounds);
  let spentUsd = 0;
  let kept = 0;
  let attempted = 0;
  let consecutiveFailures = 0;
  let budgetHit = false;
  let lastProblem: string | undefined;
  const filesChanged: string[] = [];

  emitProgress({
    type: "start",
    mode: "self-improve",
    task: label,
    target: root,
    limitUsd: options.budgetUsd,
    historyEntries: ProjectJournal.load(root).getEntries().length,
    rounds
  });
  logger.info("SelfImprove", `Self-improvement of ${root}: up to ${rounds} round(s), spending limit $${options.budgetUsd.toFixed(2)}.`);
  await recoverInterruptedRound(root);

  emitProgress({ type: "self-step", step: "health", round: 1, rounds });
  let health = await healthCheck(root);
  emitHealth(health);
  writeHealthReport(root, health, recentLogProblems(root));
  logger.info("SelfImprove", `Health: type-check ${health.typecheck.ok ? "passing" : "FAILING"}, tests ${health.tests.ok ? "passing" : "FAILING"}.`);

  for (let round = 1; round <= rounds; round += 1) {
    const remaining = options.budgetUsd - spentUsd;
    if (remaining < MIN_ROUND_BUDGET_USD) {
      if (round === 1) lastProblem = `A round needs at least $${MIN_ROUND_BUDGET_USD.toFixed(2)} of budget.`;
      break;
    }
    setSpendReporting(spentUsd, options.budgetUsd);

    const journal = ProjectJournal.load(root);
    const problems = recentLogProblems(root);
    emitProgress({
      type: "self-step",
      step: "evidence",
      round,
      rounds,
      detail: `${problems.length} recent warning(s)/error(s) in the log · ${journal.getEntries().length} earlier round(s) remembered`
    });
    const before = snapshotTree(root);
    persistRoundSnapshot(root, before);

    // Carried-over partial work goes in after the restore point, so a failed round still rolls it back.
    const carry = selectCarryover(root, explicitFocus);
    let carryNote = "";
    if (carry) {
      const applied = applyCarryover(root, carry);
      carryNote = carryoverPrompt(carry, applied);
      emitProgress({
        type: "self-step",
        step: "continuing",
        round,
        rounds,
        detail:
          `from ${carry.createdAt.slice(0, 10)} · $${carry.spentUsd.toFixed(2)} of earlier work · ` +
          (applied.applied ? `${applied.files.length} file(s) restored` : `reference only (${applied.reason})`)
      });
      logger.info("SelfImprove", `Continuing carryover ${carry.id} (${applied.applied ? "files restored" : `reference only: ${applied.reason}`}).`);
    }
    // No real focus and nothing to continue: scout for one small, concrete improvement before the expensive loop.
    let focus = explicitFocus;
    let focusSource: FocusSource = explicitFocus ? "user" : "none";
    let roundLabel = label;
    let scoutSpent = 0;
    if (!carry && !explicitFocus) {
      emitProgress({ type: "self-step", step: "scouting", round, rounds });
      const scoutEconomics = new EconomicsEngine(remaining, baseConfig.freezeStatePath, false);
      try {
        const choice = await chooseImprovement({ ...baseConfig, maxBudgetUsd: remaining }, scoutEconomics, {
          health: {
            typecheck: health.typecheck.ok,
            tests: health.tests.ok,
            detail: failureHighlights(!health.typecheck.ok ? health.typecheck.output : !health.tests.ok ? health.tests.output : "")
          },
          problems,
          history: journal.formatForPrompt(),
          sourceMap: sourceMap(root),
          todos: findTodos(root)
        });
        focus = focusFromCandidate(choice.candidate);
        focusSource = "scout";
        roundLabel = choice.candidate.title;
        emitProgress({
          type: "self-step",
          step: "scouting",
          round,
          rounds,
          detail: `${choice.candidates.length} candidate(s): ${choice.candidates.map((c) => c.title).join(" · ")}`
        });
        emitProgress({
          type: "self-step",
          step: "chosen",
          round,
          rounds,
          detail: choice.reason ? `${choice.candidate.title} (${choice.reason})` : choice.candidate.title
        });
        logger.info("SelfImprove", `Scouting chose: ${choice.candidate.title} (${choice.candidate.files.join(", ")}).`);
      } catch (err) {
        scoutSpent = scoutEconomics.getTotalSpentUsd();
        if (err instanceof Error && err.name === "BudgetExceededError") {
          budgetHit = true;
          lastProblem = errorMessage(err);
          spentUsd += scoutSpent;
          clearRoundSnapshot(root);
          logger.warn("SelfImprove", `Round ${round}: the budget ran out while scouting (${lastProblem}).`);
          break;
        }
        logger.warn("SelfImprove", `Scouting did not produce a choice (${errorMessage(err)}); improving from the evidence directly.`);
      }
      scoutSpent = scoutEconomics.getTotalSpentUsd();
      spentUsd += scoutSpent;
      setSpendReporting(spentUsd, options.budgetUsd);
    }
    const roundBudget = remaining - scoutSpent;
    if (roundBudget < MIN_ROUND_BUDGET_USD) {
      lastProblem = `Scouting used the remaining budget; a round needs at least $${MIN_ROUND_BUDGET_USD.toFixed(2)} more.`;
      budgetHit = true;
      clearRoundSnapshot(root);
      break;
    }
    const task = [composeTask(focus, focusSource, health, problems, journal.formatForPrompt()), carryNote].filter(Boolean).join("\n\n");

    emitProgress({ type: "self-step", step: "improving", round, rounds });
    // The harness verifies and rolls back by itself, so no git commits, and Ouroboros must not patch concurrently.
    const roundConfig: FoundryConfig = { ...baseConfig, maxBudgetUsd: roundBudget, autoGitCommit: false, ouroborosEnabled: false };
    const engine = new ConvergenceEngine(roundConfig, false);
    let report: ConvergenceRunReport | null = null;
    let problem: string | undefined;
    try {
      report = await engine.executePatch(task, root);
    } catch (err) {
      budgetHit = err instanceof Error && err.name === "BudgetExceededError";
      problem = errorMessage(err);
    }
    const roundSpent = engine.getTotalSpentUsd() + scoutSpent;
    spentUsd += engine.getTotalSpentUsd();
    attempted += 1;

    const diff = diffTree(root, before);
    const changed = allChanged(diff);
    let keptThisRound = false;
    let builtThisRound = false;

    if (!problem && !report?.converged) {
      problem = report?.error ?? "The engine did not converge on a change.";
    } else if (!problem && changed.length === 0) {
      problem = "The engine converged without changing any files.";
    } else if (!problem) {
      problem = scopeViolation(diff);
    }

    if (!problem) {
      emitProgress({ type: "self-step", step: "verifying", round, rounds });
      const check = await healthCheck(root);
      emitHealth(check);
      writeHealthReport(root, check, recentLogProblems(root));
      if (!check.typecheck.ok) {
        problem = `The change broke the type-check: ${failureHighlights(check.typecheck.output) || "see output"}\n\n${check.typecheck.output.slice(0, 1500)}`;
      } else if (!check.tests.ok) {
        problem = `The change broke the test suite: ${failureHighlights(check.tests.output) || "see output"}\n\n${check.tests.output.slice(-1500)}`;
      } else {
        emitProgress({ type: "self-step", step: "updating", round, rounds });
        try {
          builtThisRound = true;
          await rebuildFoundry(root);
          keptThisRound = true;
          health = check;
        } catch (err) {
          problem = `The rebuild failed: ${errorMessage(err).slice(0, 1500)}`;
        }
      }
    }

    let backupId: string | undefined;
    let summary: string;
    let saved: CarryoverManifest | null = null;
    if (keptThisRound) {
      backupId = writeBackup(root, before, diff);
      kept += 1;
      consecutiveFailures = 0;
      filesChanged.push(...changed.filter((f) => !filesChanged.includes(f)));
      summary = `Kept: ${changed.length} file(s) changed (${changed.slice(0, 6).join(", ")}${changed.length > 6 ? ", ..." : ""}).`;
      if (carry) {
        resolveCarryover(root, carry, "consumed", backupId);
        summary += " It finished the unfinished earlier round.";
      }
      logger.info("SelfImprove", `Round ${round}: ${summary} Foundry was rebuilt with the change.`);
    } else {
      // Save the best partial work before rolling back, so the next round can continue it.
      if (problem !== "The engine converged without changing any files.") {
        const diskFiles = new Map<string, string>();
        for (const rel of [...diff.created, ...diff.modified]) {
          diskFiles.set(rel, fs.readFileSync(path.join(root, rel), "utf8"));
        }
        try {
          saved = saveCarryover(root, {
            before,
            diskFiles,
            partial: engine.getPartialProgress(),
            label: roundLabel,
            focus: explicitFocus,
            reason: carryoverReason(budgetHit, report, problem),
            problem: problem ?? "",
            spentUsd: roundSpent,
            parent: carry
          });
        } catch (err) {
          logger.warn("SelfImprove", `Could not save this round's partial work: ${errorMessage(err)}`);
        }
      }
      if (carry && !saved) recordFailedContinuation(root, carry);
      if (saved) {
        emitProgress({
          type: "self-step",
          step: "saving",
          round,
          rounds,
          detail:
            saved.status === "pending"
              ? `saved ${saved.files.length} file(s) of partial work for the next round`
              : `stopped carrying this work forward after ${saved.failedContinuations} failed continuations`
        });
      }
      if (changed.length > 0) {
        emitProgress({ type: "self-step", step: "reverting", round, rounds });
        restoreTree(root, before, diff);
        if (builtThisRound) await rebuildFoundry(root).catch(() => undefined);
      }
      consecutiveFailures += 1;
      lastProblem = problem;
      summary = `Not kept: ${(problem ?? "unknown problem").split("\n")[0].slice(0, 200)}`;
      if (saved?.status === "pending") summary += " Partial work saved for the next round.";
      else if (saved?.status === "abandoned") summary += " Stopped carrying this work forward after repeated failures.";
      logger.warn("SelfImprove", `Round ${round}: ${summary}`);
    }
    clearRoundSnapshot(root);

    const outcome: JournalEntry["outcome"] = keptThisRound ? "done" : budgetHit ? "spending-limit" : "not-finished";
    try {
      journal.append({
        timestamp: new Date().toISOString(),
        task: roundLabel,
        mode: "improve",
        outcome,
        summary,
        filesChanged: keptThisRound ? changed : [],
        modules: (report?.modules ?? []).map((m) => ({ name: m.moduleName, status: m.status })),
        spentUsd: roundSpent,
        limitUsd: remaining,
        commits: [],
        problem: problem?.slice(0, 2000),
        backupId,
        carryoverId: saved?.status === "pending" ? saved.id : undefined,
        continuedFrom: carry?.id
      });
    } catch (err) {
      logger.warn("SelfImprove", `Could not update the improvement journal: ${errorMessage(err)}`);
    }
    emitProgress({ type: "round-done", round, rounds, kept: keptThisRound, summary });

    if (budgetHit || consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) break;
  }
  setSpendReporting(0, null);

  const outcome: SelfImproveSummary["outcome"] = budgetHit ? "spending-limit" : kept > 0 ? "done" : "not-finished";
  const summary =
    kept > 0
      ? `Kept ${kept} of ${attempted} improvement(s); Foundry has been rebuilt with them.`
      : attempted > 0
        ? `No improvement was kept after ${attempted} attempt(s); everything was rolled back.`
        : "No round was run.";
  return { outcome, summary, kept, attempted, filesChanged, spentUsd, problem: kept > 0 ? undefined : lastProblem };
}

/** Rolls back the most recent kept self-improvement that has not been undone yet, then rebuilds. */
export async function undoLastImprovement(config: FoundryConfig): Promise<{ summary: string; filesChanged: string[] }> {
  const root = config.foundryRootDir;
  const journal = ProjectJournal.load(root);
  const entries = journal.getEntries();
  const undone = new Set(entries.filter((e) => e.outcome === "undone").map((e) => e.backupId));
  const target = [...entries].reverse().find((e) => e.outcome === "done" && e.backupId && !undone.has(e.backupId));
  if (!target || !target.backupId) {
    throw new Error("There is no kept self-improvement to undo.");
  }

  const dir = path.join(root, ".foundry", "backups", target.backupId);
  const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8")) as TreeDiff;
  for (const rel of [...manifest.modified, ...manifest.deleted]) {
    const abs = path.join(root, rel);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    fs.copyFileSync(path.join(dir, "files", rel), abs);
  }
  removeCreatedFiles(root, manifest.created);
  await rebuildFoundry(root);

  const filesChanged = allChanged(manifest);
  const summary = `Rolled back the improvement from ${target.timestamp.slice(0, 10)} ("${target.summary}"). The user undid it: do not redo it.`;
  journal.append({
    timestamp: new Date().toISOString(),
    task: `Undo: ${target.task}`,
    mode: "improve",
    outcome: "undone",
    summary,
    filesChanged,
    modules: [],
    spentUsd: 0,
    limitUsd: 0,
    commits: [],
    backupId: target.backupId
  });
  logger.info("SelfImprove", summary);
  return { summary, filesChanged };
}
