import * as fs from "fs";
import * as path from "path";

export interface BranchVisualStatus {
  id: string;
  status: "ACTIVE" | "PRUNED" | "CONVERGED" | "PENDING";
  fitness: number;
  diagnosticsCount: number;
  mutationKillRatio: number;
  iteration: number;
}

export interface DashboardState {
  mode: "BUILD" | "PATCH" | "IDLE";
  targetDir: string;
  activeModule: string;
  branches: BranchVisualStatus[];
  lspLatencyMs: number;
  mutationSurvivalRatio: number;
  spentUsd: number;
  maxBudgetUsd: number;
  burnRateUsdPerMin: number;
  ouroborosStatus:
    | "NOMINAL"
    | "BOTTLENECK_DETECTED"
    | "SELF_PATCHING"
    | "RECOMPILING"
    | "UPGRADE_STAGED"
    | "HOT_RELOADED"
    | "AUTO_RESTARTING";
  ouroborosGeneration: number;
  recentLogs: string[];
}

const DASHBOARD_WIDTH = 104;

function padLine(content: string): string {
  const inner = content.length > DASHBOARD_WIDTH ? content.slice(0, DASHBOARD_WIDTH) : content.padEnd(DASHBOARD_WIDTH);
  return `║${inner}║`;
}

export class Logger {
  private state: DashboardState;
  private startTimeMs: number;
  private logFilePath: string;
  private interactive: boolean;
  private renderScheduled = false;

  constructor(interactive = true) {
    this.interactive = interactive && Boolean(process.stdout.isTTY);
    this.startTimeMs = Date.now();
    this.logFilePath = process.env.FOUNDRY_LOG_PATH ?? path.resolve(process.cwd(), ".foundry.log");
    this.state = {
      mode: "IDLE",
      targetDir: process.cwd(),
      activeModule: "initializing",
      branches: [
        { id: "Alpha", status: "PENDING", fitness: 0, diagnosticsCount: 0, mutationKillRatio: 0, iteration: 0 },
        { id: "Beta", status: "PENDING", fitness: 0, diagnosticsCount: 0, mutationKillRatio: 0, iteration: 0 },
        { id: "Gamma", status: "PENDING", fitness: 0, diagnosticsCount: 0, mutationKillRatio: 0, iteration: 0 }
      ],
      lspLatencyMs: 0,
      mutationSurvivalRatio: 0,
      spentUsd: 0,
      maxBudgetUsd: 5.0,
      burnRateUsdPerMin: 0,
      ouroborosStatus: "NOMINAL",
      ouroborosGeneration: 1,
      recentLogs: []
    };
  }

  public setInteractive(enabled: boolean): void {
    this.interactive = enabled && Boolean(process.stdout.isTTY);
  }

  public updateState(partial: Partial<DashboardState>): void {
    this.state = { ...this.state, ...partial };
    const elapsedMin = Math.max((Date.now() - this.startTimeMs) / 60000, 0.01);
    this.state.burnRateUsdPerMin = this.state.spentUsd / elapsedMin;
    this.scheduleRender();
  }

  public updateBranch(branchId: string, update: Partial<BranchVisualStatus>): void {
    const idx = this.state.branches.findIndex((b) => b.id === branchId);
    if (idx >= 0) {
      this.state.branches[idx] = { ...this.state.branches[idx], ...update };
    } else {
      this.state.branches.push({
        id: branchId,
        status: "ACTIVE",
        fitness: 0,
        diagnosticsCount: 0,
        mutationKillRatio: 0,
        iteration: 0,
        ...update
      });
    }
    this.scheduleRender();
  }

  public info(component: string, message: string): void {
    this.record("INFO", component, message);
  }

  public warn(component: string, message: string): void {
    this.record("WARN", component, message);
  }

  public error(component: string, message: string): void {
    this.record("ERROR", component, message);
  }

  private record(level: string, component: string, message: string): void {
    const timestamp = new Date().toISOString().slice(11, 23);
    const line = `[${timestamp}] [${level}] [${component}] ${message}`;
    try {
      fs.appendFileSync(this.logFilePath, line + "\n", "utf8");
    } catch {
      // The log file is best-effort; the console/dashboard still receives the line.
    }
    this.state.recentLogs.push(line);
    if (this.state.recentLogs.length > 10) {
      this.state.recentLogs.shift();
    }
    if (!this.interactive) {
      const stream = level === "ERROR" ? process.stderr : process.stdout;
      stream.write(line + "\n");
    } else {
      this.scheduleRender();
    }
  }

  // Coalesce bursts of state updates into a single repaint per tick.
  private scheduleRender(): void {
    if (!this.interactive || this.renderScheduled) return;
    this.renderScheduled = true;
    setImmediate(() => {
      this.renderScheduled = false;
      this.render();
    });
  }

