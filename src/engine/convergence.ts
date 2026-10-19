import * as fs from "fs";
import * as path from "path";
import { FoundryConfig } from "../config";
import { EconomicsEngine } from "./economics";
import { ASTParser, SupportedLanguage } from "../ast/parser";
import { SemanticPatch, SemanticPatcher } from "../ast/semantic_patcher";
import { CallGraphBuilder } from "../ast/call_graph";
import { LSPMultiplexer } from "../lsp/multiplexer";
import { DiagnosticParser } from "../lsp/diagnostic_parser";
import { VectorDB } from "../memory/db";
import { EmbeddingService } from "../memory/embeddings";
import { MemoryRetriever } from "../memory/retriever";
import { ClaudeArchitect, ModuleArchitectureContract, ModuleDependency } from "../swarm/claude_architect";
import { GeminiAuditor } from "../swarm/gemini_auditor";
import { AstraSynthesizer } from "../swarm/astra_synthesizer";
import { harnessRunsWholeSuite } from "../swarm/harness";
import { SandboxRunner } from "../sandbox/runner";
import { MutationEngine } from "./mutation";
import { TreeSearchEngine, BranchId, ArchitecturalBranchNode } from "./tree_search";
import { OuroborosMonitor } from "../singularity/ouroboros";
import { GitManager } from "../utils/git";
import { logger } from "../utils/logger";
import { resolveInsideRoot, toPosixRelative } from "../utils/paths";
import { accessFor, accessRulesPath, AccessRule, loadAccessRules } from "../utils/access";
import { emitProgress } from "../utils/progress";

export type ModuleConvergenceStatus = "converged" | "failed" | "skipped";

export interface ModuleConvergenceResult {
  moduleName: string;
  targetFilePath: string;
  testFilePath: string;
  dependsOn: string[];
  /** "skipped" modules come after a module that failed; they were never attempted. */
  status: ModuleConvergenceStatus;
  /** The winning branch; for a failed module, the best-scoring branch (null when no branch got a contract). */
  winningBranch: BranchId | null;
  /** Convergence iterations used, at most maxConvergenceIterations. */
  iterations: number;
  spentUsd: number;
  commitHash: string | null;
  episodeId: number | null;
  /** Files this module created or changed relative to the workspace the previous module left behind. */
  modifiedFiles: string[];
  error?: string;
}

export interface ConvergenceRunReport {
  /** True only when every planned module converged. */
  converged: boolean;
  /** Winning branch of the last module attempted (its best-scoring branch if it failed). */
  winningBranch: BranchId | null;
  totalSpentUsd: number;
  /** One commit per converged module that changed files, in build order. */
  committedHashes: string[];
  modifiedFiles: string[];
  /** Every planned module in build order; empty when the architect produced no plan. */
  modules: ModuleConvergenceResult[];
  error?: string;
}

/** What one module's branch iterations need to know about the rest of the plan. */
export interface PartialProgress {
  /** Module names of the run's plan, in order. */
  plan: string[];
  moduleName: string;
  contract: ModuleArchitectureContract;
  testFilePath: string | null;
  testCode: string | null;
  /** The best branch's full patched workspace (baseline plus its edits), or null when no branch got that far. */
  workspace: Map<string, string> | null;
  lastFeedback: string | null;
  fitness: number;
}

interface ModuleRunContext {
  /** "2/3": this module's place in the plan, for logs and the dashboard. */
  position: string;
  /** Contracts of earlier modules this module imports, as their winning branches designed them. */
  dependencies: ModuleArchitectureContract[];
  /** Every module converged earlier in this run; their tests must keep passing and stay untouched. */
  earlierModules: ModuleArchitectureContract[];
  episodicContext: string;
}

interface ModuleSearchOutcome {
  winner: ArchitecturalBranchNode | null;
  bestBranch: BranchId | null;
  iterations: number;
  error?: string;
}

