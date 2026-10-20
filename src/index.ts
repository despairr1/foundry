#!/usr/bin/env node
import { Command, InvalidArgumentError } from "commander";
import * as fs from "fs";
import * as path from "path";
import { FoundryConfig, loadConfig, missingCredentials, parseThinkingLevel, ThinkingLevel } from "./config";
import { ConvergenceEngine, ConvergenceRunReport, workspaceHasCode } from "./engine/convergence";
import { JournalEntry, ProjectJournal } from "./engine/journal";
import { emitProgress, enableProgressEvents } from "./utils/progress";
import { rebuildFoundry } from "./singularity/ouroboros";
import { checkHealthOnly, recoverInterruptedRound, runSelfImprovement, SelfImproveSummary, undoLastImprovement } from "./singularity/self_improve";
import { discardPendingCarryovers } from "./singularity/carryover";
import { CHAT_AGENTS, CHAT_MODES, ChatAgent, ChatMessage, ChatMode, replyToChat } from "./swarm/chat";
import { AccessBlockedError, assertAiAccess } from "./utils/access";
import { skillsPrompt } from "./skills";
import { EconomicsEngine } from "./engine/economics";
import { logger } from "./utils/logger";

function parseBudget(value: string): number {
  const amount = parseFloat(value);
  if (!Number.isFinite(amount) || amount <= 0) {
    throw new InvalidArgumentError("Budget must be a positive number of USD.");
  }
  return amount;
}

function parseRounds(value: string): number {
  const rounds = Number(value);
  if (!Number.isInteger(rounds) || rounds < 1 || rounds > 10) {
    throw new InvalidArgumentError("Rounds must be a whole number from 1 to 10.");
  }
  return rounds;
}

function parseChatMode(value: string): ChatMode {
  const mode = value.trim().toLowerCase();
  if ((CHAT_MODES as string[]).includes(mode)) return mode as ChatMode;
  throw new InvalidArgumentError("Mode must be project or prompt.");
}

/**
 * Folders the user blocked in the Files pane are off limits to every run. Exit code 3, and a
 * "finished" event whose problem starts with "access-blocked:" so the GUI can say so.
 */
function requireAiAccess(targetDir: string, config: FoundryConfig): void {
  try {
    assertAiAccess(targetDir, config.foundryRootDir);
  } catch (err) {
    if (!(err instanceof AccessBlockedError)) throw err;
    emitProgress({
      type: "finished",
      outcome: "error",
      summary: "AI access to this folder is blocked.",
      filesChanged: [],
      spentUsd: 0,
      limitUsd: config.maxBudgetUsd,
      problem: `access-blocked: ${err.message}`
    });
    logger.error("CLI", err.message);
    process.exit(3);
  }
}

function parseChatAgent(value: string): ChatAgent | "all" {
  const agent = value.trim().toLowerCase();
  if (agent === "all" || (CHAT_AGENTS as string[]).includes(agent)) return agent as ChatAgent | "all";
  throw new InvalidArgumentError("Agent must be claude, gemini, openai or all.");
}

/** Reads {"messages": [{role, agent?, text}]}; the last message must be the user's. */
function readConversation(file: string): ChatMessage[] {
  const raw = JSON.parse(fs.readFileSync(file, "utf8")) as { messages?: unknown };
  if (!Array.isArray(raw.messages)) throw new Error(`${file} has no "messages" list`);
  const messages = raw.messages
    .filter((m): m is Record<string, unknown> => typeof m === "object" && m !== null)
    .filter((m) => (m.role === "user" || m.role === "assistant") && typeof m.text === "string" && m.text.trim() !== "")
    .map((m) => ({
      role: m.role as ChatMessage["role"],
      text: m.text as string,
      ...((CHAT_AGENTS as unknown[]).includes(m.agent) ? { agent: m.agent as ChatAgent } : {})
    }));
  if (messages.length === 0 || messages[messages.length - 1].role !== "user") {
    throw new Error("The conversation must end with a message from the user.");
  }
  return messages;
}

