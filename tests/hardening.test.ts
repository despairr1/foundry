import { describe, it, expect } from "vitest";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { pathToFileURL } from "url";
import { ASTParser } from "../src/ast/parser";
import { SemanticPatcher } from "../src/ast/semantic_patcher";
import { BudgetExceededError, EconomicsEngine, resolvePricing } from "../src/engine/economics";
import { MutationEngine } from "../src/engine/mutation";
import { SandboxRunner } from "../src/sandbox/runner";
import { normalizeWorkspacePath } from "../src/utils/paths";
import { NonRetryableError, withExponentialBackoff } from "../src/utils/logger";
import { extractJsonArray } from "../src/swarm/providers";
import { parseSemanticPatches } from "../src/swarm/astra_synthesizer";
import { parseArchitectureContracts } from "../src/swarm/claude_architect";
import { stripMarkdownCodeFences } from "../src/swarm/gemini_auditor";
import { uriKey } from "../src/lsp/client";
import { LSPMultiplexer } from "../src/lsp/multiplexer";
import { loadConfig } from "../src/config";
import { GitManager } from "../src/utils/git";

const parser = new ASTParser();
const patcher = new SemanticPatcher(parser);

describe("AST parsing and semantic patching", () => {
  it("parses sources larger than tree-sitter's default 32 KiB buffer", () => {
    let src = "";
    for (let i = 0; i < 1500; i += 1) src += `export function f${i}(a: number): number { return a + ${i}; }\n`;
    expect(src.length).toBeGreaterThan(32 * 1024);
    const doc = parser.parse(src, "typescript");
    expect(doc.hasSyntaxError).toBe(false);
    expect(parser.findTargetNode(src, "typescript", "f1499")).not.toBeNull();
  });

  it("keeps the export keyword when the replacement omits it", () => {
    const src = `export function add(a: number, b: number): number {\n  return a - b;\n}\n`;
    const res = patcher.applyPatch(src, "typescript", {
      filePath: "src/math.ts",
      action: "REPLACE_FUNCTION",
      target: "add",
      content: `function add(a: number, b: number): number {\n  return a + b;\n}`
    });
    expect(res.success).toBe(true);
    expect(res.updatedSource).toContain("export function add");
    expect(res.updatedSource).toContain("a + b");
    expect(res.updatedSource).not.toContain("export export");
  });

  it("does not duplicate the export keyword when the replacement includes it", () => {
    const src = `export const LIMIT = 1;\n`;
    const res = patcher.applyPatch(src, "typescript", {
      filePath: "src/limits.ts",
      action: "REPLACE_NODE",
      target: "LIMIT",
      content: `export const LIMIT = 2;`
    });
    expect(res.updatedSource).toBe(`export const LIMIT = 2;\n`);
  });

  it("preserves Python decorators when the replacement omits them", () => {
    const src = `import functools\n\n@functools.lru_cache\ndef fib(n):\n    return n\n`;
    const res = patcher.applyPatch(src, "python", {
      filePath: "src/fib.py",
      action: "REPLACE_FUNCTION",
      target: "fib",
      content: `def fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)`
    });
    expect(res.success).toBe(true);
    expect(res.updatedSource).toContain("@functools.lru_cache\ndef fib(n):\n    return n if n < 2");
  });

  it("targets module-level declarations instead of same-named locals", () => {
    const src = `function helper() {\n  const result = 1;\n  return result;\n}\nexport const result = 42;\n`;
    const node = parser.findTargetNode(src, "typescript", "result");
    expect(node?.text).toBe("export const result = 42;");
  });

  it("inserts Go imports after the package clause", () => {
    const src = `package main\n\nfunc main() {}\n`;
    const res = patcher.applyPatch(src, "go", { filePath: "main.go", action: "UPSERT_IMPORT", target: "", content: `import "fmt"` });
    expect(res.success).toBe(true);
    expect(res.updatedSource.startsWith(`package main\nimport "fmt"\n`)).toBe(true);
  });

  it("allows CREATE_FILE for manifests but rejects structural patches on them", () => {
    const { updatedFiles, results } = patcher.applyBatch(new Map(), [
      { filePath: "Cargo.toml", action: "CREATE_FILE", target: "", content: `[package]\nname = "demo"\n` },
      { filePath: "go.mod", action: "REPLACE_NODE", target: "module", content: "module demo" }
    ]);
    expect(updatedFiles.get("Cargo.toml")).toContain(`name = "demo"`);
    expect(results[0].success).toBe(true);
    expect(results[1].success).toBe(false);
    expect(updatedFiles.has("go.mod")).toBe(false);
  });
});