const IGNORED_DIRS = new Set([
  ".git",
  "node_modules",
  "dist",
  "build",
  "out",
  "target",
  "coverage",
  "__pycache__",
  ".venv",
  "venv",
  ".next",
  "vendor"
]);
const CODE_FILE = /\.(ts|tsx|mts|cts|js|jsx|mjs|cjs|rs|py|go|c|cc|cpp|cxx|h|hh|hpp)$/i;
const MANIFEST_FILES = new Set([
  "package.json",
  "tsconfig.json",
  "Cargo.toml",
  "go.mod",
  "go.sum",
  "requirements.txt",
  "pyproject.toml",
  "setup.cfg",
  "CMakeLists.txt",
  "Makefile"
]);
const MAX_WORKSPACE_FILE_BYTES = 256 * 1024;
const MAX_WORKSPACE_FILES = 2000;

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function isBudgetError(err: unknown): boolean {
  return err instanceof Error && err.name === "BudgetExceededError";
}

/** True when the folder already contains source code (so a task means "change it", not "create it"). */
export function workspaceHasCode(rootDir: string): boolean {
  if (!fs.existsSync(rootDir)) return false;
  const stack = [rootDir];
  while (stack.length > 0) {
    const dir = stack.pop()!;
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (IGNORED_DIRS.has(entry.name) || entry.name.startsWith(".foundry")) continue;
      if (entry.isDirectory()) stack.push(path.join(dir, entry.name));
      else if (entry.isFile() && CODE_FILE.test(entry.name)) return true;
    }
  }
  return false;
}

export class ConvergenceEngine {
  private config: FoundryConfig;
  private economics: EconomicsEngine;
  private astParser: ASTParser;
  private patcher: SemanticPatcher;
  private callGraph: CallGraphBuilder;
  private vectorDb: VectorDB;
  private retriever: MemoryRetriever;
  private architect: ClaudeArchitect;
  private auditor: GeminiAuditor;
  private synthesizer: AstraSynthesizer;
  private sandbox: SandboxRunner;
  private mutationEngine: MutationEngine;
  private treeSearch: TreeSearchEngine;
  private ouroboros: OuroborosMonitor;
  private diagParser: DiagnosticParser;
  /** Folders the user blocked AI access to (Files pane): never read, never changed. */
  private accessRules: AccessRule[];
  /** Enabled skills (src/skills.ts), given to the models with the specification but kept out of language inference. */
  private skills = "";
  private currentPlan: string[] = [];
  private currentModule: ModuleArchitectureContract | null = null;

  constructor(config: FoundryConfig, exitOnBudgetFreeze = true) {
    this.config = config;
    this.economics = new EconomicsEngine(config.maxBudgetUsd, config.freezeStatePath, exitOnBudgetFreeze);
    this.astParser = new ASTParser();
    this.patcher = new SemanticPatcher(this.astParser);
    this.callGraph = new CallGraphBuilder(this.astParser);
    this.vectorDb = new VectorDB(config.sqliteDbPath);
    const embeddingService = new EmbeddingService(config, this.economics);
    this.retriever = new MemoryRetriever(this.vectorDb, embeddingService);
    this.architect = new ClaudeArchitect(config, this.economics);
    this.auditor = new GeminiAuditor(config, this.economics);
    this.synthesizer = new AstraSynthesizer(config, this.economics);
    this.sandbox = new SandboxRunner(config.sandboxImageName, {
      timeoutMs: config.sandboxTimeoutMs,
      dockerMemoryMb: config.dockerMemoryMb,
      dockerCpus: config.dockerCpus
    });
    this.mutationEngine = new MutationEngine(this.astParser, this.sandbox, config.minMutationKillRatio);
    this.treeSearch = new TreeSearchEngine(config.minMutationKillRatio);
    this.ouroboros = new OuroborosMonitor(config, this.astParser, this.patcher, this.economics);
    this.diagParser = new DiagnosticParser();
    this.accessRules = loadAccessRules(accessRulesPath(config.foundryRootDir));
  }

  public getTotalSpentUsd(): number {
    return this.economics.getTotalSpentUsd();
  }

  public setSkills(text: string): void {
    this.skills = text.trim();
  }

