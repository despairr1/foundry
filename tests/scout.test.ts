import { describe, it, expect } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { loadConfig } from "../src/config";
import { EconomicsEngine } from "../src/engine/economics";
import {
  chooseImprovement,
  findTodos,
  focusFromCandidate,
  isGenericFocus,
  parseCandidates,
  parseChoice,
  rankPrompt,
  scoutPrompt,
  sourceMap
} from "../src/singularity/scout";

const evidence = {
  health: { typecheck: true, tests: true },
  problems: ["[WARN] [Backoff] ClaudeArchitect[Beta] failed (attempt 1/4): bad JSON"],
  history: 'Project history:\n1. [2026-10-17] "Fix TS6133" -> done.',
  sourceMap: "src/swarm/providers.ts (418 lines)",
  todos: ["src/engine/mutation.ts:12: // TODO: skip generated files"]
};

describe("scouting for an improvement", () => {
  it("treats 'improve yourself' and the like as no focus at all", () => {
    for (const focus of [undefined, "", "   ", "improve yourself", "Just improve yourself!", "improve itself", "make yourself better",
      "do your thing", "whatever you think", "self-improve", "improve", "optimize the engine", "fix things", "your choice"]) {
      expect(isGenericFocus(focus), String(focus)).toBe(true);
    }
    for (const focus of ["make JSON parsing of model output more reliable", "fix the parser", "improve the retry logic in providers.ts",
      "Fix TS6133 in src/ast/semantic_patcher.ts"]) {
      expect(isGenericFocus(focus), focus).toBe(false);
    }
  });

  it("parses the architect's candidates and keeps only usable ones", () => {
    const text = `Here you go:\n\`\`\`json\n[
      {"title": "Harden extractJson against trailing commas", "files": ["src/swarm/providers.ts"], "change": "Strip trailing commas before JSON.parse", "why": "bad JSON warnings", "verify": "parses {\\"a\\":1,}"},
      {"title": "No files", "files": [], "change": "x"},
      {"title": "Outside src", "files": ["gui/main.py"], "change": "x"},
      {"title": "Fourth", "files": ["src/a.ts"], "change": "a"},
      {"title": "Fifth", "files": ["src/b.ts"], "change": "b"}
    ]\n\`\`\``;
    const candidates = parseCandidates(text);
    expect(candidates.map((c) => c.title)).toEqual(["Harden extractJson against trailing commas", "Fourth", "Fifth"]);
    expect(candidates[0].files).toEqual(["src/swarm/providers.ts"]);
    expect(() => parseCandidates('[{"title": "x", "files": ["README.md"], "change": "y"}]')).toThrow(/usable candidate/);
  });

  it("parses the auditor's choice and rejects one out of range", () => {
    expect(parseChoice('{"choice": 2, "reason": "smallest and best evidenced"}', 3)).toEqual({ index: 1, reason: "smallest and best evidenced" });
    expect(() => parseChoice('{"choice": 4}', 3)).toThrow(/1 to 3/);
    expect(() => parseChoice('{"choice": "none"}', 3)).toThrow();
  });

  it("gives the models the evidence, the rules and the answer shape", () => {
    const prompt = scoutPrompt(evidence);
    expect(prompt).toContain("bad JSON");
    expect(prompt).toContain("TODO: skip generated files");
    expect(prompt).toContain("src/swarm/providers.ts (418 lines)");
    expect(prompt).toContain('"title"');
    expect(prompt).toContain("tests/self-checks/");
    const candidate = { title: "A", files: ["src/a.ts"], change: "c", why: "w", verify: "v" };
    const ranking = rankPrompt([candidate, { ...candidate, title: "B" }], evidence);
    expect(ranking).toContain("1. A");
    expect(ranking).toContain("2. B");
    expect(ranking).toContain('{"choice": <1-2>');
    expect(focusFromCandidate(candidate)).toBe("A\nFiles: src/a.ts\nChange: c\nWhy: w\nThe check must prove: v");
  });

  it("maps the source tree and finds TODO notes", () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-scout-"));
    try {
      fs.mkdirSync(path.join(root, "src", "deep"), { recursive: true });
      fs.writeFileSync(path.join(root, "src", "a.ts"), "export const a = 1;\n// TODO: rename\n");
      fs.writeFileSync(path.join(root, "src", "deep", "b.ts"), "export const b = 2;\n");
      fs.writeFileSync(path.join(root, "src", "types.d.ts"), "declare const x: number;\n");
      expect(sourceMap(root)).toBe("src/a.ts (3 lines)\nsrc/deep/b.ts (2 lines)");
      expect(findTodos(root)).toEqual(["src/a.ts:2: // TODO: rename"]);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("chooses a fixed candidate offline without calling any API", async () => {
    const config = loadConfig({ offlineMode: true, anthropicApiKey: "", geminiApiKey: "", astraApiKey: "" });
    const economics = new EconomicsEngine(1, path.join(os.tmpdir(), "foundry-scout-freeze.json"), false);
    const choice = await chooseImprovement(config, economics, evidence);
    expect(choice.candidate.files).toEqual(["src/swarm/providers.ts"]);
    expect(choice.candidates).toHaveLength(1);
    expect(economics.getTotalSpentUsd()).toBe(0);
  });
});
