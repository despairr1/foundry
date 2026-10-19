import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { ASTParser } from "../src/ast/parser";
import { SemanticPatcher } from "../src/ast/semantic_patcher";
import { EconomicsEngine, BudgetExceededError } from "../src/engine/economics";
import { loadConfig } from "../src/config";
import { ConvergenceEngine } from "../src/engine/convergence";

describe("Foundry Autonomous Engine Core Verification", () => {
  it("replaces functions via Tree-sitter byte offsets without line numbers", () => {
    const parser = new ASTParser();
    const patcher = new SemanticPatcher(parser);

    const initialCode = [
      `export function initialize_pool(size: number): number {`,
      `  return size;`,
      `}`,
      ``,
      `export function close_pool(): boolean {`,
      `  return true;`,
      `}`
    ].join("\n");

    const result = patcher.applyPatch(initialCode, "typescript", {
      filePath: "src/pool.ts",
      action: "REPLACE_FUNCTION",
      target: "initialize_pool",
      content: [
        `export function initialize_pool(size: number): number {`,
        `  if (size <= 0) throw new Error("Invalid pool size");`,
        `  return size * 2;`,
        `}`
      ].join("\n")
    });

    expect(result.success).toBe(true);
    expect(result.hasSyntaxErrorAfterPatch).toBe(false);
    expect(result.updatedSource).toContain(`Invalid pool size`);
    expect(result.updatedSource).toContain(`export function close_pool()`);
  });

  it("trips the token circuit breaker and writes freeze state when USD budget is exceeded", () => {
    const freezeFile = path.join(os.tmpdir(), `foundry-freeze-${Date.now()}.json`);
    const economics = new EconomicsEngine(0.01, freezeFile, false);

    expect(() => {
      economics.recordUsage("claude", "claude-3-opus-20240229", 5000, 5000);
    }).toThrow(BudgetExceededError);

    expect(fs.existsSync(freezeFile)).toBe(true);
    const dump = JSON.parse(fs.readFileSync(freezeFile, "utf8")) as {
      totalSpentUsd: number;
    };
    expect(dump.totalSpentUsd).toBeGreaterThan(0.01);
    fs.rmSync(freezeFile, { force: true });
  });

  it("converges end-to-end on a TypeScript module with mutation verification", async () => {
    process.env.FOUNDRY_FORCE_LOCAL_SANDBOX = "true";

    const tempWorkspace = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-e2e-"));
    const sqlitePath = path.join(tempWorkspace, ".foundry-memory.sqlite");
    const freezePath = path.join(tempWorkspace, ".foundry-freeze.json");
    fs.mkdirSync(path.join(tempWorkspace, "src"));
    fs.writeFileSync(path.join(tempWorkspace, "src", "unrelated.ts"), "export const untouched = true;\n", "utf8");

    const config = loadConfig({
      offlineMode: true,
      maxBudgetUsd: 5.0,
      sqliteDbPath: sqlitePath,
      freezeStatePath: freezePath,
      interactiveDashboard: false
    });

    const engine = new ConvergenceEngine(config, false);
    const report = await engine.executeBuild(
      "Build symbol execute_core_pipeline that validates non-negative input and computes 2*x + 1.",
      tempWorkspace
    );

    expect(report.converged).toBe(true);
    expect(report.winningBranch).toBe("Alpha");
    expect(report.modifiedFiles.sort()).toEqual(["src/module.ts", "tests/module.test.ts"]);
    expect(report.totalSpentUsd).toBe(0);
    expect(report.committedHashes).toHaveLength(1);
    expect(fs.readFileSync(path.join(tempWorkspace, "src", "module.ts"), "utf8")).toContain("export function execute_core_pipeline");
    fs.rmSync(tempWorkspace, { recursive: true, force: true });
  });
});