import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { diffTree, failureHighlights, recentLogProblems, scopeViolation, snapshotTree } from "../src/singularity/self_improve";

describe("self-improvement guards", () => {
  it("only lets the harness change src/ and add (not edit) tests", () => {
    expect(scopeViolation({ created: ["src/a.ts", "tests/self-checks/a.check.ts"], modified: ["src/b.ts"], deleted: [] })).toBeUndefined();
    expect(scopeViolation({ created: [], modified: ["tests/self-checks/a.check.ts"], deleted: [] })).toBeUndefined();
    expect(scopeViolation({ created: ["gui/main.py"], modified: [], deleted: [] })).toMatch(/outside src\/ and tests\//);
    expect(scopeViolation({ created: [], modified: ["package.json"], deleted: [] })).toMatch(/outside/);
    expect(scopeViolation({ created: [], modified: ["tests/hardening.test.ts"], deleted: [] })).toMatch(/existing tests/);
    expect(scopeViolation({ created: [], modified: [], deleted: ["tests/engine.test.ts"] })).toMatch(/existing tests/);
  });

  it("detects created, modified and deleted files while ignoring logs, databases and dependencies", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-snap-"));
    try {
      fs.mkdirSync(path.join(root, "src"));
      fs.mkdirSync(path.join(root, "node_modules", "x"), { recursive: true });
      fs.writeFileSync(path.join(root, "src", "keep.ts"), "a");
      fs.writeFileSync(path.join(root, "src", "edit.ts"), "a");
      fs.writeFileSync(path.join(root, "src", "gone.ts"), "a");
      const before = snapshotTree(root);

      fs.writeFileSync(path.join(root, "src", "edit.ts"), "b");
      fs.rmSync(path.join(root, "src", "gone.ts"));
      fs.writeFileSync(path.join(root, "src", "new.ts"), "c");
      fs.writeFileSync(path.join(root, ".foundry.log"), "noise");
      fs.writeFileSync(path.join(root, "memory.sqlite"), "db");
      fs.writeFileSync(path.join(root, "node_modules", "x", "index.js"), "dep");
      fs.mkdirSync(path.join(root, ".foundry"));
      fs.writeFileSync(path.join(root, ".foundry", "journal.json"), "{}");

      expect(diffTree(root, before)).toEqual({ created: ["src/new.ts"], modified: ["src/edit.ts"], deleted: ["src/gone.ts"] });
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("pulls the failing lines out of noisy test and compiler output", () => {
    const vitest = [
      "[00:33:59.434] [ERROR] [Economics] Budget exceeded after claude completion: $0.25 > $0.20.",
      " FAIL  tests/module.test.ts [ tests/module.test.ts ]",
      "Error: No test suite found in file tests/module.test.ts",
      " × converges end-to-end 1530ms"
    ].join("\n");
    expect(failureHighlights(vitest)).toBe(
      "FAIL  tests/module.test.ts [ tests/module.test.ts ]; Error: No test suite found in file tests/module.test.ts; × converges end-to-end 1530ms"
    );
    expect(failureHighlights("tsc failed:\nsrc/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.")).toContain("error TS2322");
  });

  it("reads recent distinct warnings and errors from the engine log as evidence", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-log-"));
    const logPath = path.join(dir, "engine.log");
    const previous = process.env.FOUNDRY_LOG_PATH;
    try {
      fs.writeFileSync(
        logPath,
        [
          "[06:07:12.464] [WARN] [Backoff] ClaudeArchitect[Beta] failed (attempt 1/4): bad JSON",
          "[06:07:13.000] [INFO] [Economics] CLAUDE (claude-fable-5-1): +522in/3631out",
          "[06:07:14.572] [WARN] [Backoff] ClaudeArchitect[Beta] failed (attempt 1/4): bad JSON",
          "[06:08:00.000] [ERROR] [Convergence] No branch produced a valid architecture contract.",
          "[06:09:00.000] [WARN] [SelfImprove] Round 1: Not kept"
        ].join("\n")
      );
      process.env.FOUNDRY_LOG_PATH = logPath;
      expect(recentLogProblems(dir)).toEqual([
        "[WARN] [Backoff] ClaudeArchitect[Beta] failed (attempt 1/4): bad JSON",
        "[ERROR] [Convergence] No branch produced a valid architecture contract."
      ]);
    } finally {
      if (previous === undefined) delete process.env.FOUNDRY_LOG_PATH;
      else process.env.FOUNDRY_LOG_PATH = previous;
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
