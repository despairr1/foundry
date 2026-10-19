import { monitorEventLoopDelay, IntervalHistogram } from "perf_hooks";
import { exec, execFile } from "child_process";
import { promisify } from "util";
import * as fs from "fs";
import * as path from "path";
import { FoundryConfig } from "../config";
import { ASTParser } from "../ast/parser";
import { SemanticPatcher, SemanticPatch } from "../ast/semantic_patcher";
import { EconomicsEngine } from "../engine/economics";
import { completeWithClaude } from "../swarm/providers";
import { parseSemanticPatches } from "../swarm/astra_synthesizer";
import { logger } from "../utils/logger";
import { resolveInsideRoot, toPosixRelative } from "../utils/paths";

const execFileAsync = promisify(execFile);
const execAsync = promisify(exec);

export interface PerformanceTelemetry {
  eventLoopLagMeanMs: number;
  eventLoopLagMaxMs: number;
  astParseDurationMs: number;
  heapUsedMb: number;
  bottleneckDetected: boolean;
  bottleneckReason?: string;
  bottleneckKind?: "EVENT_LOOP_LAG" | "AST_PARSE" | "HEAP";
}

// One verified self-modification per run: each attempt costs API spend and a full rebuild.
const MAX_SELF_MODIFICATIONS_PER_RUN = 1;

const SELF_TARGETS: Record<NonNullable<PerformanceTelemetry["bottleneckKind"]>, string> = {
  EVENT_LOOP_LAG: "src/ast/parser.ts",
  AST_PARSE: "src/ast/parser.ts",
  HEAP: "src/lsp/multiplexer.ts"
};

/** Type-checks, then compiles Foundry into dist/. Throws with compiler output on failure. */
export async function rebuildFoundry(rootDir: string, typecheckOnly = false): Promise<void> {
  const tscPath = require.resolve("typescript/bin/tsc", { paths: [rootDir] });
  const args = [tscPath, "-p", path.join(rootDir, "tsconfig.json")];
  if (typecheckOnly) args.push("--noEmit");
  try {
    await execFileAsync(process.execPath, args, { cwd: rootDir, timeout: 180000, windowsHide: true });
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message?: string };
    throw new Error(`tsc failed:\n${(e.stdout || e.stderr || e.message || "").slice(0, 2000)}`);
  }
}

export class OuroborosMonitor {
  private config: FoundryConfig;
  private astParser: ASTParser;
  private patcher: SemanticPatcher;
  private economics: EconomicsEngine;
  private histogram: IntervalHistogram;
  private generation = 1;
  private attemptsThisRun = 0;

  constructor(config: FoundryConfig, astParser: ASTParser, patcher: SemanticPatcher, economics: EconomicsEngine) {
    this.config = config;
    this.astParser = astParser;
    this.patcher = patcher;
    this.economics = economics;
    this.histogram = monitorEventLoopDelay({ resolution: 20 });
    this.histogram.enable();
  }

  public getGeneration(): number {
    return this.generation;
  }

  public inspectTelemetry(): PerformanceTelemetry {
    const meanLagMs = this.histogram.mean / 1e6;
    const maxLagMs = this.histogram.max / 1e6;
    const astParseDurationMs = this.astParser.getLastParseDurationMs();
    const heapUsedMb = process.memoryUsage().heapUsed / (1024 * 1024);

    this.histogram.reset();

    let bottleneckKind: PerformanceTelemetry["bottleneckKind"];
    let bottleneckReason: string | undefined;

    if (meanLagMs > this.config.ouroborosMaxEventLoopLagMs) {
      bottleneckKind = "EVENT_LOOP_LAG";
      bottleneckReason = `Event-loop mean lag ${meanLagMs.toFixed(1)}ms exceeded threshold ${this.config.ouroborosMaxEventLoopLagMs}ms`;
    } else if (astParseDurationMs > this.config.ouroborosMaxAstParseMs) {
      bottleneckKind = "AST_PARSE";
      bottleneckReason = `AST parse duration ${astParseDurationMs.toFixed(1)}ms exceeded threshold ${this.config.ouroborosMaxAstParseMs}ms`;
    } else if (heapUsedMb > this.config.ouroborosMaxHeapMb) {
      bottleneckKind = "HEAP";
      bottleneckReason = `Heap usage ${heapUsedMb.toFixed(1)}MB exceeded threshold ${this.config.ouroborosMaxHeapMb}MB`;
    }

    return {
      eventLoopLagMeanMs: meanLagMs,
      eventLoopLagMaxMs: maxLagMs,
      astParseDurationMs,
      heapUsedMb,
      bottleneckDetected: bottleneckKind !== undefined,
      bottleneckReason,
      bottleneckKind
    };
  }

