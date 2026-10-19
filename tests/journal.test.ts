import { describe, it, expect } from "vitest";
import { execFileSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { ProjectJournal } from "../src/engine/journal";
import { workspaceHasCode } from "../src/engine/convergence";

const entry = (task: string, outcome: "done" | "not-finished", problem?: string) => ({
  timestamp: "2026-10-16T12:00:00.000Z",
  task,
  mode: "build" as const,
  outcome,
  summary: outcome === "done" ? "Finished: 1 part(s), 2 file(s) changed." : "Not finished: 0/1 part(s) done.",
  filesChanged: outcome === "done" ? ["src/todo.ts"] : [],
  modules: [],
  spentUsd: 0.5,
  limitUsd: 5,
  commits: [],
  problem
});

describe("project journal", () => {
  it("persists runs, stays out of git, and feeds earlier attempts into the next prompt", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-journal-"));
    try {
      expect(ProjectJournal.load(dir).formatForPrompt()).toBe("");

      ProjectJournal.load(dir).append(entry("Build a to-do app", "done"));
      ProjectJournal.load(dir).append(entry("Add due dates", "not-finished", "TS2322: Type 'string' is not assignable to type 'Date'"));

      const journal = ProjectJournal.load(dir);
      expect(journal.getEntries()).toHaveLength(2);
      const prompt = journal.formatForPrompt();
      expect(prompt).toContain(`1. [2026-10-16] "Build a to-do app" -> done.`);
      expect(prompt).toContain("Files changed: src/todo.ts.");
      expect(prompt).toContain(`2. [2026-10-16] "Add due dates" -> not-finished.`);
      expect(prompt).toContain("What went wrong: TS2322");
      expect(prompt).toContain("do not repeat approaches that already failed");
      expect(fs.readFileSync(path.join(dir, ".foundry", ".gitignore"), "utf8")).toBe("*\n");
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("keeps the newest 50 runs and puts only the last 10 into the prompt", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-journal-"));
    try {
      for (let i = 1; i <= 55; i += 1) ProjectJournal.load(dir).append(entry(`task ${i}`, "done"));
      const journal = ProjectJournal.load(dir);
      expect(journal.getEntries()).toHaveLength(50);
      expect(journal.getEntries()[0].task).toBe("task 6");
      const prompt = journal.formatForPrompt();
      expect(prompt).toContain(`41. [2026-10-16] "task 46"`);
      expect(prompt).not.toContain(`"task 45"`);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("starts fresh when the journal file is corrupt", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-journal-"));
    try {
      fs.mkdirSync(path.join(dir, ".foundry"));
      fs.writeFileSync(path.join(dir, ".foundry", "journal.json"), "{not json", "utf8");
      expect(ProjectJournal.load(dir).getEntries()).toEqual([]);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("new vs existing project detection", () => {
  it("treats missing, empty, docs-only and journal-only folders as new, and source folders as existing", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-detect-"));
    try {
      expect(workspaceHasCode(path.join(dir, "missing"))).toBe(false);
      expect(workspaceHasCode(dir)).toBe(false);
      fs.writeFileSync(path.join(dir, "README.md"), "# notes", "utf8");
      fs.mkdirSync(path.join(dir, ".foundry"));
      fs.writeFileSync(path.join(dir, ".foundry", "journal.json"), "{}", "utf8");
      fs.mkdirSync(path.join(dir, "node_modules", "pkg"), { recursive: true });
      fs.writeFileSync(path.join(dir, "node_modules", "pkg", "index.js"), "", "utf8");
      expect(workspaceHasCode(dir)).toBe(false);
      fs.mkdirSync(path.join(dir, "src"));
      fs.writeFileSync(path.join(dir, "src", "app.py"), "print('hi')\n", "utf8");
      expect(workspaceHasCode(dir)).toBe(true);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("foundry go (offline)", () => {
  it("builds first, then improves the same folder with the earlier run remembered", () => {
    const root = path.resolve(__dirname, "..");
    const dist = path.join(root, "dist", "index.js");
    if (!fs.existsSync(dist)) return; // dist/ is produced by `npm run build`
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-go-"));
    const target = path.join(tmp, "proj");
    const env = {
      ...process.env,
      FOUNDRY_FORCE_LOCAL_SANDBOX: "true",
      FOUNDRY_SQLITE_PATH: path.join(tmp, "mem.sqlite"),
      FOUNDRY_FREEZE_PATH: path.join(tmp, "freeze.json"),
      FOUNDRY_LOG_PATH: path.join(tmp, "log")
    };
    const run = () =>
      execFileSync(
        process.execPath,
        [dist, "go", "--task", "Build symbol execute_core_pipeline that computes 2*x + 1.", "--target", target, "--budget-usd", "2", "--offline", "--progress"],
        { env, encoding: "utf8" }
      )
        .split(/\r?\n/)
        .filter((l) => l.startsWith("@@FOUNDRY "))
        .map((l) => JSON.parse(l.slice("@@FOUNDRY ".length)));
    try {
      const first = run();
      expect(first[0]).toMatchObject({ type: "start", mode: "build", historyEntries: 0, limitUsd: 2 });
      expect(first.at(-1)).toMatchObject({ type: "finished", outcome: "done" });

      const second = run();
      expect(second[0]).toMatchObject({ type: "start", mode: "improve", historyEntries: 1 });
      expect(ProjectJournal.load(target).getEntries().map((e) => e.mode)).toEqual(["build", "improve"]);
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }, 120000);
});