  /**
   * The best attempt at the module that was in progress when the run ended, including
   * runs cut short by the budget breaker. Callers use it to carry unfinished work forward.
   */
  public getPartialProgress(): PartialProgress | null {
    if (!this.currentModule) return null;
    const best = this.treeSearch.getBestBranch();
    return {
      plan: [...this.currentPlan],
      moduleName: this.currentModule.moduleName,
      contract: best?.contract ?? this.currentModule,
      testFilePath: best?.testSuite?.testFilePath ?? null,
      testCode: best?.testSuite?.testCode ?? null,
      workspace: best ? new Map(best.workspaceSnapshot) : null,
      lastFeedback: best?.failureHistory[best.failureHistory.length - 1] ?? null,
      fitness: best?.fitnessScore ?? 0
    };
  }

  public async executeBuild(specContent: string, targetDir: string): Promise<ConvergenceRunReport> {
    logger.updateState({ mode: "BUILD", targetDir });
    return await this.runConvergenceLoop(specContent, targetDir);
  }

  public async executePatch(issueDescription: string, repoDir: string): Promise<ConvergenceRunReport> {
    logger.updateState({ mode: "PATCH", targetDir: repoDir });
    return await this.runConvergenceLoop(issueDescription, repoDir);
  }

  private failureReport(error: string): ConvergenceRunReport {
    logger.error("Convergence", error);
    return {
      converged: false,
      winningBranch: null,
      totalSpentUsd: this.economics.getTotalSpentUsd(),
      committedHashes: [],
      modifiedFiles: [],
      modules: [],
      error
    };
  }