  /**
   * On a detected bottleneck, asks the architect model for a performance patch
   * to Foundry's own source, verifies it (type-check, test suite, build) and
   * stages it for the next run. Never restarts the process mid-build: a restart
   * would discard the run's progress and reset the in-memory budget ledger.
   */
  public async checkAndSelfModifyIfNeeded(): Promise<boolean> {
    const telemetry = this.inspectTelemetry();
    if (!telemetry.bottleneckDetected || !telemetry.bottleneckKind) {
      return false;
    }

    logger.warn("Ouroboros", `Bottleneck detected: ${telemetry.bottleneckReason}.`);
    if (!this.config.ouroborosEnabled) {
      logger.info("Ouroboros", "Self-modification disabled (OUROBOROS_AUTO_HEAL=false); continuing.");
      return false;
    }
    if (this.attemptsThisRun >= MAX_SELF_MODIFICATIONS_PER_RUN) {
      return false;
    }
    if (this.config.offlineMode || !this.config.anthropicApiKey) {
      logger.info("Ouroboros", "Self-modification needs the architect model (ANTHROPIC_API_KEY, not offline); skipping.");
      return false;
    }
    this.attemptsThisRun += 1;

    const relTarget = SELF_TARGETS[telemetry.bottleneckKind];
    const absTarget = resolveInsideRoot(this.config.foundryRootDir, relTarget);
    if (!fs.existsSync(absTarget)) {
      logger.warn("Ouroboros", `Self source file ${absTarget} not accessible; skipping self-patch.`);
      return false;
    }

    logger.updateState({ ouroborosStatus: "BOTTLENECK_DETECTED" });
    let patches: SemanticPatch[];
    try {
      patches = await completeWithClaude<SemanticPatch[]>(this.config, this.economics, {
        label: "Ouroboros",
        prompt: [
          `You are Ouroboros, the self-optimization module of the Foundry engine (TypeScript on Node.js, CommonJS).`,
          `Runtime telemetry: ${telemetry.bottleneckReason}. Event-loop max lag ${telemetry.eventLoopLagMaxMs.toFixed(
            1
          )}ms, heap ${telemetry.heapUsedMb.toFixed(1)}MB.`,
          `Source of ${relTarget}:\n${fs.readFileSync(absTarget, "utf8")}`,
          [
            `Propose a performance improvement for this file only.`,
            `Keep every exported name, signature and observable behavior unchanged, and keep telemetry honest: measurement code must still report real values.`,
            `If no safe improvement exists, return {"patches": []}.`,
            `Respond with ONLY JSON: {"patches": [{"filePath": "${relTarget}", "action": "REPLACE_FUNCTION" | "REPLACE_CLASS" | "REPLACE_NODE" | "INSERT_AFTER" | "INSERT_BEFORE" | "UPSERT_IMPORT", "target": string, "content": string}]}`
          ].join("\n")
        ].join("\n\n"),
        parse: (text) => (/"patches"\s*:\s*\[\s*\]/.test(text) ? [] : parseSemanticPatches(text))
      });
    } catch (err) {
      if (err instanceof Error && err.name === "BudgetExceededError") throw err;
      logger.warn("Ouroboros", `Self-patch generation failed: ${err instanceof Error ? err.message : String(err)}`);
      logger.updateState({ ouroborosStatus: "NOMINAL" });
      return false;
    }

    const scoped = patches.filter((p) => p.filePath === relTarget);
    if (scoped.length === 0) {
      logger.info("Ouroboros", "Architect found no safe self-optimization; continuing unchanged.");
      logger.updateState({ ouroborosStatus: "NOMINAL" });
      return false;
    }
    return this.applyVerifiedSelfPatch(scoped, telemetry.bottleneckReason ?? "bottleneck");
  }

