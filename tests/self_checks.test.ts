import { describe, it } from "vitest";
import * as fs from "fs";
import * as path from "path";

/**
 * Runs the regression checks that `foundry self-improve` adds with each kept
 * improvement (tests/self-checks/*.check.ts: standalone node:assert scripts),
 * so every improvement keeps being verified by `npm test` from then on.
 */
const dir = path.join(__dirname, "self-checks");
const checks = fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => f.endsWith(".check.ts")).sort() : [];

describe("self-improvement regression checks", () => {
  if (checks.length === 0) {
    it.skip("no self-improvement checks yet", () => undefined);
  }
  for (const file of checks) {
    it(file, async () => {
      await import(path.join(dir, file));
    });
  }
});