  /**
   * Plans the work as an ordered list of modules, then converges them one at a
   * time: each converged module is written, committed and indexed into memory,
   * and its winning workspace becomes the baseline the next module builds on.
   * Stops at the first module that does not converge.
   */
  private async runConvergenceLoop(specification: string, targetDir: string): Promise<ConvergenceRunReport> {
    const absTargetDir = path.resolve(targetDir);
    const git = new GitManager(absTargetDir);
    // With AUTO_GIT_COMMIT off, leave the folder alone: no surprise `git init`.
    if (this.config.autoGitCommit) {
      await git.ensureInitialized();
    } else {
      fs.mkdirSync(absTargetDir, { recursive: true });
    }
    this.sandbox.setDependencyRoot(absTargetDir);

    const lspMultiplexer = new LSPMultiplexer(absTargetDir, this.astParser, this.config.lspDiagnosticTimeoutMs);

    try {
      const initialFiles = this.loadWorkspaceFiles(absTargetDir);
      const initialCodeFiles = this.codeFilesOf(initialFiles);
      const primaryLanguage = this.inferProjectLanguage(specification, initialCodeFiles);
      // The models see the skills with the spec; the language above comes from the user's words alone.
      const promptSpec = this.skills ? `${specification}\n\n${this.skills}` : specification;

      this.economics.setExecutionContext({
        targetDir: absTargetDir,
        primaryLanguage,
        specificationPreview: specification.slice(0, 300)
      });

      let plan: ModuleArchitectureContract[];
      emitProgress({ type: "phase", phase: "planning" });
      try {
        plan = await this.architect.planModules({
          specification: promptSpec,
          language: primaryLanguage,
          callGraphSummary: this.callGraph.summarizeGraphForContext(initialCodeFiles),
          episodicMemoryContext: await this.retriever.retrieveRelevantFixes(specification, primaryLanguage),
          existingFiles: initialFiles
        });
      } catch (err) {
        if (isBudgetError(err)) throw err;
        return this.failureReport(`The architect did not produce a valid module plan: ${errorMessage(err)}`);
      }
      logger.info(
        "Convergence",
        `Module plan: ${plan
          .map((m, idx) => `${idx + 1}. ${m.moduleName} (${m.targetFilePath}${m.dependsOn.length > 0 ? `, imports ${m.dependsOn.join(", ")}` : ""})`)
          .join(" | ")}`
      );
      this.economics.setExecutionContext({ modulePlan: plan.map((m) => m.moduleName) });
      this.currentPlan = plan.map((m) => m.moduleName);
      emitProgress({ type: "plan", modules: plan.map((m) => m.moduleName) });

      const moduleBudgetUsd = this.config.maxModuleBudgetUsd ?? this.config.maxBudgetUsd;
      const results: ModuleConvergenceResult[] = [];
      const convergedContracts: ModuleArchitectureContract[] = [];
      let baseline = initialFiles;
      let runBranchCheckedOut = false;

      for (let index = 0; index < plan.length; index += 1) {
        const planned = plan[index];
        const position = `${index + 1}/${plan.length}`;
        const spentBeforeUsd = this.economics.getTotalSpentUsd();
        this.economics.beginModuleBudget(planned.moduleName, moduleBudgetUsd);
        this.economics.setExecutionContext({
          moduleIndex: index + 1,
          moduleName: planned.moduleName,
          convergedModules: convergedContracts.map((m) => m.moduleName)
        });
        logger.info("Convergence", `Module ${position}: ${planned.moduleName} -> ${planned.targetFilePath}`);
        const moduleRef = { module: planned.moduleName, moduleIndex: index + 1, moduleCount: plan.length };
        emitProgress({ type: "phase", phase: "designing", ...moduleRef });

        const search = await this.convergeModule({
          specification: promptSpec,
          plan,
          index,
          language: primaryLanguage,
          baseline,
          earlierModules: convergedContracts,
          lspMultiplexer
        });
        const identity = {
          moduleName: planned.moduleName,
          targetFilePath: planned.targetFilePath,
          testFilePath: planned.testFilePath,
          dependsOn: planned.dependsOn,
          iterations: search.iterations
        };

        const winner = search.winner;
        if (!winner) {
          const error = search.error ?? `No branch converged within ${search.iterations} iteration(s)`;
          logger.warn("Convergence", `Module ${position} ${planned.moduleName} did not converge: ${error.slice(0, 300)}`);
          emitProgress({ type: "module-done", ...moduleRef, success: false });
          results.push({
            ...identity,
            status: "failed",
            winningBranch: search.bestBranch,
            spentUsd: this.economics.getTotalSpentUsd() - spentBeforeUsd,
            commitHash: null,
            episodeId: null,
            modifiedFiles: [],
            error
          });
          break;
        }

        const modifiedFiles = this.writeWinningWorkspace(absTargetDir, baseline, winner);
        let commitHash: string | null = null;
        if (this.config.autoGitCommit && modifiedFiles.length > 0) {
          try {
            // One git branch per run, named after the first module's winner; later modules commit on top of it.
            if (!runBranchCheckedOut) {
              await git.createOrCheckoutBranch(`foundry/${winner.branchId.toLowerCase()}`);
              runBranchCheckedOut = true;
            }
            commitHash = await git.stageAndCommit(
              modifiedFiles,
              `feat(${winner.contract.moduleName}): autonomous convergence via Branch ${winner.branchId}`
            );
          } catch (err) {
            logger.warn("Git", `Files were written but the commit failed: ${errorMessage(err)}`);
          }
        }

        const episodeId = await this.retriever.recordWinningEpisode({
          language: winner.contract.language,
          moduleName: winner.contract.moduleName,
          failurePattern: winner.failureHistory[0] ?? "Clean first-pass convergence",
          stackTrace: winner.failureHistory.join("\n"),
          winningPatches: winner.patches
        });

        results.push({
          ...identity,
          status: "converged",
          winningBranch: winner.branchId,
          spentUsd: this.economics.getTotalSpentUsd() - spentBeforeUsd,
          commitHash,
          episodeId,
          modifiedFiles
        });
        logger.info(
          "Convergence",
          `Module ${position} ${planned.moduleName} converged on Branch ${winner.branchId} after ${search.iterations} iteration(s).`
        );
        emitProgress({ type: "module-done", ...moduleRef, success: true });
        convergedContracts.push(winner.contract);
        baseline = winner.workspaceSnapshot;
      }
      this.economics.endModuleBudget();

      for (const skipped of plan.slice(results.length)) {
        results.push({
          moduleName: skipped.moduleName,
          targetFilePath: skipped.targetFilePath,
          testFilePath: skipped.testFilePath,
          dependsOn: skipped.dependsOn,
          status: "skipped",
          winningBranch: null,
          iterations: 0,
          spentUsd: 0,
          commitHash: null,
          episodeId: null,
          modifiedFiles: []
        });
      }
      return this.buildRunReport(results);
    } finally {
      await lspMultiplexer.shutdownAll();
      this.ouroboros.stop();
      this.vectorDb.close();
    }
  }