  /**
   * Applies patches to Foundry's own src/ tree, then gates them on a type-check,
   * the test suite (when installed) and a full build. Any failure restores the
   * original sources and rebuilds, so dist/ never keeps output from a rejected patch.
   */
  public async applyVerifiedSelfPatch(patches: SemanticPatch[], reason: string): Promise<boolean> {
    const root = this.config.foundryRootDir;
    logger.updateState({ ouroborosStatus: "SELF_PATCHING" });

    const originals = new Map<string, string>();
    const updated = new Map<string, string>();
    try {
      for (const patch of patches) {
        const absPath = resolveInsideRoot(root, patch.filePath);
        if (!toPosixRelative(root, absPath).startsWith("src/")) {
          throw new Error(`Self-patches may only touch src/: ${patch.filePath}`);
        }
        if (!originals.has(absPath)) originals.set(absPath, fs.readFileSync(absPath, "utf8"));
        const current = updated.get(absPath) ?? originals.get(absPath)!;
        const res = this.patcher.applyPatch(current, "typescript", patch);
        if (!res.success) throw new Error(res.errorDetails ?? `Patch on ${patch.filePath} failed`);
        updated.set(absPath, res.updatedSource);
      }
    } catch (err) {
      logger.error("Ouroboros", `Self-patch rejected before writing: ${err instanceof Error ? err.message : String(err)}`);
      logger.updateState({ ouroborosStatus: "NOMINAL" });
      return false;
    }

    const backupDir = path.join(root, ".foundry-ouroboros", `gen-${this.generation}-${Date.now()}`);
    for (const [absPath, content] of originals) {
      const backupPath = path.join(backupDir, toPosixRelative(root, absPath));
      fs.mkdirSync(path.dirname(backupPath), { recursive: true });
      fs.writeFileSync(backupPath, content, "utf8");
    }
    for (const [absPath, content] of updated) {
      fs.writeFileSync(absPath, content, "utf8");
    }

    logger.updateState({ ouroborosStatus: "RECOMPILING" });
    try {
      await rebuildFoundry(root, true);
      if (fs.existsSync(path.join(root, "node_modules", "vitest"))) {
        await execAsync("npm test", { cwd: root, timeout: 600000, windowsHide: true, env: { ...process.env, FOUNDRY_DASHBOARD: "false" } });
      }
      await rebuildFoundry(root);
    } catch (err) {
      for (const [absPath, content] of originals) {
        fs.writeFileSync(absPath, content, "utf8");
      }
      const detail = err instanceof Error ? err.message : String(err);
      try {
        await rebuildFoundry(root);
      } catch (rebuildErr) {
        logger.error("Ouroboros", `Rebuild after revert failed: ${String(rebuildErr)}`);
      }
      logger.warn("Ouroboros", `Self-patch failed verification and was reverted: ${detail.slice(0, 500)}`);
      logger.updateState({ ouroborosStatus: "NOMINAL" });
      return false;
    }

    this.generation += 1;
    logger.updateState({ ouroborosStatus: "UPGRADE_STAGED", ouroborosGeneration: this.generation });
    logger.info(
      "Ouroboros",
      `Self-modification v1.0.${this.generation} (${reason}) passed type-check, tests and build. ` +
        `It takes effect on the next Foundry run; originals saved in ${backupDir}.`
    );
    return true;
  }

  public stop(): void {
    this.histogram.disable();
  }
}
