import { execFile } from "child_process";
import { promisify } from "util";
import * as fs from "fs";
import * as path from "path";
import { logger } from "./logger";

const execFileAsync = promisify(execFile);

export class GitManager {
  private repoDir: string;

  constructor(repoDir: string) {
    this.repoDir = path.resolve(repoDir);
  }

  public async ensureInitialized(): Promise<void> {
    if (!fs.existsSync(this.repoDir)) {
      fs.mkdirSync(this.repoDir, { recursive: true });
    }
    const gitDir = path.join(this.repoDir, ".git");
    if (!fs.existsSync(gitDir)) {
      await this.runGit(["init"]);
      await this.runGit(["config", "user.name", "Foundry Autonomous Engine"]);
      await this.runGit(["config", "user.email", "foundry@autonomous.local"]);
      logger.info("Git", `Initialized repository at ${this.repoDir}`);
    }
  }

  public async createOrCheckoutBranch(branchName: string): Promise<void> {
    await this.ensureInitialized();
    try {
      await this.runGit(["rev-parse", "--verify", "HEAD"]);
    } catch {
      const gitkeep = path.join(this.repoDir, ".gitkeep");
      if (!fs.existsSync(gitkeep)) {
        fs.writeFileSync(gitkeep, "", "utf8");
      }
      await this.runGit(["add", ".gitkeep"]);
      await this.runGit(["commit", "-m", "chore: initial foundry root commit"]);
    }

    try {
      await this.runGit(["checkout", "-B", branchName]);
    } catch (err) {
      logger.warn("Git", `Branch checkout warning for ${branchName}: ${String(err)}`);
    }
  }

  /** Commits exactly `files`; anything else the user has staged or modified is left out of the commit. */
  public async stageAndCommit(files: string[], message: string): Promise<string> {
    await this.ensureInitialized();
    const relativeFiles = files.map((file) => (path.isAbsolute(file) ? path.relative(this.repoDir, file) : file));
    if (relativeFiles.length === 0) {
      return await this.getCurrentCommitHash();
    }
    await this.runGit(["add", "--", ...relativeFiles]);
    const staged = await this.runGit(["diff", "--cached", "--name-only", "--", ...relativeFiles]);
    if (!staged.trim()) {
      return await this.getCurrentCommitHash();
    }
    await this.runGit(["commit", "-m", message, "--", ...relativeFiles]);
    const commitHash = await this.getCurrentCommitHash();
    logger.info("Git", `Committed ${commitHash.slice(0, 8)}: ${message}`);
    return commitHash;
  }

  public async getCurrentCommitHash(): Promise<string> {
    try {
      const out = await this.runGit(["rev-parse", "HEAD"]);
      return out.trim();
    } catch {
      return "00000000";
    }
  }

  public async snapshotFiles(files: string[]): Promise<Map<string, string>> {
    const map = new Map<string, string>();
    for (const file of files) {
      const fullPath = path.isAbsolute(file) ? file : path.join(this.repoDir, file);
      if (fs.existsSync(fullPath)) {
        map.set(fullPath, fs.readFileSync(fullPath, "utf8"));
      }
    }
    return map;
  }

  public async restoreSnapshot(snapshot: Map<string, string>): Promise<void> {
    for (const [fullPath, content] of snapshot.entries()) {
      fs.mkdirSync(path.dirname(fullPath), { recursive: true });
      fs.writeFileSync(fullPath, content, "utf8");
    }
  }

  private async runGit(args: string[]): Promise<string> {
    const { stdout } = await execFileAsync("git", args, {
      cwd: this.repoDir,
      maxBuffer: 10 * 1024 * 1024,
      windowsHide: true
    });
    return stdout;
  }
}