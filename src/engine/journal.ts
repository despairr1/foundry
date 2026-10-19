import * as fs from "fs";
import * as path from "path";

export interface JournalEntry {
  timestamp: string;
  task: string;
  mode: "build" | "improve";
  /**
   * "stopped" is written by the GUI when the user presses Stop (the engine is killed and cannot record it).
   * "undone" marks a self-improvement the user rolled back, so later rounds do not redo it.
   */
  outcome: "done" | "not-finished" | "spending-limit" | "error" | "stopped" | "undone";
  /** Self-improvement only: where the pre-change originals are kept (`.foundry/backups/<id>`), for Undo. */
  backupId?: string;
  /** Self-improvement only: partial work this round saved for the next one (`.foundry/carryover/<id>`). */
  carryoverId?: string;
  /** Self-improvement only: the carryover this round continued. */
  continuedFrom?: string;
  summary: string;
  filesChanged: string[];
  modules: Array<{ name: string; status: string }>;
  spentUsd: number;
  limitUsd: number;
  commits: string[];
  /** What blocked the run, so later runs can avoid repeating the same dead end. */
  problem?: string;
}

const JOURNAL_DIR = ".foundry";
const JOURNAL_FILE = "journal.json";
const MAX_STORED_ENTRIES = 50;
const PROMPT_ENTRIES = 10;

/**
 * Per-project history of every Foundry run in a folder: what was asked, what
 * changed, and what went wrong. It is fed back into the next run's prompt so
 * work stacks up instead of starting over. Lives in `<project>/.foundry/`,
 * which the workspace scanner and git commits ignore.
 */
export class ProjectJournal {
  private readonly filePath: string;
  private entries: JournalEntry[];

  private constructor(projectDir: string, entries: JournalEntry[]) {
    this.filePath = path.join(projectDir, JOURNAL_DIR, JOURNAL_FILE);
    this.entries = entries;
  }

  public static load(projectDir: string): ProjectJournal {
    const filePath = path.join(projectDir, JOURNAL_DIR, JOURNAL_FILE);
    let entries: JournalEntry[] = [];
    try {
      const parsed = JSON.parse(fs.readFileSync(filePath, "utf8")) as { entries?: JournalEntry[] };
      if (Array.isArray(parsed.entries)) entries = parsed.entries;
    } catch {
      // Missing or unreadable journal: start a fresh history.
    }
    return new ProjectJournal(projectDir, entries);
  }

  public getEntries(): ReadonlyArray<JournalEntry> {
    return this.entries;
  }

  public append(entry: JournalEntry): void {
    this.entries.push(entry);
    this.entries = this.entries.slice(-MAX_STORED_ENTRIES);
    const dir = path.dirname(this.filePath);
    fs.mkdirSync(dir, { recursive: true });
    const ignorePath = path.join(dir, ".gitignore");
    if (!fs.existsSync(ignorePath)) fs.writeFileSync(ignorePath, "*\n", "utf8");
    fs.writeFileSync(this.filePath, JSON.stringify({ version: 1, entries: this.entries }, null, 2), "utf8");
  }

  /** The recent history, phrased for the models. Empty string when there is none. */
  public formatForPrompt(): string {
    const recent = this.entries.slice(-PROMPT_ENTRIES);
    if (recent.length === 0) return "";
    const offset = this.entries.length - recent.length;
    const lines = recent.map((e, idx) => {
      const files = e.filesChanged.length > 0 ? ` Files changed: ${e.filesChanged.join(", ")}.` : "";
      const problem = e.problem ? ` What went wrong: ${e.problem.slice(0, 300)}` : "";
      return `${offset + idx + 1}. [${e.timestamp.slice(0, 10)}] "${e.task.slice(0, 300)}" -> ${e.outcome}. ${e.summary}${files}${problem}`;
    });
    return [
      `Project history (earlier Foundry runs in this folder, oldest first):`,
      ...lines,
      `Build on the code that is already there instead of starting over, keep work that was finished, and do not repeat approaches that already failed.`
    ].join("\n");
  }
}