/** What the chat models should know about the folder: new or existing, and what earlier runs did. */
function describeProject(targetDir: string): string {
  const state = workspaceHasCode(targetDir)
    ? `An existing project with code, in ${targetDir}.`
    : `A new project; its folder (${targetDir}) has no code yet.`;
  return [state, ProjectJournal.load(targetDir).formatForPrompt()].filter(Boolean).join("\n");
}

const THINKING_HELP = "How hard the models think: low, medium, high, xhigh or max (default: FOUNDRY_THINKING, else each model's own)";

function parseThinking(value: string): ThinkingLevel {
  const level = parseThinkingLevel(value);
  if (!level) throw new InvalidArgumentError("Thinking must be low, medium, high, xhigh or max.");
  return level;
}

interface RunOptions {
  budgetUsd: number;
  moduleBudgetUsd?: number;
  offline: boolean;
  thinking?: ThinkingLevel;
}

function loadRunConfig(options: RunOptions): FoundryConfig {
  const config = loadConfig({
    maxBudgetUsd: options.budgetUsd,
    ...(options.moduleBudgetUsd !== undefined ? { maxModuleBudgetUsd: options.moduleBudgetUsd } : {}),
    ...(options.offline ? { offlineMode: true } : {}),
    ...(options.thinking ? { thinkingLevel: options.thinking } : {})
  });
  const missing = missingCredentials(config);
  if (missing.length > 0) {
    const message = `Missing credentials: ${missing.join(", ")}. Set them in .env, or pass --offline for a no-API demo run.`;
    emitProgress({
      type: "finished",
      outcome: "error",
      summary: "API keys are missing.",
      filesChanged: [],
      spentUsd: 0,
      limitUsd: config.maxBudgetUsd,
      problem: `missing-keys: ${missing.join(", ")}`
    });
    logger.error("CLI", message);
    process.exit(1);
  }
  if (config.offlineMode) {
    logger.warn("CLI", "Offline mode: models are replaced by deterministic stand-ins; output is a demo, not a real build.");
  }
  return config;
}

/** One line per module; skipped for single-module plans, where the summary line already says everything. */
function logModuleResults(report: ConvergenceRunReport): void {
  if (report.modules.length < 2) return;
  report.modules.forEach((m, idx) => {
    const label = `Module ${idx + 1}/${report.modules.length} ${m.moduleName}`;
    if (m.status === "converged") {
      logger.info(
        "CLI",
        `${label} converged on Branch ${m.winningBranch} in ${m.iterations} iteration(s). Commit: ${m.commitHash?.slice(0, 8) ?? "none"} | ` +
          `Files: ${m.modifiedFiles.join(", ") || "none"} | Cost: $${m.spentUsd.toFixed(4)}`
      );
    } else if (m.status === "failed") {
      logger.error("CLI", `${label} did not converge after ${m.iterations} iteration(s). Cost: $${m.spentUsd.toFixed(4)}`);
    } else {
      logger.warn("CLI", `${label} skipped: an earlier module did not converge.`);
    }
  });
}

/** "on Branch Alpha" for one module; "on all 2 modules (core via Alpha, api via Beta)" for a plan. */
function describeWinners(report: ConvergenceRunReport): string {
  if (report.modules.length < 2) return `on Branch ${report.winningBranch}`;
  return `on all ${report.modules.length} modules (${report.modules.map((m) => `${m.moduleName} via ${m.winningBranch}`).join(", ")})`;
}

function reportFailure(kind: string, report: ConvergenceRunReport): never {
  logModuleResults(report);
  logger.error(
    "CLI",
    `${kind} did not converge. Total spend: $${report.totalSpentUsd.toFixed(4)}${report.error ? `. Last failure: ${report.error.slice(0, 600)}` : ""}`
  );
  process.exit(2);
}

