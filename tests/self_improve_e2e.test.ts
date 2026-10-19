import { describe, it, expect } from "vitest";
import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

/**
 * Offline end-to-end check of carryover: a round that is not kept saves its partial work, and the next
 * round restores it and finishes it. Runs on a throwaway copy of Foundry (never the real folder) and
 * takes about a minute, because every round runs the copy's full test suite, so it is opt-in:
 *   FOUNDRY_E2E=1 npx vitest run tests/self_improve_e2e.test.ts
 */
const enabled = process.env.FOUNDRY_E2E === "1";
const ROOT = path.resolve(__dirname, "..");

function makeCopy(): { tmp: string; copy: string } {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-e2e-carry-"));
  const copy = path.join(tmp, "foundry-copy");
  fs.mkdirSync(copy);
  for (const entry of ["src", "tests", "package.json", "tsconfig.json", "vitest.config.ts"]) {
    fs.cpSync(path.join(ROOT, entry), path.join(copy, entry), { recursive: true });
  }
  fs.symlinkSync(path.join(ROOT, "node_modules"), path.join(copy, "node_modules"), "junction");
  const tsc = spawnSync(process.execPath, [path.join(ROOT, "node_modules", "typescript", "bin", "tsc"), "-p", "tsconfig.json"], { cwd: copy });
  if (tsc.status !== 0) throw new Error(`building the copy failed: ${tsc.stdout}${tsc.stderr}`);
  return { tmp, copy };
}

function selfImprove(copy: string, tmp: string): { status: number | null; events: Array<Record<string, unknown>> } {
  const env: NodeJS.ProcessEnv = {
    ...process.env,
    FOUNDRY_ROOT_DIR: copy,
    FOUNDRY_FORCE_LOCAL_SANDBOX: "true",
    FOUNDRY_SQLITE_PATH: path.join(tmp, "mem.sqlite"),
    FOUNDRY_FREEZE_PATH: path.join(tmp, "freeze.json"),
    FOUNDRY_LOG_PATH: path.join(tmp, "engine.log"),
    FOUNDRY_DASHBOARD: "false"
  };
  delete env.FOUNDRY_E2E; // the copy's own test suite must not start this test again
  const run = spawnSync(
    process.execPath,
    [path.join(copy, "dist", "index.js"), "self-improve", "--offline", "--budget-usd", "2", "--rounds", "1", "--progress"],
    { cwd: tmp, env, encoding: "utf8", timeout: 300000 }
  );
  const events = (run.stdout ?? "")
    .split(/\r?\n/)
    .filter((l) => l.startsWith("@@FOUNDRY "))
    .map((l) => JSON.parse(l.slice("@@FOUNDRY ".length)) as Record<string, unknown>);
  return { status: run.status, events };
}

function readJson<T>(file: string): T {
  return JSON.parse(fs.readFileSync(file, "utf8")) as T;
}

describe.skipIf(!enabled)("self-improvement carryover (offline, on a copy of Foundry)", () => {
  it("saves a failed round's partial work and finishes it in the next round", () => {
    const { tmp, copy } = makeCopy();
    try {
      // Round 1: the offline stand-in writes src/module.ts plus tests/module.test.ts. That test file is not a
      // vitest suite, so the copy's `npm test` fails, the round is not kept, and everything is rolled back.
      const first = selfImprove(copy, tmp);
      expect(first.status).toBe(2);
      expect(fs.existsSync(path.join(copy, "src", "module.ts"))).toBe(false);
      const saving = first.events.find((e) => e.type === "self-step" && e.step === "saving");
      expect(saving?.detail).toBe("saved 1 file(s) of partial work for the next round");

      const carryDir = path.join(copy, ".foundry", "carryover");
      const [id] = fs.readdirSync(carryDir);
      const manifest = readJson<{ status: string; reason: string; files: Array<{ path: string; baseHash: string | null }> }>(
        path.join(carryDir, id, "manifest.json")
      );
      expect(manifest).toMatchObject({ status: "pending", reason: "verification-failed", files: [{ path: "src/module.ts", baseHash: null }] });
      const journal = readJson<{ entries: Array<Record<string, unknown>> }>(path.join(copy, ".foundry", "journal.json"));
      expect(journal.entries.at(-1)).toMatchObject({ outcome: "not-finished", carryoverId: id });

      // Let the copy's suite accept the stand-in's test file, so the continuation can pass verification.
      const vitestConfig = path.join(copy, "vitest.config.ts");
      fs.writeFileSync(
        vitestConfig,
        fs.readFileSync(vitestConfig, "utf8").replace('exclude: ["demo-output/**"', 'exclude: ["tests/module.test.ts", "demo-output/**"')
      );

      // Round 2: no focus, so it continues the saved work: src/module.ts is restored first, then finished and kept.
      const second = selfImprove(copy, tmp);
      const outcome = second.events.filter((e) => e.type === "round-done" || e.type === "finished" || e.step === "continuing");
      expect(second.status, JSON.stringify(outcome, null, 2)).toBe(0);
      const continuing = second.events.find((e) => e.type === "self-step" && e.step === "continuing");
      expect(String(continuing?.detail)).toMatch(/of earlier work · 1 file\(s\) restored$/);
      expect(second.events.at(-1)).toMatchObject({ type: "finished", outcome: "done" });
      expect(fs.existsSync(path.join(copy, "src", "module.ts"))).toBe(true);

      expect(readJson<{ status: string }>(path.join(carryDir, id, "manifest.json")).status).toBe("consumed");
      const after = readJson<{ entries: Array<Record<string, unknown>> }>(path.join(copy, ".foundry", "journal.json"));
      expect(after.entries.at(-1)).toMatchObject({ outcome: "done", continuedFrom: id });
      expect(String(after.entries.at(-1)?.summary)).toContain("It finished the unfinished earlier round.");
    } finally {
      // rmSync does not follow the node_modules junction, so the real dependencies are untouched.
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }, 600000);
});
