import * as fs from "fs";
import * as path from "path";
import { logger } from "../utils/logger";
import { emitProgress } from "../utils/progress";

export type ModelProvider =
  | "claude"
  | "gemini"
  | "astra"
  | "gemini_embedding"
  | "openai"
  | "deepseek"
  | "openrouter";

export interface PricingTier {
  inputCostPerMillionUsd: number;
  outputCostPerMillionUsd: number;
}

export interface TokenLedgerEntry {
  timestamp: string;
  provider: ModelProvider;
  model: string;
  promptTokens: number;
  completionTokens: number;
  costUsd: number;
}

export interface FreezeDumpState {
  reason: string;
  timestamp: string;
  maxBudgetUsd: number;
  totalSpentUsd: number;
  moduleBudget: { moduleName: string; maxUsd: number; spentUsd: number } | null;
  ledger: TokenLedgerEntry[];
  activeContext: Record<string, unknown>;
}

interface ModuleBudgetScope {
  moduleName: string;
  maxUsd: number;
  startSpentUsd: number;
}

export class BudgetExceededError extends Error {
  public readonly spentUsd: number;
  public readonly maxBudgetUsd: number;

  constructor(spentUsd: number, maxBudgetUsd: number, reason?: string) {
    super(
      reason ??
        `Token circuit breaker tripped: spent $${spentUsd.toFixed(4)} USD, exceeding max budget of $${maxBudgetUsd.toFixed(
          4
        )} USD.`
    );
    this.name = "BudgetExceededError";
    this.spentUsd = spentUsd;
    this.maxBudgetUsd = maxBudgetUsd;
  }
}

/**
 * Per-million-token list prices. Each provider's rules are checked in order and
 * the first matching substring wins; the final entry is the provider default.
 * Unknown models deliberately fall back to the most expensive tier of their
 * provider so the circuit breaker trips early rather than late.
 */
const PRICING_RULES: Record<ModelProvider, Array<[string, PricingTier]>> = {
  claude: [
    ["fable", { inputCostPerMillionUsd: 10.0, outputCostPerMillionUsd: 50.0 }],
    ["mythos", { inputCostPerMillionUsd: 10.0, outputCostPerMillionUsd: 50.0 }],
    ["opus-5-5", { inputCostPerMillionUsd: 4.0, outputCostPerMillionUsd: 20.0 }],
    ["opus-5", { inputCostPerMillionUsd: 5.0, outputCostPerMillionUsd: 25.0 }],
    ["opus-4-8", { inputCostPerMillionUsd: 5.0, outputCostPerMillionUsd: 25.0 }],
    ["opus-4-7", { inputCostPerMillionUsd: 5.0, outputCostPerMillionUsd: 25.0 }],
    ["opus-4-6", { inputCostPerMillionUsd: 5.0, outputCostPerMillionUsd: 25.0 }],
    ["opus-4-5", { inputCostPerMillionUsd: 5.0, outputCostPerMillionUsd: 25.0 }],
    ["opus", { inputCostPerMillionUsd: 15.0, outputCostPerMillionUsd: 75.0 }],
    ["sonnet-5", { inputCostPerMillionUsd: 2.0, outputCostPerMillionUsd: 10.0 }],
    ["sonnet", { inputCostPerMillionUsd: 3.0, outputCostPerMillionUsd: 15.0 }],
    ["haiku-5", { inputCostPerMillionUsd: 0.1, outputCostPerMillionUsd: 0.5 }],
    ["haiku-4", { inputCostPerMillionUsd: 1.0, outputCostPerMillionUsd: 5.0 }],
    ["haiku", { inputCostPerMillionUsd: 0.8, outputCostPerMillionUsd: 4.0 }],
    ["", { inputCostPerMillionUsd: 10.0, outputCostPerMillionUsd: 50.0 }]
  ],
  gemini: [
    ["flash-lite", { inputCostPerMillionUsd: 0.1, outputCostPerMillionUsd: 0.4 }],
    ["flash", { inputCostPerMillionUsd: 0.3, outputCostPerMillionUsd: 2.5 }],
    ["", { inputCostPerMillionUsd: 2.0, outputCostPerMillionUsd: 12.0 }]
  ],
  gemini_embedding: [["", { inputCostPerMillionUsd: 0.15, outputCostPerMillionUsd: 0.0 }]],
  astra: [
    ["nano", { inputCostPerMillionUsd: 0.05, outputCostPerMillionUsd: 0.4 }],
    ["mini", { inputCostPerMillionUsd: 0.25, outputCostPerMillionUsd: 2.0 }],
    ["gpt-5", { inputCostPerMillionUsd: 1.25, outputCostPerMillionUsd: 10.0 }],
    ["gpt-4o", { inputCostPerMillionUsd: 2.5, outputCostPerMillionUsd: 10.0 }],
    ["", { inputCostPerMillionUsd: 15.0, outputCostPerMillionUsd: 60.0 }]
  ],
  openai: [
    ["nano", { inputCostPerMillionUsd: 0.05, outputCostPerMillionUsd: 0.4 }],
    ["mini", { inputCostPerMillionUsd: 0.25, outputCostPerMillionUsd: 2.0 }],
    ["gpt-5", { inputCostPerMillionUsd: 1.25, outputCostPerMillionUsd: 10.0 }],
    ["gpt-4o", { inputCostPerMillionUsd: 2.5, outputCostPerMillionUsd: 10.0 }],
    ["", { inputCostPerMillionUsd: 15.0, outputCostPerMillionUsd: 60.0 }]
  ],
  deepseek: [["", { inputCostPerMillionUsd: 0.56, outputCostPerMillionUsd: 1.68 }]],
  openrouter: [["", { inputCostPerMillionUsd: 10.0, outputCostPerMillionUsd: 50.0 }]]
};