  /** Runs the three-branch search for one planned module, starting from the workspace earlier modules left. */
  private async convergeModule(params: {
    specification: string;
    plan: ModuleArchitectureContract[];
    index: number;
    language: SupportedLanguage;
    baseline: Map<string, string>;
    earlierModules: ModuleArchitectureContract[];
    lspMultiplexer: LSPMultiplexer;
  }): Promise<ModuleSearchOutcome> {
    const planned = params.plan[params.index];
    const position = `${params.index + 1}/${params.plan.length}`;
    logger.updateState({ activeModule: `${planned.moduleName} (${position}) [design]` });
    // Forget the previous module's branches so getPartialProgress() never reports stale work.
    this.currentModule = planned;
    this.treeSearch.reset();

    // Recomputed per module so the architect sees the code earlier modules added.
    const callGraphSummary = this.callGraph.summarizeGraphForContext(this.codeFilesOf(params.baseline));
    const episodicContext = await this.retriever.retrieveRelevantFixes(
      `${planned.moduleName}: ${planned.contracts.map((c) => c.signature || c.symbolName).join("; ")}\n${params.specification}`,
      params.language
    );

    const branchIds: BranchId[] = ["Alpha", "Beta", "Gamma"];
    const branchContracts = new Map<BranchId, ModuleArchitectureContract>();
    const architectFailures: string[] = [];

    const designs = await Promise.allSettled(
      branchIds.map((branchId) =>
        this.architect.designBranchArchitecture({
          branchId,
          specification: params.specification,
          plan: params.plan,
          moduleIndex: params.index,
          language: params.language,
          callGraphSummary,
          episodicMemoryContext: episodicContext,
          existingFiles: params.baseline
        })
      )
    );
    designs.forEach((result, idx) => {
      if (result.status === "fulfilled") {
        branchContracts.set(branchIds[idx], result.value);
      } else {
        if (isBudgetError(result.reason)) throw result.reason;
        architectFailures.push(`${branchIds[idx]}: ${errorMessage(result.reason)}`);
        logger.warn(
          "Convergence",
          `Architect failed for Branch ${branchIds[idx]} on ${planned.moduleName}: ${errorMessage(result.reason)}`
        );
      }
    });

    if (branchContracts.size === 0) {
      return {
        winner: null,
        bestBranch: null,
        iterations: 0,
        error: `No branch produced a valid architecture contract. ${architectFailures.join(" | ")}`
      };
    }

    const context: ModuleRunContext = {
      position,
      dependencies: planned.dependsOn.flatMap((name) => params.earlierModules.filter((m) => m.moduleName === name)),
      earlierModules: params.earlierModules,
      episodicContext
    };

    this.treeSearch.initializeBranches(branchContracts, params.baseline);
    const moduleRef = { module: planned.moduleName, moduleIndex: params.index + 1, moduleCount: params.plan.length };
    emitProgress({ type: "phase", phase: "testing", ...moduleRef });

    const suites = await Promise.allSettled(
      this.treeSearch
        .getActiveBranches()
        .map((branch) =>
          this.auditor.generateMutationLethalTests(branch.contract, undefined, this.dependencySources(context, params.baseline))
        )
    );
    this.treeSearch.getActiveBranches().forEach((branch, idx) => {
      const result = suites[idx];
      if (result.status === "fulfilled") {
        branch.testSuite = result.value;
        branch.workspaceSnapshot.set(result.value.testFilePath, result.value.testCode);
      } else {
        if (isBudgetError(result.reason)) throw result.reason;
        this.treeSearch.pruneBranch(branch.branchId, `test generation failed: ${errorMessage(result.reason)}`);
      }
    });

    let iterations = 0;
    for (let iteration = 1; iteration <= this.config.maxConvergenceIterations; iteration += 1) {
      await this.ouroboros.checkAndSelfModifyIfNeeded();

      const activeBranches = this.treeSearch.getActiveBranches();
      if (activeBranches.length === 0) break;
      iterations = iteration;
      emitProgress({ type: "phase", phase: "coding", ...moduleRef, attempt: iteration });
      this.economics.setExecutionContext({
        iteration,
        activeBranches: activeBranches.map((b) => b.branchId),
        bestFitness: this.treeSearch.getBestBranch()?.fitnessScore ?? 0
      });

      for (const branch of activeBranches) {
        await this.runBranchIteration(branch, iteration, params.lspMultiplexer, context);
        if (this.treeSearch.getConvergedBranch()) break;
      }

      const winner = this.treeSearch.getConvergedBranch();
      if (winner) {
        return { winner, bestBranch: winner.branchId, iterations };
      }

      this.treeSearch.pruneUnderperformingBranches(iteration);
    }

    const best = this.treeSearch.getBestBranch();
    return {
      winner: null,
      bestBranch: best?.branchId ?? null,
      iterations,
      error: best?.failureHistory[best.failureHistory.length - 1]
    };
  }