describe("model output validation", () => {
  it("rejects workspace paths that escape the target or need shell quoting", () => {
    expect(normalizeWorkspacePath("./src\\module.ts")).toBe("src/module.ts");
    for (const bad of ["../etc/passwd", "/abs/file.ts", "C:\\Windows\\x.ts", "src/../../x.ts", "a.py; rm -rf ~", ".git/config", "src/$(whoami).ts"]) {
      expect(() => normalizeWorkspacePath(bad)).toThrow();
    }
  });

  it("extracts JSON arrays from prose, fences and wrapper objects", () => {
    expect(extractJsonArray('Here you go:\n```json\n[{"a":1}]\n```\nThanks!', "patches")).toEqual([{ a: 1 }]);
    expect(extractJsonArray('{"patches": [{"a": 2}]}', "patches")).toEqual([{ a: 2 }]);
    expect(extractJsonArray('Sure. [note] The result is {"patches":[{"a":"]"}]} done', "patches")).toEqual([{ a: "]" }]);
  });

  it("validates semantic patches and their paths", () => {
    const ok = parseSemanticPatches(JSON.stringify({ patches: [{ filePath: "src\\a.ts", action: "CREATE_FILE", content: "x" }] }));
    expect(ok[0]).toEqual({ filePath: "src/a.ts", action: "CREATE_FILE", target: "", content: "x" });
    expect(() => parseSemanticPatches(JSON.stringify([{ filePath: "../a.ts", action: "CREATE_FILE", content: "" }]))).toThrow();
    expect(() => parseSemanticPatches(JSON.stringify([{ filePath: "a.ts", action: "REWRITE_EVERYTHING" }]))).toThrow();
  });

  it("validates architecture contracts against the project language", () => {
    const contract = {
      moduleName: "pool",
      targetFilePath: "src/pool.ts",
      testFilePath: "tests/pool.test.ts",
      language: "typescript",
      contracts: [{ symbolName: "initialize_pool" }]
    };
    const parsed = parseArchitectureContracts(JSON.stringify([contract]), "typescript");
    expect(parsed[0].stateSchemas).toEqual([]);
    expect(parsed[0].contracts[0].preconditions).toEqual([]);
    expect(() => parseArchitectureContracts(JSON.stringify([contract]), "rust")).toThrow(/does not match/);
    expect(() => parseArchitectureContracts(JSON.stringify([{ ...contract, contracts: [] }]), "typescript")).toThrow();
  });

  it("picks the largest fenced block from auditor output", () => {
    expect(stripMarkdownCodeFences("Notes:\n```bash\nnpm test\n```\n```ts\nconst a = 1;\nconst b = 2;\n```")).toBe(
      "const a = 1;\nconst b = 2;"
    );
  });
});

describe("economics and retries", () => {
  it("prices current Claude models at list rates", () => {
    expect(resolvePricing("claude", "claude-fable-5-1")).toEqual({ inputCostPerMillionUsd: 10, outputCostPerMillionUsd: 50 });
    expect(resolvePricing("claude", "claude-opus-5-5").inputCostPerMillionUsd).toBe(4);
    expect(resolvePricing("claude", "claude-opus-5").inputCostPerMillionUsd).toBe(5);
    expect(resolvePricing("claude", "claude-sonnet-5-5").inputCostPerMillionUsd).toBe(2);
    expect(resolvePricing("claude", "some-future-model").outputCostPerMillionUsd).toBe(50);
  });

  it("refuses a request whose projected cost exceeds the remaining budget", () => {
    const freezeFile = path.join(os.tmpdir(), `foundry-preflight-${Date.now()}.json`);
    const economics = new EconomicsEngine(0.05, freezeFile, false);
    expect(() => economics.enforceBudgetPreFlight("claude", "claude-fable-5-1", "x".repeat(1000), 4096)).toThrow(BudgetExceededError);
    fs.rmSync(freezeFile, { force: true });
  });

  it("does not retry non-retryable or budget errors", async () => {
    let calls = 0;
    await expect(
      withExponentialBackoff("t", async () => {
        calls += 1;
        throw new NonRetryableError("HTTP 401");
      }, { baseDelayMs: 1 })
    ).rejects.toThrow("HTTP 401");
    expect(calls).toBe(1);

    calls = 0;
    await expect(
      withExponentialBackoff("t", async () => {
        calls += 1;
        throw new BudgetExceededError(2, 1);
      }, { baseDelayMs: 1 })
    ).rejects.toBeInstanceOf(BudgetExceededError);
    expect(calls).toBe(1);

    calls = 0;
    const value = await withExponentialBackoff("t", async () => {
      calls += 1;
      if (calls < 3) throw new Error("transient");
      return "ok";
    }, { baseDelayMs: 1 });
    expect(value).toBe("ok");
    expect(calls).toBe(3);
  });
});

