import { describe, it, expect, beforeEach, afterEach } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import {
  applyCarryover,
  carryoverPrompt,
  discardPendingCarryovers,
  hashContent,
  listCarryovers,
  MAX_FAILED_CONTINUATIONS,
  recordFailedContinuation,
  saveCarryover,
  selectCarryover
} from "../src/singularity/carryover";
import { PartialProgress } from "../src/engine/convergence";

let root: string;

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-carry-"));
  fs.mkdirSync(path.join(root, "src"), { recursive: true });
  fs.writeFileSync(path.join(root, "src", "parse.ts"), "export const v = 1;\n");
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

const contract = { moduleName: "json_repair", targetFilePath: "src/parse.ts", contracts: [{ symbolName: "repairJson" }] };

function partial(workspace: Record<string, string>): PartialProgress {
  return {
    plan: ["json_repair"],
    moduleName: "json_repair",
    contract: contract as unknown as PartialProgress["contract"],
    testFilePath: "tests/self-checks/json_repair.check.ts",
    testCode: "import assert from 'node:assert/strict';",
    workspace: new Map(Object.entries(workspace)),
    lastFeedback: "AssertionError: expected repairJson('{a:1}') to equal {a:1}",
    fitness: 12
  };
}

function save(overrides: Partial<Parameters<typeof saveCarryover>[1]> = {}) {
  const before = new Map([["src/parse.ts", Buffer.from("export const v = 1;\n")]]);
  return saveCarryover(root, {
    before,
    diskFiles: new Map(),
    partial: partial({
      "src/parse.ts": "export const v = 2;\n",
      "src/new_helper.ts": "export function repairJson() {}\n",
      "tests/self-checks/json_repair.check.ts": "check",
      "gui/main.py": "print('x')"
    }),
    label: "Make JSON parsing more reliable",
    focus: "  Make JSON   parsing more reliable ",
    reason: "spending-limit",
    problem: "Pre-flight budget check failed",
    spentUsd: 9.67,
    parent: null,
    ...overrides
  });
}

describe("saving partial work", () => {
  it("carries only changed src/ files, with fingerprints of what they were based on", () => {
    const saved = save()!;
    expect(saved.status).toBe("pending");
    expect(saved.focus).toBe("make json parsing more reliable");
    expect(saved.files).toEqual([
      { path: "src/parse.ts", baseHash: hashContent(Buffer.from("export const v = 1;\n")) },
      { path: "src/new_helper.ts", baseHash: null }
    ]);
    expect(saved.testCode).toContain("node:assert");
    const dir = path.join(root, ".foundry", "carryover", saved.id, "files");
    expect(fs.readFileSync(path.join(dir, "src", "parse.ts"), "utf8")).toBe("export const v = 2;\n");
    expect(fs.existsSync(path.join(dir, "tests"))).toBe(false);
    expect(fs.existsSync(path.join(dir, "gui"))).toBe(false);
  });

  it("returns nothing when the round produced neither code nor a contract", () => {
    expect(save({ partial: null })).toBeNull();
  });

  it("keeps changed files from disk when the engine has no in-memory attempt", () => {
    const saved = save({ partial: null, diskFiles: new Map([["src/parse.ts", "export const v = 3;\n"]]) })!;
    expect(saved.files.map((f) => f.path)).toEqual(["src/parse.ts"]);
    expect(saved.moduleName).toBeNull();
  });
});

describe("choosing what the next round continues", () => {
  it("matches the same focus, or takes the newest when the round has no focus", () => {
    const a = save()!;
    const b = save({ focus: "speed up AST parsing", label: "speed up AST parsing" })!;
    expect(selectCarryover(root, "make json PARSING more reliable")?.id).toBe(a.id);
    expect(selectCarryover(root, "something else")).toBeNull();
    expect(selectCarryover(root, "")?.id).toBe(b.id);
    expect(selectCarryover(root, undefined)?.id).toBe(b.id);
  });

  it("forgets discarded work", () => {
    save();
    expect(discardPendingCarryovers(root)).toBe(1);
    expect(selectCarryover(root)).toBeNull();
    expect(listCarryovers(root)[0].status).toBe("discarded");
  });
});

describe("restoring partial work", () => {
  it("writes the carried files when their base files are unchanged", () => {
    const saved = save()!;
    const result = applyCarryover(root, saved);
    expect(result).toEqual({ applied: true, files: ["src/parse.ts", "src/new_helper.ts"] });
    expect(fs.readFileSync(path.join(root, "src", "parse.ts"), "utf8")).toBe("export const v = 2;\n");
    expect(fs.existsSync(path.join(root, "src", "new_helper.ts"))).toBe(true);
  });

  it("refuses, without writing anything, when a base file changed since (e.g. a newer kept improvement)", () => {
    const saved = save()!;
    fs.writeFileSync(path.join(root, "src", "parse.ts"), "export const v = 99; // newer kept improvement\n");
    const result = applyCarryover(root, saved);
    expect(result.applied).toBe(false);
    expect(result.reason).toBe("src/parse.ts has changed since");
    expect(fs.readFileSync(path.join(root, "src", "parse.ts"), "utf8")).toContain("v = 99");
    expect(fs.existsSync(path.join(root, "src", "new_helper.ts"))).toBe(false);
  });

  it("refuses when a file the round created now exists with other content", () => {
    const saved = save()!;
    fs.writeFileSync(path.join(root, "src", "new_helper.ts"), "export const unrelated = true;\n");
    expect(applyCarryover(root, saved).applied).toBe(false);
  });

  it("tells the models to finish the earlier attempt, with its contract, failure and check", () => {
    const saved = save()!;
    const prompt = carryoverPrompt(saved, applyCarryover(root, saved));
    expect(prompt).toContain("Continue an unfinished earlier attempt instead of starting over.");
    expect(prompt).toContain("ran out of budget before it finished (about $9.67 of work so far)");
    expect(prompt).toContain("already in the workspace: src/parse.ts, src/new_helper.ts");
    expect(prompt).toContain("repairJson");
    expect(prompt).toContain("AssertionError");
    expect(prompt).toContain("tests/self-checks/json_repair.check.ts");
  });
});

describe("retiring a line of work", () => {
  it("supersedes the parent, sums the spend, and gives up after two failed continuations", () => {
    const first = save()!;
    const second = save({ parent: first, spentUsd: 1.5 })!;
    expect(listCarryovers(root).find((c) => c.id === first.id)?.status).toBe("superseded");
    expect(second.failedContinuations).toBe(1);
    expect(second.spentUsd).toBeCloseTo(11.17);
    expect(second.status).toBe("pending");

    const third = save({ parent: second, spentUsd: 1 })!;
    expect(third.failedContinuations).toBe(MAX_FAILED_CONTINUATIONS);
    expect(third.status).toBe("abandoned");
    expect(selectCarryover(root)).toBeNull();
  });

  it("counts a failed continuation that produced nothing new", () => {
    const first = save()!;
    recordFailedContinuation(root, first);
    expect(listCarryovers(root)[0].status).toBe("pending");
    recordFailedContinuation(root, first);
    expect(listCarryovers(root)[0].status).toBe("abandoned");
  });
});
