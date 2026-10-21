import { describe, expect, it } from "vitest";
import {
  ThrashDetector,
  describeEvidence,
  distillCounterexample,
  failureSignature,
  foldMessages,
  holdoutTestPath,
  patchSignature,
  signatureStub
} from "../src/engine/methods";
import { TreeSearchEngine } from "../src/engine/tree_search";
import { EconomicsEngine, cacheReadDiscount } from "../src/engine/economics";
import { formatTranscript } from "../src/swarm/chat";
import { describeModuleDependencies, ModuleArchitectureContract } from "../src/swarm/claude_architect";
import * as os from "os";
import * as path from "path";
import * as fs from "fs";

const SANDBOX_LOG = [
  "Exit Code: 1",
  "",
  "Compiler/Assertion Errors:",
  "AssertionError [ERR_ASSERTION]: Zero boundary must return 1",
  "AssertionError [ERR_ASSERTION]: Zero boundary must return 1",
  "",
  "Stack Trace:",
  "  runBoundarySuite (C:\\Users\\x\\AppData\\Local\\Temp\\foundry-sandbox-ab12\\tests\\module.test.js:7:12)",
  "  Module._compile (node:internal/modules/cjs/loader:1256:14)",
  "  Object..js (node:internal/modules/cjs/loader:1310:10)",
  "",
  "Raw Output Tail:",
  "\x1b[31m- 1\x1b[0m",
  "\x1b[32m+ 0\x1b[0m",
  "    at runBoundarySuite (C:\\Users\\x\\AppData\\Local\\Temp\\foundry-sandbox-ab12\\tests\\module.test.js:7:12)",
  "    at Module._compile (node:internal/modules/cjs/loader:1256:14)",
  "    at Object..js (node:internal/modules/cjs/loader:1310:10)",
  "    at Module.load (node:internal/modules/cjs/loader:1119:32)",
  "Node.js v22.1.0"
].join("\n");

describe("counterexample distillation (GEF shrinking, TCA log folding)", () => {
  it("keeps the assertion, the diff and the workspace frame, and drops repeats, colors and runtime frames", () => {
    const out = distillCounterexample(SANDBOX_LOG);
    expect(out).toContain("Zero boundary must return 1");
    expect(out).toContain("- 1");
    expect(out).toContain("+ 0");
    expect(out).toContain("module.test.js:7:12");
    expect(out).not.toContain("\x1b[");
    expect(out).not.toContain("node:internal");
    expect(out.split("Zero boundary must return 1").length).toBe(2);
    expect(out.length).toBeLessThan(SANDBOX_LOG.length);
  });

  it("falls back to the tail when nothing looks like an error, and caps the size", () => {
    const plain = Array.from({ length: 40 }, (_, i) => `line ${i}`).join("\n");
    const out = distillCounterexample(plain);
    expect(out).toContain("line 39");
    expect(out).not.toContain("line 0\n");

    const huge = Array.from({ length: 500 }, (_, i) => `AssertionError: case ${i} expected ${i} got ${i + 1}`).join("\n");
    const capped = distillCounterexample(huge, 2000);
    expect(capped.length).toBeLessThanOrEqual(2000);
    expect(capped).toContain("[folded]");
    expect(capped).toContain("case 0 ");
    expect(capped).toContain("case 499");
  });
});

