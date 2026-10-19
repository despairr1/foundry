import { SemanticPatch } from "../ast/semantic_patcher";
import { ModuleArchitectureContract } from "../swarm/claude_architect";
import { TestSuiteSpec } from "../swarm/gemini_auditor";
import { logger } from "../utils/logger";

export type BranchId = "Alpha" | "Beta" | "Gamma";

export interface BranchEvaluationMetrics {
  lspDiagnosticLatencyMs: number;
  lspErrorCount: number;
  sandboxPassed: boolean;
  mutationKillRatio: number;
  iteration: number;
}

export interface ArchitecturalBranchNode {
  branchId: BranchId;
  status: "ACTIVE" | "PRUNED" | "CONVERGED";
  contract: ModuleArchitectureContract;
  testSuite?: TestSuiteSpec;
  patches: SemanticPatch[];
  workspaceSnapshot: Map<string, string>;
  metrics: BranchEvaluationMetrics;
  fitnessScore: number;
  failureHistory: string[];
}

export class TreeSearchEngine {
  private branches = new Map<BranchId, ArchitecturalBranchNode>();
  private readonly branchOrder: BranchId[] = ["Alpha", "Beta", "Gamma"];
  private readonly minKillRatio: number;

  constructor(minKillRatio = 0.85) {
    this.minKillRatio = minKillRatio;
  }

  public initializeBranches(
    contractsByBranch: Map<BranchId, ModuleArchitectureContract>,
    baseWorkspace: Map<string, string>
  ): ArchitecturalBranchNode[] {
    this.branches.clear();

    for (const branchId of this.branchOrder) {
      const contract = contractsByBranch.get(branchId);
      if (!contract) {
        // Clear the dashboard row a previous module left behind.
        logger.updateBranch(branchId, { status: "PRUNED", fitness: 0, diagnosticsCount: 0, mutationKillRatio: 0, iteration: 0 });
        continue;
      }

      const node: ArchitecturalBranchNode = {
        branchId,
        status: "ACTIVE",
        contract,
        patches: [],
        workspaceSnapshot: new Map<string, string>(baseWorkspace),
        metrics: {
          lspDiagnosticLatencyMs: 0,
          lspErrorCount: 0,
          sandboxPassed: false,
          mutationKillRatio: 0,
          iteration: 0
        },
        fitnessScore: 0,
        failureHistory: []
      };

      this.branches.set(branchId, node);
      logger.updateBranch(branchId, {
        status: "ACTIVE",
        fitness: 0,
        diagnosticsCount: 0,
        mutationKillRatio: 0,
        iteration: 0
      });
    }

    return this.getActiveBranches();
  }

  public computeFitness(metrics: BranchEvaluationMetrics): number {
    const lspPenalty = metrics.lspErrorCount * 20;
    const latencyBonus = Math.max(0, 15 - metrics.lspDiagnosticLatencyMs / 50);
    const sandboxScore = metrics.sandboxPassed ? 50 : 0;
    const mutationScore = metrics.mutationKillRatio * 35;

    return Math.max(0, sandboxScore + mutationScore + latencyBonus - lspPenalty);
  }

  public recordBranchStep(
    branchId: BranchId,
    metrics: BranchEvaluationMetrics,
    updatedWorkspace: Map<string, string>,
    patches: SemanticPatch[],
    failureSummary?: string
  ): ArchitecturalBranchNode {
    const node = this.branches.get(branchId);
    if (!node) {
      throw new Error(`Unknown search branch ${branchId}`);
    }

    node.metrics = metrics;
    node.workspaceSnapshot = new Map(updatedWorkspace);
    node.patches = patches;
    node.fitnessScore = this.computeFitness(metrics);

    if (failureSummary) {
      node.failureHistory.push(failureSummary);
    }

    if (metrics.sandboxPassed && metrics.lspErrorCount === 0 && metrics.mutationKillRatio >= this.minKillRatio) {
      node.status = "CONVERGED";
    }

    // One fixed-shape line per step so the log (and the GUI's Search tree page, gui/engine_log.py) carries the
    // numbers the console dashboard shows:
    //   Branch Alpha step 2: fitness 75.5, 0 LSP errors, mutation kill 80%, tests passed
    // Mutation testing only runs once the tests pass; before that the kill ratio is a placeholder, not a measurement.
    const measured = metrics.sandboxPassed || metrics.mutationKillRatio > 0;
    const killText = measured ? `${Math.round(metrics.mutationKillRatio * 100)}%` : "not measured";
    logger.info(
      "TreeSearch",
      `Branch ${branchId} step ${metrics.iteration}: fitness ${node.fitnessScore.toFixed(1)}, ` +
        `${metrics.lspErrorCount} LSP error${metrics.lspErrorCount === 1 ? "" : "s"}, mutation kill ${killText}, ` +
        `tests ${metrics.sandboxPassed ? "passed" : "failed"}`
    );

    logger.updateBranch(branchId, {
      status: node.status,
      fitness: node.fitnessScore,
      diagnosticsCount: metrics.lspErrorCount,
      mutationKillRatio: metrics.mutationKillRatio,
      iteration: metrics.iteration
    });

    return node;
  }

  public pruneUnderperformingBranches(iteration: number): void {
    if (iteration < 2) return;
    const active = this.getActiveBranches();
    if (active.length <= 1) return;

    active.sort((a, b) => b.fitnessScore - a.fitnessScore);
    const leaderFitness = active[0].fitnessScore;

    for (let i = 1; i < active.length; i += 1) {
      const candidate = active[i];
      if (leaderFitness - candidate.fitnessScore > 35 || candidate.metrics.lspErrorCount > 5) {
        candidate.status = "PRUNED";
        logger.updateBranch(candidate.branchId, { status: "PRUNED" });
        logger.info(
          "TreeSearch",
          `Pruned Branch ${candidate.branchId} (fitness ${candidate.fitnessScore.toFixed(1)} vs leader ${leaderFitness.toFixed(1)})`
        );
      }
    }
  }

  public reset(): void {
    this.branches.clear();
  }

  public pruneBranch(branchId: BranchId, reason: string): void {
    const node = this.branches.get(branchId);
    if (!node || node.status !== "ACTIVE") return;
    node.status = "PRUNED";
    node.failureHistory.push(reason);
    logger.updateBranch(branchId, { status: "PRUNED" });
    logger.warn("TreeSearch", `Pruned Branch ${branchId}: ${reason}`);
  }

  public getConvergedBranch(): ArchitecturalBranchNode | null {
    for (const node of this.branches.values()) {
      if (node.status === "CONVERGED") {
        return node;
      }
    }
    return null;
  }

  public getBestBranch(): ArchitecturalBranchNode | null {
    const all = Array.from(this.branches.values());
    if (all.length === 0) return null;
    all.sort((a, b) => b.fitnessScore - a.fitnessScore);
    return all[0];
  }

  public getActiveBranches(): ArchitecturalBranchNode[] {
    return Array.from(this.branches.values()).filter((b) => b.status === "ACTIVE");
  }
}