/** Recompiles Foundry and confirms the fresh build loads, without re-running the previous command. */
async function rebuildAndVerify(config: FoundryConfig): Promise<void> {
  logger.info("CLI", "Recompiling Foundry kernel...");
  await rebuildFoundry(config.foundryRootDir);
  const { execFileSync } = await import("child_process");
  const version = execFileSync(process.execPath, [path.join(config.foundryRootDir, "dist", "index.js"), "--version"], {
    encoding: "utf8",
    windowsHide: true
  }).trim();
  logger.info("CLI", `Rebuilt kernel loads cleanly (v${version}). New code takes effect on the next run.`);
}

export function createCliProgram(): Command {
  const program = new Command();

  program
    .name("foundry")
    .description(
      "Universal autonomous software engineering engine with tri-model consensus, dynamic LSP multiplexing, semantic AST patching, and Ouroboros self-modification."
    )
    .version("1.0.0");

  program
    .command("build")
    .description("Autonomously architect, test, synthesize, and verify a project from a specification")
    .requiredOption("--spec <path.md>", "Path to the markdown specification file")
    .requiredOption("--target <dir>", "Target project directory")
    .option("--budget-usd <amount>", "Maximum USD token budget before circuit breaker freeze", parseBudget, 5.0)
    .option("--module-budget-usd <amount>", "Maximum USD spend per planned module (default: no cap beyond --budget-usd)", parseBudget)
    .option("--offline", "Run with deterministic stand-ins instead of model APIs (demo/testing)", false)
    .option("--thinking <level>", THINKING_HELP, parseThinking)
    .action(async (options: { spec: string; target: string } & RunOptions) => {
      const specPath = path.resolve(options.spec);
      if (!fs.existsSync(specPath)) {
        logger.error("CLI", `Specification file not found at ${specPath}`);
        process.exit(1);
      }

      const config = loadRunConfig(options);
      const specContent = fs.readFileSync(specPath, "utf8");
      requireAiAccess(path.resolve(options.target), config);
      const engine = new ConvergenceEngine(config);
      engine.setSkills(skillsPrompt(config.foundryRootDir));
      const report = await engine.executeBuild(specContent, options.target);

      if (!report.converged) reportFailure("Build", report);

      logModuleResults(report);
      logger.info(
        "CLI",
        `Build converged ${describeWinners(report)}. Modified files: ${report.modifiedFiles.join(", ") || "none"} | ` +
          `Total Cost: $${report.totalSpentUsd.toFixed(4)}`
      );
    });

  program
    .command("patch")
    .description("Autonomously diagnose and patch an existing repository issue")
    .requiredOption("--repo <dir>", "Path to target repository")
    .requiredOption("--issue <description>", "Issue description or stack trace to resolve")
    .option("--budget-usd <amount>", "Maximum USD token budget before circuit breaker freeze", parseBudget, 5.0)
    .option("--module-budget-usd <amount>", "Maximum USD spend per planned module (default: no cap beyond --budget-usd)", parseBudget)
    .option("--offline", "Run with deterministic stand-ins instead of model APIs (demo/testing)", false)
    .option("--thinking <level>", THINKING_HELP, parseThinking)
    .option("--auto-restart", "Recompile Foundry after the patch converges (implied when patching Foundry itself)", false)
    .action(
      async (options: { repo: string; issue: string; autoRestart: boolean } & RunOptions) => {
        const config = loadRunConfig(options);
        requireAiAccess(path.resolve(options.repo), config);
        const engine = new ConvergenceEngine(config);
        engine.setSkills(skillsPrompt(config.foundryRootDir));
        const report = await engine.executePatch(options.issue, options.repo);

        if (!report.converged) reportFailure("Patch cycle", report);

        logModuleResults(report);
        logger.info(
          "CLI",
          `Patch converged ${describeWinners(report)}. Commit: ${report.committedHashes.join(", ") || "none"} | ` +
            `Total Cost: $${report.totalSpentUsd.toFixed(4)}`
        );

        const isSelfRepo = path.resolve(options.repo) === path.resolve(config.foundryRootDir);
        if (options.autoRestart || (isSelfRepo && config.ouroborosAutoRestart)) {
          try {
            await rebuildAndVerify(config);
          } catch (e) {
            logger.error("CLI", `Recompilation after self-patch failed: ${e instanceof Error ? e.message : String(e)}`);
            process.exit(1);
          }
        }
      }
    );

  program
    .command("go")
    .description(
      "One-step mode: describe what you want in plain English. An empty folder gets a new project; a folder with code gets improved. Each run is remembered in the folder's journal so the next run builds on it."
    )
    .requiredOption("--task <text>", "What to build or change, in plain English")
    .requiredOption("--target <dir>", "Project folder (created if it does not exist)")
    .option("--budget-usd <amount>", "Spending limit for this run in USD", parseBudget, 5.0)
    .option("--offline", "Run with deterministic stand-ins instead of model APIs (demo/testing)", false)
    .option("--thinking <level>", THINKING_HELP, parseThinking)
    .option("--progress", "Print machine-readable @@FOUNDRY progress events (used by the GUI)", false)
    .action(async (options: { task: string; target: string; progress: boolean } & RunOptions) => {
      if (options.progress) {
        enableProgressEvents();
        logger.setInteractive(false);
      }
      const task = options.task.trim();
      if (!task) {
        logger.error("CLI", "Describe what you want Foundry to do (--task).");
        process.exit(1);
      }
      const config = loadRunConfig(options);
      const targetDir = path.resolve(options.target);
      requireAiAccess(targetDir, config);
      const mode: JournalEntry["mode"] = workspaceHasCode(targetDir) ? "improve" : "build";
      const journal = ProjectJournal.load(targetDir);
      const history = journal.formatForPrompt();
      const specification = [
        mode === "build" ? `Build this new project:\n${task}` : `Change this existing project as requested:\n${task}`,
        history
      ]
        .filter(Boolean)
        .join("\n\n");

      emitProgress({ type: "start", mode, task, target: targetDir, limitUsd: config.maxBudgetUsd, historyEntries: journal.getEntries().length });
      logger.info(
        "CLI",
        `${mode === "build" ? "Building a new project" : "Improving the existing project"} in ${targetDir} ` +
          `(spending limit $${config.maxBudgetUsd.toFixed(2)}, ${journal.getEntries().length} earlier run(s) remembered).`
      );

      const engine = new ConvergenceEngine(config, false);
      engine.setSkills(skillsPrompt(config.foundryRootDir));
      let report: ConvergenceRunReport | null = null;
      let outcome: Exclude<JournalEntry["outcome"], "stopped">;
      let problem: string | undefined;
      try {
        report = mode === "build" ? await engine.executeBuild(specification, targetDir) : await engine.executePatch(specification, targetDir);
        outcome = report.converged ? "done" : "not-finished";
        problem = report.error;
      } catch (err) {
        const budgetHit = err instanceof Error && err.name === "BudgetExceededError";
        outcome = budgetHit ? "spending-limit" : "error";
        problem = err instanceof Error ? err.message : String(err);
      }

      // Modules that converged before a later failure are already written and committed, so they are kept.
      const filesChanged = report?.modifiedFiles ?? [];
      const spentUsd = report?.totalSpentUsd ?? engine.getTotalSpentUsd();
      const doneModules = (report?.modules ?? []).filter((m) => m.status === "converged").length;
      const totalModules = report?.modules.length ?? 0;
      const summary =
        outcome === "done"
          ? `Finished: ${totalModules} part(s), ${filesChanged.length} file(s) changed.`
          : outcome === "spending-limit"
            ? `Stopped at the $${config.maxBudgetUsd.toFixed(2)} spending limit after ${doneModules}/${totalModules || "?"} part(s).`
            : outcome === "not-finished"
              ? `Not finished: ${doneModules}/${totalModules} part(s) done.`
              : `Stopped by an error.`;

      try {
        journal.append({
          timestamp: new Date().toISOString(),
          task,
          mode,
          outcome,
          summary,
          filesChanged,
          modules: (report?.modules ?? []).map((m) => ({ name: m.moduleName, status: m.status })),
          spentUsd,
          limitUsd: config.maxBudgetUsd,
          commits: report?.committedHashes ?? [],
          problem: problem?.slice(0, 2000)
        });
      } catch (err) {
        logger.warn("CLI", `Could not update the project journal: ${err instanceof Error ? err.message : String(err)}`);
      }

      emitProgress({ type: "finished", outcome, summary, filesChanged, spentUsd, limitUsd: config.maxBudgetUsd, problem: problem?.slice(0, 600) });
      if (report) logModuleResults(report);
      const line = `${summary} Total cost: $${spentUsd.toFixed(4)}${problem && outcome !== "done" ? `. Problem: ${problem.slice(0, 600)}` : ""}`;
      if (outcome === "done") logger.info("CLI", line);
      else logger.error("CLI", line);
      process.exit(outcome === "done" ? 0 : outcome === "spending-limit" ? 42 : 2);
    });

  program
    .command("self-improve")
    .description(
      "Let Foundry improve its own code (the harness). It studies its logs, failures and test results, patches src/, keeps the change only if the type-check, the full test suite and the build pass, rebuilds itself, and remembers every attempt."
    )
    .option("--focus <text>", "Optional: what to improve, in plain English")
    .option("--budget-usd <amount>", "Spending limit for the whole session in USD", parseBudget, 5.0)
    .option("--rounds <n>", "Improvements to attempt in a row (stops early after two failed rounds)", parseRounds, 1)
    .option("--undo", "Roll back the most recent kept self-improvement", false)
    .option("--recover", "Roll back a round that was interrupted (Stop button, crash) and exit", false)
    .option("--discard-carryover", "Forget saved partial work from unfinished rounds, so the next round starts fresh", false)
    .option("--check", "Only check Foundry's health (type-check, tests, recent log problems) and write .foundry/health.json; no model is called", false)
    .option("--offline", "Run with deterministic stand-ins instead of model APIs (demo/testing)", false)
    .option("--thinking <level>", THINKING_HELP, parseThinking)
    .option("--progress", "Print machine-readable @@FOUNDRY progress events (used by the GUI)", false)
    .action(async (options: { focus?: string; rounds: number; undo: boolean; recover: boolean; discardCarryover: boolean; check: boolean; progress: boolean } & RunOptions) => {
      if (options.progress) {
        enableProgressEvents();
        logger.setInteractive(false);
      }

      if (options.check) {
        const report = await checkHealthOnly(loadConfig().foundryRootDir);
        const ok = report.typecheck.ok && report.tests.ok;
        const summary = ok
          ? `Type-check and tests pass; ${report.problems.length} recent warning(s)/error(s) in the log.`
          : `${!report.typecheck.ok ? "The type-check fails" : "The test suite fails"}: ${(report.typecheck.ok ? report.tests : report.typecheck).detail || "see the log"}`;
        emitProgress({ type: "finished", outcome: ok ? "done" : "not-finished", summary, filesChanged: [], spentUsd: 0, limitUsd: 0 });
        if (ok) logger.info("CLI", summary);
        else logger.warn("CLI", summary);
        process.exit(0);
      }

      if (options.discardCarryover) {
        const count = discardPendingCarryovers(loadConfig().foundryRootDir);
        const summary = count > 0 ? `Discarded ${count} unfinished round(s); the next round starts fresh.` : "There was no unfinished round to discard.";
        emitProgress({ type: "finished", outcome: "done", summary, filesChanged: [], spentUsd: 0, limitUsd: 0 });
        logger.info("CLI", summary);
        process.exit(0);
      }

      if (options.recover) {
        const restored = await recoverInterruptedRound(loadConfig().foundryRootDir);
        const summary = restored?.length
          ? `The interrupted round was rolled back (${restored.length} file(s) restored).`
          : "Nothing needed rolling back.";
        emitProgress({ type: "finished", outcome: "done", summary, filesChanged: restored ?? [], spentUsd: 0, limitUsd: 0 });
        logger.info("CLI", summary);
        process.exit(0);
      }

      if (options.undo) {
        const config = loadConfig();
        try {
          const undone = await undoLastImprovement(config);
          emitProgress({ type: "finished", outcome: "done", summary: undone.summary, filesChanged: undone.filesChanged, spentUsd: 0, limitUsd: 0 });
          process.exit(0);
        } catch (e) {
          const message = e instanceof Error ? e.message : String(e);
          emitProgress({ type: "finished", outcome: "error", summary: "Nothing was undone.", filesChanged: [], spentUsd: 0, limitUsd: 0, problem: message });
          logger.error("CLI", message);
          process.exit(1);
        }
      }

      const config = loadRunConfig(options);
      let result: SelfImproveSummary;
      try {
        result = await runSelfImprovement(config, { focus: options.focus, budgetUsd: config.maxBudgetUsd, rounds: options.rounds });
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        emitProgress({ type: "finished", outcome: "error", summary: "Self-improvement stopped by an error.", filesChanged: [], spentUsd: 0, limitUsd: config.maxBudgetUsd, problem: message });
        logger.error("CLI", message);
        process.exit(1);
      }
      emitProgress({
        type: "finished",
        outcome: result.outcome,
        summary: result.summary,
        filesChanged: result.filesChanged,
        spentUsd: result.spentUsd,
        limitUsd: config.maxBudgetUsd,
        problem: result.problem?.slice(0, 600)
      });
      const line = `${result.summary} Total cost: $${result.spentUsd.toFixed(4)}${result.problem ? `. Last problem: ${result.problem.slice(0, 600)}` : ""}`;
      if (result.kept > 0) logger.info("CLI", line);
      else logger.error("CLI", line);
      process.exit(result.outcome === "done" ? 0 : result.outcome === "spending-limit" ? 42 : 2);
    });

  program
    .command("chat")
    .description(
      "Talk with the models about a project before building it. Reads the conversation from a JSON file and prints the replies."
    )
    .requiredOption(
      "--conversation <file.json>",
      'Conversation file: {"messages": [{"role": "user" | "assistant", "agent"?: "claude" | "gemini" | "openai", "text": "..."}]}'
    )
    .option("--agent <name>", "Who replies: claude, gemini, openai, or all (one after another)", parseChatAgent, "claude")
    .option("--mode <mode>", "project (the default: talk about a folder), prompt (the prompt studio: write and refine a prompt) or job (the studio's planner: split a job into Build requests)", parseChatMode, "project")
    .option("--target <dir>", "Project folder, so the models know what is there and what earlier runs did")
    .option("--budget-usd <amount>", "Spending limit for this reply in USD", parseBudget, 1.0)
    .option("--offline", "Reply with deterministic stand-ins instead of model APIs (demo/testing)", false)
    .option("--thinking <level>", THINKING_HELP, parseThinking)
    .option("--progress", "Print machine-readable @@FOUNDRY progress events (used by the GUI)", false)
    .action(
      async (options: { conversation: string; agent: ChatAgent | "all"; mode: ChatMode; target?: string; progress: boolean } & RunOptions) => {
        if (options.progress) {
          enableProgressEvents();
          logger.setInteractive(false);
        }
        const config = loadConfig({
          maxBudgetUsd: options.budgetUsd,
          ...(options.offline ? { offlineMode: true } : {}),
          ...(options.thinking ? { thinkingLevel: options.thinking } : {})
        });
        const finish = (outcome: "done" | "error" | "spending-limit", summary: string, spentUsd: number, problem?: string): never => {
          emitProgress({ type: "finished", outcome, summary, filesChanged: [], spentUsd, limitUsd: config.maxBudgetUsd, problem });
          if (outcome === "done") logger.info("CLI", summary);
          else logger.error("CLI", `${summary}${problem ? ` ${problem}` : ""}`);
          process.exit(outcome === "done" ? 0 : outcome === "spending-limit" ? 42 : 1);
        };

        let messages: ChatMessage[];
        try {
          messages = readConversation(path.resolve(options.conversation));
        } catch (err) {
          return finish("error", "Could not read the conversation.", 0, err instanceof Error ? err.message : String(err));
        }
        if (options.target) {
          try {
            assertAiAccess(path.resolve(options.target), config.foundryRootDir);
          } catch (err) {
            if (!(err instanceof AccessBlockedError)) throw err;
            return finish("error", "AI access to this folder is blocked.", 0, `access-blocked: ${err.message}`);
          }
        }
        const context = options.target && options.mode === "project" ? describeProject(path.resolve(options.target)) : "";
        const skills = skillsPrompt(config.foundryRootDir);
        const economics = new EconomicsEngine(config.maxBudgetUsd, config.freezeStatePath, false);
        const agents = options.agent === "all" ? CHAT_AGENTS : [options.agent];
        let replies = 0;
        let lastProblem: string | undefined;
        let budgetHit = false;

        // With "all", each model sees the replies the others gave earlier in the same turn.
        for (const agent of agents) {
          emitProgress({ type: "chat-thinking", agent });
          const before = economics.getTotalSpentUsd();
          const tokensBefore = economics.getTokenTotals();
          let attempt = 1;
          const stream = {
            onAttempt: (n: number) => {
              attempt = n;
            },
            onText: (text: string) => emitProgress({ type: "chat-delta", agent, text, attempt })
          };
          try {
            const reply = await replyToChat(config, economics, agent, messages, context, stream, options.mode, skills);
            const spentUsd = economics.getTotalSpentUsd() - before;
            const tokens = economics.getTokenTotals();
            emitProgress({
              type: "chat-reply",
              agent,
              text: reply.text,
              model: reply.model,
              spentUsd,
              inputTokens: tokens.inputTokens - tokensBefore.inputTokens,
              outputTokens: tokens.outputTokens - tokensBefore.outputTokens
            });
            logger.info("CLI", `Chat reply from ${agent} (${reply.model}, ${reply.text.length} chars, $${spentUsd.toFixed(4)}).`);
            messages = [...messages, { role: "assistant", agent, text: reply.text }];
            replies++;
          } catch (err) {
            lastProblem = err instanceof Error ? err.message : String(err);
            budgetHit = err instanceof Error && err.name === "BudgetExceededError";
            emitProgress({ type: "chat-error", agent, problem: lastProblem.slice(0, 600) });
            if (budgetHit) break;
          }
        }

        const spent = economics.getTotalSpentUsd();
        if (budgetHit) return finish("spending-limit", `Stopped at the $${config.maxBudgetUsd.toFixed(2)} chat limit.`, spent, lastProblem);
        if (replies === 0) return finish("error", "No reply.", spent, lastProblem);
        return finish("done", `${replies} repl${replies === 1 ? "y" : "ies"}, $${spent.toFixed(4)}.`, spent, lastProblem);
      }
    );

  program
    .command("restart")
    .description("Recompile Foundry and verify that the new build loads")
    .action(async () => {
      const config = loadConfig();
      try {
        await rebuildAndVerify(config);
      } catch (e) {
        logger.error("CLI", `Recompilation failed: ${e instanceof Error ? e.message : String(e)}`);
        process.exit(1);
      }
    });

  return program;
}

if (require.main === module) {
  const cli = createCliProgram();
  cli.parseAsync(process.argv).catch((err) => {
    logger.error("Fatal", err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