/**
 * A custom synthesizer (any OpenAI-compatible endpoint set as ASTRA_BASE_URL) is priced by
 * ASTRA_INPUT_PRICE_PER_M / ASTRA_OUTPUT_PRICE_PER_M when they are set, since its model name is unknown here.
 */
function customAstraPricing(): PricingTier | undefined {
  const input = Number(process.env.ASTRA_INPUT_PRICE_PER_M);
  const output = Number(process.env.ASTRA_OUTPUT_PRICE_PER_M);
  if (!process.env.ASTRA_INPUT_PRICE_PER_M?.trim() || !Number.isFinite(input) || input < 0) return undefined;
  return { inputCostPerMillionUsd: input, outputCostPerMillionUsd: Number.isFinite(output) && output >= 0 ? output : input };
}

export function resolvePricing(provider: ModelProvider, model: string): PricingTier {
  if (provider === "astra") {
    const custom = customAstraPricing();
    if (custom) return custom;
  }
  const lower = model.toLowerCase();
  const rules = PRICING_RULES[provider] ?? PRICING_RULES.claude;
  for (const [needle, tier] of rules) {
    if (lower.includes(needle)) return tier;
  }
  return rules[rules.length - 1][1];
}

export class EconomicsEngine {
  private maxBudgetUsd: number;
  private freezeStatePath: string;
  private totalSpentUsd = 0;
  private ledger: TokenLedgerEntry[] = [];
  private activeContext: Record<string, unknown> = {};
  private moduleScope: ModuleBudgetScope | null = null;
  private exitOnFreeze: boolean;

  constructor(maxBudgetUsd: number, freezeStatePath: string, exitOnFreeze = true) {
    this.maxBudgetUsd = maxBudgetUsd;
    this.freezeStatePath = freezeStatePath;
    this.exitOnFreeze = exitOnFreeze;
    logger.updateState({ maxBudgetUsd, spentUsd: 0 });
  }

  public setExecutionContext(context: Record<string, unknown>): void {
    this.activeContext = { ...this.activeContext, ...context };
  }

  /**
   * Starts a per-module allowance: from now on the circuit breaker also trips
   * when spend since this call would exceed `maxUsd`. The run-wide budget keeps
   * applying on top of it. Replaces any previous module allowance.
   */
  public beginModuleBudget(moduleName: string, maxUsd: number): void {
    this.moduleScope = { moduleName, maxUsd, startSpentUsd: this.totalSpentUsd };
  }

  public endModuleBudget(): void {
    this.moduleScope = null;
  }

  public getModuleSpentUsd(): number {
    return this.moduleScope ? this.totalSpentUsd - this.moduleScope.startSpentUsd : 0;
  }

  public estimateTokens(text: string): number {
    if (!text) return 0;
    return Math.ceil(Buffer.byteLength(text, "utf8") / 3.8);
  }