  private dependencySources(context: ModuleRunContext, files: Map<string, string>): ModuleDependency[] {
    return context.dependencies.map((contract) => ({ contract, source: files.get(contract.targetFilePath) ?? "" }));
  }

  private buildRunReport(results: ModuleConvergenceResult[]): ConvergenceRunReport {
    const failedIndex = results.findIndex((r) => r.status === "failed");
    const attempted = results.filter((r) => r.status !== "skipped");
    return {
      converged: results.length > 0 && results.every((r) => r.status === "converged"),
      winningBranch: attempted[attempted.length - 1]?.winningBranch ?? null,
      totalSpentUsd: this.economics.getTotalSpentUsd(),
      committedHashes: results.flatMap((r) => (r.commitHash ? [r.commitHash] : [])),
      modifiedFiles: Array.from(new Set(results.flatMap((r) => r.modifiedFiles))),
      modules: results,
      error:
        failedIndex >= 0
          ? `Module ${failedIndex + 1}/${results.length} "${results[failedIndex].moduleName}": ${results[failedIndex].error}`
          : undefined
    };
  }

  private async runBranchIteration(
    branch: ArchitecturalBranchNode,
    iteration: number,
    lspMultiplexer: LSPMultiplexer,
    context: ModuleRunContext
  ): Promise<void> {
    const { contract } = branch;
    const testSuite = branch.testSuite!;
    logger.updateState({ activeModule: `${contract.moduleName} (${context.position}) [${branch.branchId} #${iteration}]` });

    let patches: SemanticPatch[];
    try {
      patches = await this.synthesizer.synthesizeSemanticPatches({
        branchId: branch.branchId,
        contract,
        testSuite,
        currentSource: branch.workspaceSnapshot.get(contract.targetFilePath) ?? "",
        feedbackText: branch.failureHistory[branch.failureHistory.length - 1] ?? "None (first attempt).",
        episodicMemoryContext: context.episodicContext,
        workspaceFilePaths: Array.from(branch.workspaceSnapshot.keys()),
        dependencies: this.dependencySources(context, branch.workspaceSnapshot)
      });
    } catch (err) {
      if (isBudgetError(err)) throw err;
      this.treeSearch.pruneBranch(branch.branchId, `synthesis failed: ${errorMessage(err)}`);
      return;
    }

    // The synthesizer must not be able to pass by rewriting the auditor's tests, this module's or earlier ones'.
    const auditorOwned = new Set([testSuite.testFilePath, ...context.earlierModules.map((m) => m.testFilePath)]);
    const notes: string[] = [];
    const allowedPatches = patches.filter((p) => {
      if (auditorOwned.has(p.filePath)) {
        notes.push(`Rejected ${p.action} on ${p.filePath}: the test file is owned by the auditor and cannot be modified.`);
        return false;
      }
      return true;
    });

    const { updatedFiles, results } = this.patcher.applyBatch(branch.workspaceSnapshot, allowedPatches);
    for (const result of results) {
      if (!result.success) notes.push(`Rejected patch on ${result.filePath}: ${result.errorDetails ?? "unknown error"}`);
    }
    updatedFiles.set(testSuite.testFilePath, testSuite.testCode);
    const withNotes = (text: string): string => (notes.length > 0 ? `${notes.join("\n")}\n\n${text}` : text);

    const patchedTargetCode = updatedFiles.get(contract.targetFilePath) ?? "";
    const lspFrames = await lspMultiplexer.validateFile(contract.targetFilePath, patchedTargetCode, updatedFiles);
    const lspErrors = lspFrames.filter((f) => f.severity === "ERROR");
    const baseMetrics = {
      lspDiagnosticLatencyMs: lspMultiplexer.getLastLatencyMs(),
      lspErrorCount: lspErrors.length,
      sandboxPassed: false,
      mutationKillRatio: 0,
      iteration
    };

    if (lspErrors.length > 0) {
      this.treeSearch.recordBranchStep(
        branch.branchId,
        baseMetrics,
        updatedFiles,
        allowedPatches,
        withNotes(this.diagParser.formatFramesForPrompt(lspErrors))
      );
      return;
    }

    emitProgress({ type: "phase", phase: "checking", module: contract.moduleName, attempt: iteration });
    const sandboxResult = await this.sandbox.executeWorkspaceTests(updatedFiles, contract.language, testSuite.testFilePath);
    if (!sandboxResult.passed) {
      this.treeSearch.recordBranchStep(branch.branchId, baseMetrics, updatedFiles, allowedPatches, withNotes(sandboxResult.summaryText));
      return;
    }

    const regression = await this.runEarlierModuleSuites(updatedFiles, context.earlierModules);
    if (regression) {
      this.treeSearch.recordBranchStep(branch.branchId, baseMetrics, updatedFiles, allowedPatches, withNotes(regression));
      return;
    }

    const lethalityParams = {
      workspaceFiles: updatedFiles,
      targetFilePath: contract.targetFilePath,
      testFilePath: testSuite.testFilePath,
      language: contract.language
    };
    let mutationReport = await this.mutationEngine.evaluateTestLethality(lethalityParams);

    if (!mutationReport.passedLethalityThreshold) {
      logger.warn(
        "Convergence",
        `Branch ${branch.branchId} tests failed mutation lethality (${(mutationReport.killRatio * 100).toFixed(
          0
        )}%). Regenerating hardened test suite.`
      );
      let hardenedSuite = testSuite;
      try {
        hardenedSuite = await this.auditor.generateMutationLethalTests(
          contract,
          mutationReport.survivorDescriptions.join("\n"),
          this.dependencySources(context, updatedFiles)
        );
        branch.testSuite = hardenedSuite;
      } catch (err) {
        if (isBudgetError(err)) throw err;
        logger.warn("Convergence", `Test hardening failed on Branch ${branch.branchId}: ${errorMessage(err)}`);
      }
      updatedFiles.set(hardenedSuite.testFilePath, hardenedSuite.testCode);

      // Hardened tests must still pass on the unmutated code; otherwise every mutant looks "killed".
      const recheck = await this.sandbox.executeWorkspaceTests(updatedFiles, contract.language, hardenedSuite.testFilePath);
      if (!recheck.passed) {
        this.treeSearch.recordBranchStep(
          branch.branchId,
          baseMetrics,
          updatedFiles,
          allowedPatches,
          withNotes(`The hardened test suite fails against the current implementation:\n${recheck.summaryText}`)
        );
        return;
      }
      mutationReport = await this.mutationEngine.evaluateTestLethality({
        ...lethalityParams,
        testFilePath: hardenedSuite.testFilePath
      });
    }

    this.treeSearch.recordBranchStep(
      branch.branchId,
      { ...baseMetrics, sandboxPassed: true, mutationKillRatio: mutationReport.killRatio },
      updatedFiles,
      allowedPatches,
      mutationReport.passedLethalityThreshold
        ? undefined
        : withNotes(
            `Tests pass, but ${mutationReport.survivedMutants}/${mutationReport.totalMutants} mutants survived:\n${mutationReport.survivorDescriptions.join(
              "\n"
            )}`
          )
    );
  }