describe("failure and patch signatures", () => {
  it("matches the same failure across line numbers, temp folders and timings", () => {
    const a = "AssertionError: expected 3 to equal 4\n    at suite (C:\\tmp\\foundry-sandbox-aaa\\tests\\m.test.js:7:12)\nran in 12ms";
    const b = "AssertionError: expected 5 to equal 6\n    at suite (C:\\tmp\\foundry-sandbox-zzz\\tests\\m.test.js:9:3)\nran in 48ms";
    const c = "TypeError: fn is not a function\n    at suite (C:\\tmp\\foundry-sandbox-aaa\\tests\\m.test.js:7:12)";
    expect(failureSignature(a)).toBe(failureSignature(b));
    expect(failureSignature(a)).not.toBe(failureSignature(c));
    expect(failureSignature(a)).toMatch(/^[0-9a-f]{8}$/);
  });

  it("gives identical patch sets one signature regardless of order and whitespace", () => {
    const p1 = { filePath: "src/a.ts", action: "REPLACE_FUNCTION", target: "f", content: "export function f() {\n  return 1;\n}" };
    const p2 = { filePath: "src/a.ts", action: "UPSERT_IMPORT", target: "", content: 'import x from "y";' };
    const p1ws = { ...p1, content: "export function f() {   return 1; }" };
    expect(patchSignature([p1, p2])).toBe(patchSignature([p2, p1ws]));
    expect(patchSignature([p1])).not.toBe(patchSignature([p2]));
  });
});

describe("thrash detector (TCA)", () => {
  it("warns one short of the limit and stops at it when the same failure keeps coming back", () => {
    const detector = new ThrashDetector(3);
    expect(detector.record("Alpha", "f1", "p1")).toMatchObject({ repeats: 1, escalate: false, stalled: false });
    const second = detector.record("Alpha", "f1", "p2");
    expect(second).toMatchObject({ repeats: 2, escalate: true, stalled: false });
    expect(second.reason).toContain("same failure");
    expect(detector.record("Alpha", "f1", "p3")).toMatchObject({ repeats: 3, stalled: true });
  });

  it("counts an identical patch resent as thrashing, keeps branches apart, and can be switched off", () => {
    const detector = new ThrashDetector(2);
    detector.record("Beta", "f1", "same");
    const again = detector.record("Beta", "f2", "same");
    expect(again.stalled).toBe(true);
    expect(again.reason).toContain("same patch");
    // Gamma's history is its own.
    expect(detector.record("Gamma", "f1", "same")).toMatchObject({ repeats: 1, stalled: false });
    // A different result resets the run.
    detector.record("Alpha", "f1", "p1");
    detector.record("Alpha", "f9", "p2");
    expect(detector.record("Alpha", "f1", "p3")).toMatchObject({ repeats: 1, stalled: false });

    const off = new ThrashDetector(0);
    for (let i = 0; i < 10; i += 1) expect(off.record("Alpha", "f", "p").stalled).toBe(false);
  });
});

describe("holdout gate in the tree search (GEF)", () => {
  const contract: ModuleArchitectureContract = {
    branchStrategy: "s",
    moduleName: "m",
    targetFilePath: "src/m.ts",
    testFilePath: "tests/m.test.ts",
    language: "typescript",
    dependsOn: [],
    stateSchemas: [],
    contracts: [{ symbolName: "f", signature: "f(x: number): number", preconditions: [], postconditions: [], complexityBound: "O(1)" }],
    boundaryInvariants: []
  };

  it("does not converge on passing visible tests alone, and ranks a hidden failure between pass and fail", () => {
    const search = new TreeSearchEngine(0.85);
    search.initializeBranches(new Map([["Alpha", contract], ["Beta", contract]]), new Map());
    const base = { lspDiagnosticLatencyMs: 0, lspErrorCount: 0, iteration: 1 };
    const hiddenFail = search.recordBranchStep("Alpha", { ...base, sandboxPassed: true, mutationKillRatio: 1, holdoutPassed: false }, new Map(), [], "hidden");
    expect(hiddenFail.status).toBe("ACTIVE");
    const allPass = search.recordBranchStep("Beta", { ...base, sandboxPassed: true, mutationKillRatio: 1, holdoutPassed: true }, new Map(), []);
    expect(allPass.status).toBe("CONVERGED");
    expect(allPass.fitnessScore).toBeGreaterThan(hiddenFail.fitnessScore);
    expect(hiddenFail.fitnessScore).toBeGreaterThan(search.computeFitness({ ...base, sandboxPassed: false, mutationKillRatio: 0 }));
    // Without a holdout suite nothing changes.
    expect(search.computeFitness({ ...base, sandboxPassed: true, mutationKillRatio: 1 })).toBe(allPass.fitnessScore);
  });

  it("names the hidden suite next to the visible one, only where the harness can run a second file", () => {
    expect(holdoutTestPath("tests/m.test.ts", "typescript", "src/m.ts")).toBe("tests/m.holdout.test.ts");
    expect(holdoutTestPath("tests/m.spec.ts", "typescript")).toBe("tests/m.holdout.spec.ts");
    expect(holdoutTestPath("tests/m.ts", "typescript")).toBe("tests/m.holdout.ts");
    expect(holdoutTestPath("tests/test_m.py", "python")).toBe("tests/test_m_holdout.py");
    expect(holdoutTestPath("tests/m_test.py", "python")).toBe("tests/m_holdout_test.py");
    expect(holdoutTestPath("pkg/m_test.go", "go")).toBe("pkg/m_holdout_test.go");
    expect(holdoutTestPath("tests/integration_test.rs", "rust")).toBe("tests/integration_test_holdout.rs");
    expect(holdoutTestPath("src/lib.rs", "rust", "src/lib.rs")).toBeNull();
    expect(holdoutTestPath("tests/m_test.cpp", "cpp")).toBeNull();
  });
});