  /**
   * Refuses to start a request whose projected cost (prompt plus a typical
   * completion) would push spend past the budget.
   */
  public enforceBudgetPreFlight(
    provider: ModelProvider,
    model: string,
    estimatedPromptText: string,
    expectedCompletionTokens = 4096
  ): void {
    const pricing = resolvePricing(provider, model);
    const projectedCost =
      (this.estimateTokens(estimatedPromptText) / 1_000_000) * pricing.inputCostPerMillionUsd +
      (expectedCompletionTokens / 1_000_000) * pricing.outputCostPerMillionUsd;
    if (this.totalSpentUsd + projectedCost > this.maxBudgetUsd) {
      this.freezeAndAbort(
        `Pre-flight budget check failed for ${provider} (${model}): current $${this.totalSpentUsd.toFixed(
          4
        )} + projected $${projectedCost.toFixed(4)} > $${this.maxBudgetUsd.toFixed(4)}`
      );
    }
    if (this.moduleScope && this.getModuleSpentUsd() + projectedCost > this.moduleScope.maxUsd) {
      this.freezeAndAbort(
        `Pre-flight module budget check failed for ${provider} (${model}) on module "${
          this.moduleScope.moduleName
        }": module spend $${this.getModuleSpentUsd().toFixed(4)} + projected $${projectedCost.toFixed(
          4
        )} > $${this.moduleScope.maxUsd.toFixed(4)}`
      );
    }
  }

  public recordUsage(
    provider: ModelProvider,
    model: string,
    promptTokens: number,
    completionTokens: number
  ): TokenLedgerEntry {
    const pricing = resolvePricing(provider, model);
    const inputCost = (promptTokens / 1_000_000) * pricing.inputCostPerMillionUsd;
    const outputCost = (completionTokens / 1_000_000) * pricing.outputCostPerMillionUsd;
    const costUsd = inputCost + outputCost;

    const entry: TokenLedgerEntry = {
      timestamp: new Date().toISOString(),
      provider,
      model,
      promptTokens,
      completionTokens,
      costUsd
    };

    this.ledger.push(entry);
    this.totalSpentUsd += costUsd;

    logger.updateState({
      spentUsd: this.totalSpentUsd,
      maxBudgetUsd: this.maxBudgetUsd
    });
    emitProgress({ type: "spend", spentUsd: this.totalSpentUsd, limitUsd: this.maxBudgetUsd });

    logger.info(
      "Economics",
      `${provider.toUpperCase()} (${model}): +${promptTokens}in/${completionTokens}out ($${costUsd.toFixed(
        4
      )}) | Total: $${this.totalSpentUsd.toFixed(4)}/$${this.maxBudgetUsd.toFixed(2)}`
    );

    if (this.totalSpentUsd > this.maxBudgetUsd) {
      this.freezeAndAbort(
        `Budget exceeded after ${provider} completion: $${this.totalSpentUsd.toFixed(4)} > $${this.maxBudgetUsd.toFixed(4)}`
      );
    }
    if (this.moduleScope && this.getModuleSpentUsd() > this.moduleScope.maxUsd) {
      this.freezeAndAbort(
        `Module budget exceeded on "${this.moduleScope.moduleName}" after ${provider} completion: $${this.getModuleSpentUsd().toFixed(
          4
        )} > $${this.moduleScope.maxUsd.toFixed(4)}`
      );
    }

    return entry;
  }

  public getTotalSpentUsd(): number {
    return this.totalSpentUsd;
  }

  public getRemainingBudgetUsd(): number {
    return Math.max(0, this.maxBudgetUsd - this.totalSpentUsd);
  }

  public getLedger(): ReadonlyArray<TokenLedgerEntry> {
    return this.ledger;
  }

  private freezeAndAbort(reason: string): never {
    const dump: FreezeDumpState = {
      reason,
      timestamp: new Date().toISOString(),
      maxBudgetUsd: this.maxBudgetUsd,
      totalSpentUsd: this.totalSpentUsd,
      moduleBudget: this.moduleScope
        ? { moduleName: this.moduleScope.moduleName, maxUsd: this.moduleScope.maxUsd, spentUsd: this.getModuleSpentUsd() }
        : null,
      ledger: this.ledger,
      activeContext: this.activeContext
    };

    try {
      fs.mkdirSync(path.dirname(this.freezeStatePath), { recursive: true });
      fs.writeFileSync(this.freezeStatePath, JSON.stringify(dump, null, 2), "utf8");
      logger.error("Economics", `${reason}. Execution frozen; progress dumped to ${this.freezeStatePath}`);
    } catch (err) {
      logger.error("Economics", `Failed to write freeze state: ${String(err)}`);
    }

    if (this.exitOnFreeze) {
      process.exit(42);
    }
    throw new BudgetExceededError(this.totalSpentUsd, this.maxBudgetUsd, reason);
  }
}