  public render(): void {
    if (!this.interactive) {
      return;
    }

    const budgetPct = Math.min(100, (this.state.spentUsd / Math.max(this.state.maxBudgetUsd, 0.0001)) * 100);
    const filledBars = Math.round(budgetPct / 5);
    const budgetBar = "█".repeat(filledBars) + "░".repeat(Math.max(0, 20 - filledBars));
    const rule = "═".repeat(DASHBOARD_WIDTH);

    const branchLines = this.state.branches.map((b) => {
      const icon =
        b.status === "CONVERGED" ? "✔" : b.status === "PRUNED" ? "✖" : b.status === "ACTIVE" ? "▶" : "○";
      return padLine(
        `  [${icon}] Branch ${b.id.padEnd(6)} | Status: ${b.status.padEnd(9)} | Iter: ${String(b.iteration).padEnd(2)} | ` +
          `Fitness: ${b.fitness.toFixed(2).padStart(6)} | LSP Err: ${String(b.diagnosticsCount).padEnd(3)} | ` +
          `Mut Kill: ${(b.mutationKillRatio * 100).toFixed(0)}%`
      );
    });

    const logLines = this.state.recentLogs.map((l) => padLine(`  ${l}`));

    const screen = [
      "\x1b[2J\x1b[0;0H" + `╔${rule}╗`,
      padLine(
        ` FOUNDRY AUTONOMOUS ENGINEERING ENGINE | Mode: ${this.state.mode.padEnd(6)} | Module: ${this.state.activeModule}`
      ),
      `╠${rule}╣`,
      padLine(` Target: ${this.state.targetDir}`),
      `╠${rule}╣`,
      padLine(" TELEMETRY & ECONOMICS"),
      padLine(
        `   Token Spend : [${budgetBar}] $${this.state.spentUsd.toFixed(4)} / $${this.state.maxBudgetUsd.toFixed(2)} ` +
          `(${budgetPct.toFixed(1)}%) | Burn: $${this.state.burnRateUsdPerMin.toFixed(4)}/min`
      ),
      padLine(
        `   LSP Latency : ${(this.state.lspLatencyMs.toFixed(1) + " ms").padEnd(14)} | Mutation Survival Ratio: ` +
          `${(this.state.mutationSurvivalRatio * 100).toFixed(1)}%`
      ),
      padLine(
        `   Ouroboros   : ${this.state.ouroborosStatus.padEnd(20)} | Self-Mod Generation: v1.0.${this.state.ouroborosGeneration}`
      ),
      `╠${rule}╣`,
      padLine(" ACTIVE ARCHITECTURAL SEARCH TREE"),
      ...branchLines,
      `╠${rule}╣`,
      padLine(" EVENT STREAM"),
      ...logLines,
      `╚${rule}╝\n`
    ].join("\n");

    process.stdout.write(screen);
  }
}

export const logger = new Logger(process.env.FOUNDRY_DASHBOARD !== "false");

/**
 * Errors that must never be retried: retrying them either cannot succeed
 * (bad credentials, malformed request) or would spend more money (budget trips).
 */
export class NonRetryableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "NonRetryableError";
  }
}

export interface BackoffOptions {
  maxAttempts?: number;
  baseDelayMs?: number;
  isRetryable?: (err: unknown) => boolean;
}

function defaultIsRetryable(err: unknown): boolean {
  if (err instanceof NonRetryableError) return false;
  if (err instanceof Error && err.name === "BudgetExceededError") return false;
  return true;
}

function retryAfterMs(err: unknown): number | null {
  const value = (err as { retryAfterMs?: unknown } | null)?.retryAfterMs;
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

export async function withExponentialBackoff<T>(
  operationName: string,
  fn: () => Promise<T>,
  options: BackoffOptions = {}
): Promise<T> {
  const maxAttempts = Math.max(1, options.maxAttempts ?? 4);
  const baseDelayMs = options.baseDelayMs ?? 400;
  const isRetryable = options.isRetryable ?? defaultIsRetryable;
  let attempt = 0;
  let lastError: unknown;

  while (attempt < maxAttempts) {
    try {
      return await fn();
    } catch (err) {
      attempt += 1;
      lastError = err;
      if (attempt >= maxAttempts || !isRetryable(err)) {
        break;
      }
      const jitter = Math.floor(Math.random() * 150);
      const waitMs = retryAfterMs(err) ?? baseDelayMs * Math.pow(2, attempt - 1) + jitter;
      logger.warn(
        "Backoff",
        `${operationName} failed (attempt ${attempt}/${maxAttempts}): ${
          err instanceof Error ? err.message : String(err)
        }. Retrying in ${waitMs}ms.`
      );
      await new Promise((resolve) => setTimeout(resolve, waitMs));
    }
  }

  if (lastError instanceof Error && !isRetryable(lastError)) {
    throw lastError;
  }
  throw new Error(
    `${operationName} failed after ${attempt} attempt(s). Last error: ${
      lastError instanceof Error ? lastError.message : String(lastError)
    }`
  );
}