describe("signature stubs and evidence (REA recon, TCA lazy handles)", () => {
  const tsSource = [
    `import { x } from "./x";`,
    `const PRIVATE = 1;`,
    `export const LIMIT = 10;`,
    `export function parse(input: string): number {`,
    `  return input.length;`,
    `}`,
    `function helper() {}`,
    `export class Store {`,
    `  private items: number[] = [];`,
    `  public add(item: number): void {`,
    `    this.items.push(item);`,
    `  }`,
    `  size(): number {`,
    `    return this.items.length;`,
    `  }`,
    `}`,
    `export type Id = string;`
  ].join("\n");

  it("lists exported declarations and public members, not bodies or private helpers", () => {
    const stub = signatureStub(tsSource, "typescript");
    expect(stub).toContain("export const LIMIT = 10");
    expect(stub).toContain("export function parse(input: string): number");
    expect(stub).toContain("public add(item: number): void");
    expect(stub).toContain("size(): number");
    expect(stub).toContain("export type Id = string");
    expect(stub).not.toContain("PRIVATE");
    expect(stub).not.toContain("helper");
    expect(stub).not.toContain("private items");
    expect(stub).not.toContain("this.items.push");

    const py = signatureStub("import os\n\nLIMIT = 3\n\ndef _hidden():\n    pass\n\nclass Store:\n    def add(self, item):\n        pass\n\ndef parse(text: str) -> int:\n    return 1\n", "python");
    expect(py).toContain("LIMIT = 3");
    expect(py).toContain("class Store");
    expect(py).toContain("def add(self, item)");
    expect(py).toContain("def parse(text: str) -> int");
    expect(py).not.toContain("_hidden");
    const goSource = "package x\n\nfunc Exported(a int) int { return a }\nfunc hidden() {}\ntype Shape struct{}\n";
    expect(signatureStub(goSource, "go")).toBe(
      `// signatures only (${goSource.length} chars of source not shown)\nfunc Exported(a int) int\ntype Shape struct`
    );
  });

  it("hands a large dependency to the models as a stub instead of a cut-off listing", () => {
    const contract: ModuleArchitectureContract = {
      branchStrategy: "s",
      moduleName: "big",
      targetFilePath: "src/big.ts",
      testFilePath: "tests/big.test.ts",
      language: "typescript",
      dependsOn: [],
      stateSchemas: [],
      contracts: [{ symbolName: "parse", signature: "parse(input: string): number", preconditions: [], postconditions: [], complexityBound: "" }],
      boundaryInvariants: []
    };
    const large = `${tsSource}\n${"// filler\n".repeat(2000)}`;
    const text = describeModuleDependencies([{ contract, source: large }]);
    expect(text).toContain("signatures only");
    expect(text).toContain("export function parse(input: string): number");
    expect(text).not.toContain("(truncated)");
    expect(text.length).toBeLessThan(3000);
    expect(describeModuleDependencies([{ contract, source: tsSource }])).toContain("this.items.push");
  });

  it("surveys manifests, tests and exports of an existing workspace, and nothing for an empty one", () => {
    const files = new Map<string, string>([
      ["package.json", JSON.stringify({ name: "demo", scripts: { test: "vitest" }, dependencies: { zod: "^3" } })],
      ["src/store.ts", tsSource],
      ["tests/store.test.ts", "import {} from '../src/store';\n"],
      ["src/util.py", "def helper(x):\n    return x\n"]
    ]);
    const evidence = describeEvidence(files);
    expect(evidence).toContain("name demo");
    expect(evidence).toContain("scripts: test");
    expect(evidence).toContain("dependencies: zod");
    expect(evidence).toContain("Existing tests (1 file; they must keep passing)");
    expect(evidence).toContain("tests/store.test.ts");
    expect(evidence).toContain("src/store.ts (17 lines)");
    expect(evidence).toContain("export function parse(input: string): number");
    expect(evidence).toContain("def helper(x)");
    expect(describeEvidence(new Map())).toBe("");
  });
});