describe("mutation engine", () => {
  const engine = new MutationEngine(parser, new SandboxRunner("unused"));

  it("mixes mutant kinds and only emits mutants that still parse", () => {
    const src = `export function f(x: number): number {\n  if (x < 0) {\n    throw new Error("neg");\n  }\n  return x * 2 + 1;\n}\n`;
    const mutants = engine.generateMutants(src, "typescript", 5);
    expect(new Set(mutants.map((m) => m.kind))).toEqual(new Set(["OPERATOR_INVERSION", "GUARD_DELETION", "CONSTANT_PERTURBATION"]));
    for (const m of mutants) expect(parser.parse(m.mutatedSource, "typescript").hasSyntaxError).toBe(false);
  });

  it("mutates Python operators and replaces deleted guards with pass", () => {
    const src = `def f(x):\n    if x < 0 and x != -5:\n        raise ValueError("neg")\n`;
    const mutants = engine.generateMutants(src, "python", 10);
    expect(mutants.some((m) => m.mutatedSource.includes("x < 0 or x != -5"))).toBe(true);
    const guard = mutants.find((m) => m.kind === "GUARD_DELETION");
    expect(guard?.mutatedSource).toBe(`def f(x):\n    pass\n`);
  });
});

describe("LSP plumbing", () => {
  it("normalizes client and server file URIs to the same key", () => {
    const abs = path.resolve(os.tmpdir(), "foundry uri", "a.ts");
    const href = pathToFileURL(abs).href;
    const serverStyle = process.platform === "win32" ? href.replace(/^file:\/\/\/([A-Za-z]):/, (_m, d: string) => `file:///${d.toLowerCase()}%3A`) : href;
    expect(uriKey(serverStyle)).toBe(uriKey(href));
  });

  it("reports type mismatches in memory but only warns about missing dependencies", async () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-lsp-"));
    const mux = new LSPMultiplexer(root, parser, 200);
    try {
      const frames = await mux.validateFile(
        "src/a.ts",
        `import { helper } from "./b";\nimport leftPad from "left-pad";\nexport const n: number = helper();\nexport const p = leftPad;\n`,
        new Map([["src/b.ts", `export function helper(): string { return "x"; }\n`]])
      );
      const errors = frames.filter((f) => f.severity === "ERROR");
      expect(errors.map((f) => f.code)).toEqual(["TS2322"]);
      expect(errors[0].filePath).toBe("src/a.ts");
      expect(frames.some((f) => f.code === "TS2307" && f.severity === "WARNING")).toBe(true);
    } finally {
      await mux.shutdownAll();
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});

describe("config and git", () => {
  it("normalizes provider base URLs", () => {
    const config = loadConfig({
      anthropicBaseUrl: "https://api.anthropic.com",
      astraBaseUrl: "https://api.groq.com/openai/v1/",
      geminiBaseUrl: "https://generativelanguage.googleapis.com"
    });
    expect(config.anthropicBaseUrl).toBe("https://api.anthropic.com");
    expect(config.astraBaseUrl).toBe("https://api.groq.com/openai/v1/chat/completions");
    expect(config.geminiBaseUrl).toBe("https://generativelanguage.googleapis.com/v1beta");
    // The Anthropic SDK appends /v1/messages itself; older .env files hold the full endpoint.
    expect(loadConfig({ anthropicBaseUrl: "https://api.anthropic.com/v1/messages" }).anthropicBaseUrl).toBe("https://api.anthropic.com");
    expect(loadConfig({ anthropicBaseUrl: "https://proxy.local/v1/messages/" }).anthropicBaseUrl).toBe("https://proxy.local");
    expect(loadConfig({ anthropicBaseUrl: "https://proxy.local/anthropic/v1" }).anthropicBaseUrl).toBe("https://proxy.local/anthropic");
  });

  it("commits only the engine's files, leaving the user's staged work alone", async () => {
    const repo = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-git-"));
    try {
      const git = new GitManager(repo);
      await git.createOrCheckoutBranch("main");
      fs.writeFileSync(path.join(repo, "user.txt"), "work in progress", "utf8");
      execFileSync("git", ["add", "user.txt"], { cwd: repo });
      fs.mkdirSync(path.join(repo, "src"));
      fs.writeFileSync(path.join(repo, "src", "module.ts"), "export const x = 1;\n", "utf8");
      await git.stageAndCommit(["src/module.ts"], "feat: engine output");
      const committed = execFileSync("git", ["show", "--name-only", "--format=", "HEAD"], { cwd: repo, encoding: "utf8" });
      expect(committed.trim()).toBe("src/module.ts");
      const staged = execFileSync("git", ["diff", "--cached", "--name-only"], { cwd: repo, encoding: "utf8" });
      expect(staged.trim()).toBe("user.txt");
    } finally {
      fs.rmSync(repo, { recursive: true, force: true });
    }
  });
});
