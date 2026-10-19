import { afterEach, describe, it, expect, vi } from "vitest";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import Database from "better-sqlite3";
import { loadConfig } from "../src/config";
import { ConvergenceEngine } from "../src/engine/convergence";
import { BudgetExceededError, EconomicsEngine } from "../src/engine/economics";
import { LSPMultiplexer } from "../src/lsp/multiplexer";
import { AstraSynthesizer } from "../src/swarm/astra_synthesizer";
import { ClaudeArchitect, parseArchitectureContracts, parseBranchModuleDesign } from "../src/swarm/claude_architect";

const TWO_MODULE_SPEC = [
  "Build symbol execute_core_pipeline that validates non-negative input and computes 2*x + 1.",
  "Then build symbol pair_pipeline, which imports execute_core_pipeline and returns execute_core_pipeline(x) + execute_core_pipeline(x + 1)."
].join("\n");

function makeWorkspace(): { dir: string; sqlitePath: string; freezePath: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-modules-"));
  return { dir, sqlitePath: path.join(dir, ".foundry-memory.sqlite"), freezePath: path.join(dir, ".foundry-freeze.json") };
}

function git(cwd: string, args: string[]): string {
  return execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("multi-module convergence (offline)", () => {
  it("converges module 2 on top of module 1, with one commit and one episode per module", async () => {
    process.env.FOUNDRY_FORCE_LOCAL_SANDBOX = "true";
    const { dir, sqlitePath, freezePath } = makeWorkspace();
    const designSpy = vi.spyOn(ClaudeArchitect.prototype, "designBranchArchitecture");
    const lspSpy = vi.spyOn(LSPMultiplexer.prototype, "validateFile");
    const budgetSpy = vi.spyOn(EconomicsEngine.prototype, "beginModuleBudget");

    try {
      const config = loadConfig({
        offlineMode: true,
        maxBudgetUsd: 5.0,
        maxModuleBudgetUsd: 1.5,
        sqliteDbPath: sqlitePath,
        freezeStatePath: freezePath,
        interactiveDashboard: false
      });
      const report = await new ConvergenceEngine(config, false).executeBuild(TWO_MODULE_SPEC, dir);

      expect(report.error).toBeUndefined();
      expect(report.converged).toBe(true);
      expect(report.totalSpentUsd).toBe(0);
      expect(report.modules.map((m) => [m.moduleName, m.status, m.winningBranch, m.iterations])).toEqual([
        ["core_module", "converged", "Alpha", 1],
        ["pair_pipeline_module", "converged", "Alpha", 1]
      ]);
      expect(report.modules[1].dependsOn).toEqual(["core_module"]);

      // Each module only reports (and commits) what it changed relative to the previous module's workspace.
      expect([...report.modules[0].modifiedFiles].sort()).toEqual(["src/module.ts", "tests/module.test.ts"]);
      expect([...report.modules[1].modifiedFiles].sort()).toEqual(["src/pair_pipeline.ts", "tests/pair_pipeline.test.ts"]);
      expect([...report.modifiedFiles].sort()).toEqual(
        ["src/module.ts", "src/pair_pipeline.ts", "tests/module.test.ts", "tests/pair_pipeline.test.ts"]
      );

      // Module 2 really imports module 1.
      const pairSource = fs.readFileSync(path.join(dir, "src", "pair_pipeline.ts"), "utf8");
      expect(pairSource).toContain(`import { execute_core_pipeline } from "./module";`);
      expect(pairSource).toContain("export function pair_pipeline");

      // One commit per module, in order, on a single run branch.
      expect(report.committedHashes).toHaveLength(2);
      expect(report.committedHashes).toEqual(report.modules.map((m) => m.commitHash));
      expect(git(dir, ["rev-parse", "--abbrev-ref", "HEAD"])).toBe("foundry/alpha");
      expect(git(dir, ["log", "--format=%s", "-2"]).split("\n")).toEqual([
        "feat(pair_pipeline_module): autonomous convergence via Branch Alpha",
        "feat(core_module): autonomous convergence via Branch Alpha"
      ]);
      expect(git(dir, ["show", "--name-only", "--format=", report.committedHashes[1]]).split("\n").sort()).toEqual([
        "src/pair_pipeline.ts",
        "tests/pair_pipeline.test.ts"
      ]);

      // One episode per module in vector memory.
      const episodeIds = report.modules.map((m) => m.episodeId);
      expect(new Set(episodeIds).size).toBe(2);
      const db = new Database(sqlitePath, { readonly: true });
      try {
        const rows = db.prepare("SELECT id, module_name FROM episodic_memory ORDER BY id").all() as Array<{ id: number; module_name: string }>;
        expect(rows.map((r) => r.module_name)).toEqual(["core_module", "pair_pipeline_module"]);
        expect(rows.map((r) => r.id)).toEqual(episodeIds);
      } finally {
        db.close();
      }

      // Module 2 was designed against a call graph and file set that already contain module 1...
      const pairDesigns = designSpy.mock.calls.map(([p]) => p).filter((p) => p.moduleIndex === 1);
      expect(pairDesigns).toHaveLength(3);
      for (const design of pairDesigns) {
        expect(design.callGraphSummary).toContain("src/module.ts::execute_core_pipeline");
        expect(design.existingFiles.get("src/module.ts")).toContain("export function execute_core_pipeline");
      }
      // ...and type-checked with module 1 in the LSP overlay.
      const pairChecks = lspSpy.mock.calls.filter(([filePath]) => filePath === "src/pair_pipeline.ts");
      expect(pairChecks.length).toBeGreaterThan(0);
      expect(pairChecks[0][2]?.get("src/module.ts")).toContain("export function execute_core_pipeline");

      // Each module gets its own budget allowance.
      expect(budgetSpy.mock.calls).toEqual([
        ["core_module", 1.5],
        ["pair_pipeline_module", 1.5]
      ]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("keeps earlier modules when a later one fails, giving each module its own iteration allowance", async () => {
    process.env.FOUNDRY_FORCE_LOCAL_SANDBOX = "true";
    const { dir, sqlitePath, freezePath } = makeWorkspace();
    const realSynthesize = AstraSynthesizer.prototype.synthesizeSemanticPatches;
    vi.spyOn(AstraSynthesizer.prototype, "synthesizeSemanticPatches").mockImplementation(async function (
      this: AstraSynthesizer,
      params: Parameters<AstraSynthesizer["synthesizeSemanticPatches"]>[0]
    ) {
      if (params.contract.moduleName === "core_module") return realSynthesize.call(this, params);
      return [
        {
          filePath: params.contract.targetFilePath,
          action: "CREATE_FILE" as const,
          target: "",
          content: `export function pair_pipeline(input: number): number {\n  return "not a number";\n}\n`
        }
      ];
    });

    try {
      const config = loadConfig({
        offlineMode: true,
        maxConvergenceIterations: 2,
        sqliteDbPath: sqlitePath,
        freezeStatePath: freezePath,
        interactiveDashboard: false
      });
      const report = await new ConvergenceEngine(config, false).executeBuild(
        `${TWO_MODULE_SPEC}\nFinally build symbol quad_pipeline on top of pair_pipeline.`,
        dir
      );

      expect(report.converged).toBe(false);
      expect(report.modules.map((m) => [m.moduleName, m.status, m.iterations])).toEqual([
        ["core_module", "converged", 1],
        ["pair_pipeline_module", "failed", 2],
        ["quad_pipeline_module", "skipped", 0]
      ]);
      expect(report.error).toContain(`Module 2/3 "pair_pipeline_module"`);
      expect(report.error).toContain("TS2322");
      expect(report.committedHashes).toHaveLength(1);
      expect(fs.existsSync(path.join(dir, "src", "module.ts"))).toBe(true);
      expect(fs.existsSync(path.join(dir, "src", "pair_pipeline.ts"))).toBe(false);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("re-runs earlier modules' tests and keeps the synthesizer away from them", async () => {
    process.env.FOUNDRY_FORCE_LOCAL_SANDBOX = "true";
    const { dir, sqlitePath, freezePath } = makeWorkspace();
    const realSynthesize = AstraSynthesizer.prototype.synthesizeSemanticPatches;
    vi.spyOn(AstraSynthesizer.prototype, "synthesizeSemanticPatches").mockImplementation(async function (
      this: AstraSynthesizer,
      params: Parameters<AstraSynthesizer["synthesizeSemanticPatches"]>[0]
    ) {
      const patches = await realSynthesize.call(this, params);
      if (params.contract.moduleName === "core_module") return patches;
      // Module 2's own (relational) tests still pass when module 1 is broken; only module 1's tests notice.
      return [
        ...patches,
        {
          filePath: "src/module.ts",
          action: "REPLACE_FUNCTION" as const,
          target: "execute_core_pipeline",
          content: `export function execute_core_pipeline(input: number): number {\n  if (input < 0) throw new RangeError("negative");\n  return input * 2 + 2;\n}`
        },
        { filePath: "tests/module.test.ts", action: "CREATE_FILE" as const, target: "", content: "// tests removed\n" }
      ];
    });

    try {
      const config = loadConfig({
        offlineMode: true,
        maxConvergenceIterations: 1,
        sqliteDbPath: sqlitePath,
        freezeStatePath: freezePath,
        interactiveDashboard: false
      });
      const report = await new ConvergenceEngine(config, false).executeBuild(TWO_MODULE_SPEC, dir);

      expect(report.modules.map((m) => m.status)).toEqual(["converged", "failed"]);
      expect(report.error).toContain(`Regression: the tests of earlier module "core_module"`);
      expect(report.error).toContain("Rejected CREATE_FILE on tests/module.test.ts");
      expect(fs.readFileSync(path.join(dir, "src", "module.ts"), "utf8")).toContain("input * 2 + 1");
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("module plan validation", () => {
  const moduleJson = (name: string, dependsOn: string[] = []) => ({
    moduleName: name,
    targetFilePath: `src/${name}.ts`,
    testFilePath: `tests/${name}.test.ts`,
    language: "typescript",
    dependsOn,
    contracts: [{ symbolName: `${name}_fn` }]
  });

  it("accepts an ordered plan whose modules import earlier ones", () => {
    const plan = parseArchitectureContracts(JSON.stringify({ modules: [moduleJson("a"), moduleJson("b", ["a", "a"])] }), "typescript");
    expect(plan.map((m) => [m.moduleName, m.dependsOn])).toEqual([
      ["a", []],
      ["b", ["a"]]
    ]);
    const legacy = { ...moduleJson("c") } as Record<string, unknown>;
    delete legacy.dependsOn;
    expect(parseArchitectureContracts(JSON.stringify([legacy]), "typescript")[0].dependsOn).toEqual([]);
  });

  it("rejects forward, self and unknown dependencies, duplicate names and shared files", () => {
    const parse = (modules: unknown[]) => () => parseArchitectureContracts(JSON.stringify(modules), "typescript");
    expect(parse([moduleJson("a", ["b"]), moduleJson("b")])).toThrow(/not an earlier module/);
    expect(parse([moduleJson("a", ["a"])])).toThrow(/not an earlier module/);
    expect(parse([moduleJson("a"), { ...moduleJson("a"), targetFilePath: "src/x.ts", testFilePath: "tests/x.test.ts" }])).toThrow(
      /duplicates/
    );
    expect(parse([moduleJson("a"), { ...moduleJson("b"), testFilePath: "src/a.ts" }])).toThrow(/reuses src\/a\.ts/);
    expect(parse([])).toThrow(/empty module plan/);
  });

  it("pins a branch design to the planned identity and keeps planned symbols", () => {
    const planned = parseArchitectureContracts(JSON.stringify([moduleJson("a"), moduleJson("b", ["a"])]), "typescript")[1];
    const design = parseBranchModuleDesign(
      JSON.stringify({
        branchStrategy: "fast path",
        moduleName: "renamed",
        targetFilePath: "src/elsewhere.ts",
        dependsOn: [],
        contracts: [{ symbolName: "b_fn", signature: "b_fn(): number" }, { symbolName: "helper" }]
      }),
      planned
    );
    expect(design).toMatchObject({
      branchStrategy: "fast path",
      moduleName: "b",
      targetFilePath: "src/b.ts",
      testFilePath: "tests/b.test.ts",
      dependsOn: ["a"]
    });
    expect(design.contracts.map((c) => c.symbolName)).toEqual(["b_fn", "helper"]);
    expect(() => parseBranchModuleDesign(JSON.stringify({ contracts: [{ symbolName: "other" }] }), planned)).toThrow(
      /dropped planned symbol/
    );
    expect(() => parseBranchModuleDesign(JSON.stringify([{ contracts: [{ symbolName: "b_fn" }] }, {}]), planned)).toThrow(
      /exactly one/
    );
  });
});

describe("per-module budget", () => {
  it("trips the circuit breaker on a module's allowance while the run budget keeps accumulating", () => {
    const freezeFile = path.join(os.tmpdir(), `foundry-module-budget-${Date.now()}.json`);
    const economics = new EconomicsEngine(5, freezeFile, false);
    try {
      economics.beginModuleBudget("first", 0.2);
      economics.recordUsage("claude", "claude-opus-5-5", 10000, 1000); // $0.06
      expect(economics.getModuleSpentUsd()).toBeCloseTo(0.06, 6);
      expect(() => economics.recordUsage("claude", "claude-opus-5-5", 10000, 10000)).toThrow(/Module budget exceeded on "first"/);
      const dump = JSON.parse(fs.readFileSync(freezeFile, "utf8")) as { moduleBudget: { moduleName: string } };
      expect(dump.moduleBudget.moduleName).toBe("first");

      economics.beginModuleBudget("second", 0.2);
      expect(economics.getModuleSpentUsd()).toBe(0);
      expect(economics.getTotalSpentUsd()).toBeCloseTo(0.3, 6);
      expect(() => economics.enforceBudgetPreFlight("claude", "claude-opus-5-5", "x", 5000)).not.toThrow();
      expect(() => economics.enforceBudgetPreFlight("claude", "claude-opus-5-5", "x", 20000)).toThrow(BudgetExceededError);
    } finally {
      fs.rmSync(freezeFile, { force: true });
    }

    const runFreezeFile = path.join(os.tmpdir(), `foundry-run-budget-${Date.now()}.json`);
    const small = new EconomicsEngine(0.1, runFreezeFile, false);
    try {
      small.beginModuleBudget("generous", 10);
      expect(() => small.recordUsage("claude", "claude-opus-5-5", 10000, 10000)).toThrow(/Budget exceeded after/);
    } finally {
      fs.rmSync(runFreezeFile, { force: true });
    }
  });
});