describe("prompt cache accounting (TCA)", () => {
  it("prices cached input tokens at the provider's discount and keeps them in the totals", () => {
    const freeze = path.join(os.tmpdir(), `foundry-cache-${process.pid}-${Date.now()}.json`);
    const economics = new EconomicsEngine(100, freeze, false);
    // claude-opus-5: $5/M input. 1000 fresh + 9000 read at 10% + 2000 written at 125% = 1000 + 900 + 2500 = 4400 token-equivalents.
    const entry = economics.recordUsage("claude", "claude-opus-5", 1000, 0, { readTokens: 9000, writeTokens: 2000 });
    expect(entry.cacheReadTokens).toBe(9000);
    expect(entry.cacheWriteTokens).toBe(2000);
    expect(entry.costUsd).toBeCloseTo((4400 / 1_000_000) * 5, 8);
    expect(economics.getTokenTotals()).toEqual({ inputTokens: 12000, outputTokens: 0, cachedTokens: 9000 });
    // Without a cache nothing changes.
    const plain = economics.recordUsage("claude", "claude-opus-5", 1000, 100);
    expect(plain.cacheReadTokens).toBe(0);
    expect(plain.costUsd).toBeCloseTo((1000 / 1_000_000) * 5 + (100 / 1_000_000) * 25, 8);
    expect(cacheReadDiscount("astra", "gpt-5")).toBe(0.1);
    expect(cacheReadDiscount("astra", "gpt-4o")).toBe(0.5);
    expect(cacheReadDiscount("deepseek", "x")).toBe(1);
    fs.rmSync(freeze, { force: true });
  });
});

describe("transcript folding (TCA compaction)", () => {
  it("keeps one line per message that no longer fits, newest in full", () => {
    const long = Array.from({ length: 40 }, (_, i) => ({ role: (i % 2 ? "assistant" : "user") as "user" | "assistant", text: `message ${i} ${"x".repeat(120)}` }));
    const transcript = formatTranscript(long, 1200);
    expect(transcript.startsWith("[Earlier messages left out] Folded, one line each:")).toBe(true);
    expect(transcript).toContain("message 39 " + "x".repeat(120));
    // Folded messages are one line each, with a "- " in front; the long assistant lines are cut with an ellipsis.
    expect(transcript).toContain("- User: message 20 ");
    expect(transcript).toMatch(/- Claude \(architect\): message 21 x+…/);
    expect(transcript).not.toContain("\n\nUser: message 20 ");
    expect(transcript).toContain("earlier messages not shown");
    expect(foldMessages(["User: hello"], 24, 140)).toBe("- User: hello");
  });
});