  /**
   * Re-runs the test suites of modules converged earlier in this run against a
   * candidate workspace. Returns a failure summary, or null when they all pass.
   */
  private async runEarlierModuleSuites(
    files: Map<string, string>,
    earlierModules: ModuleArchitectureContract[]
  ): Promise<string | null> {
    for (const earlier of earlierModules) {
      // Harnesses that always run the whole suite already covered these tests.
      if (harnessRunsWholeSuite(earlier.language)) continue;
      const result = await this.sandbox.executeWorkspaceTests(files, earlier.language, earlier.testFilePath);
      if (!result.passed) {
        return `Regression: the tests of earlier module "${earlier.moduleName}" (${earlier.testFilePath}) now fail:\n${result.summaryText}`;
      }
    }
    return null;
  }

  private codeFilesOf(files: Map<string, string>): Map<string, string> {
    return new Map(Array.from(files).filter(([p]) => this.astParser.languageForPath(p) !== null));
  }

  /** Writes only files the winning branch created or changed, and returns their workspace-relative paths. */
  private writeWinningWorkspace(
    absTargetDir: string,
    baselineFiles: Map<string, string>,
    winningNode: ArchitecturalBranchNode
  ): string[] {
    const modified: string[] = [];
    for (const [relPath, fileContent] of winningNode.workspaceSnapshot.entries()) {
      if (baselineFiles.get(relPath) === fileContent) continue;
      const absPath = resolveInsideRoot(absTargetDir, relPath);
      fs.mkdirSync(path.dirname(absPath), { recursive: true });
      fs.writeFileSync(absPath, fileContent, "utf8");
      modified.push(relPath);
    }
    return modified;
  }

