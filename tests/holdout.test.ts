import { describe, expect, it } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { loadConfig } from "../src/config";
import { ConvergenceEngine } from "../src/engine/convergence";

/** Offline Builds: the hidden suite is written, run, and kept out of the workspace the code writer sees. */
describe("hidden acceptance suite (GEF holdout) in an offline Build", () => {
  const spec = "Build symbol execute_core_pipeline that validates non-negative input and computes 2*x + 1.";

  async function build(holdoutTests: boolean): Promise<{ dir: string; log: string; converged: boolean }> {
    process.env.FOUNDRY_FORCE_LOCAL_SANDBOX = "true";
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-holdout-"));
    const logPath = path.join(dir, "engine.log");
    const previousLog = process.env.FOUNDRY_LOG_PATH;
    process.env.FOUNDRY_LOG_PATH = logPath;
    try {
      const config = loadConfig({
        offlineMode: true,
        holdoutTests,
        autoGitCommit: false,
        sqliteDbPath: path.join(dir, "memory.sqlite"),
        freezeStatePath: path.join(dir, "freeze.json"),
        interactiveDashboard: false
      });
      const engine = new ConvergenceEngine(config, false);
      const report = await engine.executeBuild(spec, dir);
      const log = fs.existsSync(logPath) ? fs.readFileSync(logPath, "utf8") : "";
      return { dir, log, converged: report.converged };
    } finally {
      if (previousLog === undefined) delete process.env.FOUNDRY_LOG_PATH;
      else process.env.FOUNDRY_LOG_PATH = previousLog;
    }
  }

  it("runs the hidden suite as a gate and saves it under .foundry/holdout, never in the workspace", async () => {
    const { dir, log, converged } = await build(true);
    expect(converged).toBe(true);
    expect(fs.existsSync(path.join(dir, "tests", "module.test.ts"))).toBe(true);
    expect(fs.existsSync(path.join(dir, "tests", "module.holdout.test.ts"))).toBe(false);
    const saved = path.join(dir, ".foundry", "holdout", "tests", "module.holdout.test.ts");
    expect(fs.existsSync(saved)).toBe(true);
    expect(fs.readFileSync(saved, "utf8")).toContain("runHiddenSuite");
    if (log) {
      expect(log).toContain("Hidden acceptance suite written for");
      expect(log).toMatch(/Branch Alpha step 1: .*tests passed, hidden tests passed/);
    }
    fs.rmSync(dir, { recursive: true, force: true });
  }, 60000);

  it("skips the hidden suite when the setting is off", async () => {
    const { dir, log, converged } = await build(false);
    expect(converged).toBe(true);
    expect(fs.existsSync(path.join(dir, ".foundry", "holdout"))).toBe(false);
    if (log) {
      expect(log).not.toContain("Hidden acceptance suite");
      expect(log).toMatch(/Branch Alpha step 1: .*tests passed$/m);
    }
    fs.rmSync(dir, { recursive: true, force: true });
  }, 60000);
});