  private loadWorkspaceFiles(rootDir: string): Map<string, string> {
    const files = new Map<string, string>();
    if (!fs.existsSync(rootDir)) {
      fs.mkdirSync(rootDir, { recursive: true });
      return files;
    }

    const walk = (dir: string): void => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        if (files.size >= MAX_WORKSPACE_FILES) return;
        if (IGNORED_DIRS.has(entry.name) || entry.name.startsWith(".foundry")) continue;
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (accessFor(fullPath, this.accessRules) === "block") continue;
          walk(fullPath);
        } else if (entry.isFile() && (CODE_FILE.test(entry.name) || MANIFEST_FILES.has(entry.name))) {
          if (fs.statSync(fullPath).size > MAX_WORKSPACE_FILE_BYTES) continue;
          files.set(toPosixRelative(rootDir, fullPath), fs.readFileSync(fullPath, "utf8"));
        }
      }
    };

    walk(rootDir);
    if (files.size >= MAX_WORKSPACE_FILES) {
      logger.warn("Convergence", `Workspace scan stopped at ${MAX_WORKSPACE_FILES} files.`);
    }
    return files;
  }

  private inferProjectLanguage(spec: string, codeFiles: Map<string, string>): SupportedLanguage {
    const counts = new Map<SupportedLanguage, number>();
    for (const filePath of codeFiles.keys()) {
      const language = this.astParser.languageForPath(filePath);
      if (language) counts.set(language, (counts.get(language) ?? 0) + 1);
    }
    if (counts.size > 0) {
      return Array.from(counts.entries()).sort((a, b) => b[1] - a[1])[0][0];
    }
    const lower = spec.toLowerCase();
    if (/\brust\b|\bcargo\b/.test(lower)) return "rust";
    if (/\bpython\b|\bpytest\b/.test(lower)) return "python";
    if (/\bgolang\b|\bgopls\b|\bgo\.mod\b|\bgo module\b/.test(lower)) return "go";
    if (/c\+\+|\bcpp\b|\bg\+\+|\bgcc\b/.test(lower)) return "cpp";
    return "typescript";
  }
}
