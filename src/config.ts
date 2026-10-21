import { z } from "zod";
import * as dotenv from "dotenv";
import * as path from "path";

dotenv.config();

export const ConfigSchema = z.object({
  offlineMode: z.boolean().default(false),

  anthropicApiKey: z.string().default(""),
  anthropicModel: z.string().default("claude-fable-5-1"),
  anthropicBaseUrl: z.string().url().default("https://api.anthropic.com"),
  /** Claude also writes code when the OpenAI key is missing or out of credits; 16k truncated module code in practice. */
  anthropicMaxTokens: z.number().int().positive().default(65536),
  anthropicRefusalFallbacks: z.boolean().default(true),

  geminiApiKey: z.string().default(""),
  geminiModel: z.string().default("gemini-3.1-pro-preview"),
  geminiEmbeddingModel: z.string().default("gemini-embedding-2"),
  geminiBaseUrl: z.string().url().default("https://generativelanguage.googleapis.com/v1beta"),
  geminiMaxOutputTokens: z.number().int().positive().default(16384),

  astraApiKey: z.string().default(""),
  astraModel: z.string().default("gpt-5"),
  astraBaseUrl: z.string().url().default("https://api.openai.com/v1/chat/completions"),
  astraMaxCompletionTokens: z.number().int().positive().default(16384),

  /** How hard the models think (Claude effort; the nearest level on Gemini and OpenAI). Unset: each model's default. */
  thinkingLevel: z.enum(["low", "medium", "high", "xhigh", "max"]).optional(),

  /** Which model holds which role. "openai" means the OpenAI key, or the custom provider chosen as ASTRA_*. */
  architectProvider: z.enum(["claude", "gemini", "openai"]).default("claude"),
  auditorProvider: z.enum(["claude", "gemini", "openai"]).default("gemini"),
  writerProvider: z.enum(["claude", "gemini", "openai"]).default("openai"),

  requestTimeoutMs: z.number().int().positive().default(600000),
  requestMaxRetries: z.number().int().min(0).max(10).default(3),

  maxBudgetUsd: z.number().positive().default(5.0),
  /** Per-module spend cap; unset means each module may use whatever is left of maxBudgetUsd. */
  maxModuleBudgetUsd: z.number().positive().optional(),
  maxConvergenceIterations: z.number().int().positive().default(8),
  minMutationKillRatio: z.number().min(0).max(1).default(0.85),
  /** GEF: the auditor also writes a hidden acceptance suite per module that the code writer never sees. */
  holdoutTests: z.boolean().default(true),
  /** TCA: identical results in a row before a branch's attempt is abandoned (0 = never). */
  thrashLimit: z.number().int().min(0).max(20).default(3),
  /** TCA: cache the stable prefix of every prompt on providers that support it (Claude: explicit cache_control). */
  promptCaching: z.boolean().default(true),

  sqliteDbPath: z.string().default(path.resolve(process.cwd(), ".foundry-memory.sqlite")),
  freezeStatePath: z.string().default(path.resolve(process.cwd(), ".foundry-freeze.json")),
  sandboxImageName: z.string().default("foundry-sandbox:latest"),
  sandboxTimeoutMs: z.number().int().positive().default(120000),
  dockerMemoryMb: z.number().int().positive().default(1024),
  dockerCpus: z.number().positive().default(2.0),
  lspDiagnosticTimeoutMs: z.number().int().positive().default(1500),
  autoGitCommit: z.boolean().default(true),

  ouroborosEnabled: z.boolean().default(true),
  ouroborosMaxEventLoopLagMs: z.number().positive().default(180),
  ouroborosMaxAstParseMs: z.number().positive().default(350),
  ouroborosMaxHeapMb: z.number().positive().default(1024),
  ouroborosAutoRestart: z.boolean().default(false),
  foundryRootDir: z.string().default(path.resolve(__dirname, "..")),
  interactiveDashboard: z.boolean().default(true)
});

export type FoundryConfig = z.infer<typeof ConfigSchema>;

function env(name: string): string | undefined {
  const value = process.env[name]?.trim();
  return value ? value : undefined;
}

function envFloat(name: string): number | undefined {
  const value = env(name);
  return value === undefined ? undefined : parseFloat(value);
}

function envInt(name: string): number | undefined {
  const value = env(name);
  return value === undefined ? undefined : parseInt(value, 10);
}

function envBool(name: string): boolean | undefined {
  const value = env(name)?.toLowerCase();
  if (value === undefined) return undefined;
  return value === "true" || value === "1" || value === "yes";
}

export type ModelRoleProvider = FoundryConfig["architectProvider"];
export const ROLE_PROVIDERS: ModelRoleProvider[] = ["claude", "gemini", "openai"];

export function parseRoleProvider(value: string | undefined): ModelRoleProvider | undefined {
  const normalized = value?.trim().toLowerCase();
  if (normalized === "anthropic") return "claude";
  if (normalized === "google") return "gemini";
  if (normalized === "astra") return "openai";
  return (ROLE_PROVIDERS as string[]).includes(normalized ?? "") ? (normalized as ModelRoleProvider) : undefined;
}

export type ThinkingLevel = NonNullable<FoundryConfig["thinkingLevel"]>;
export const THINKING_LEVELS: ThinkingLevel[] = ["low", "medium", "high", "xhigh", "max"];

/** "High", "extra high", "xhigh"... -> a level; anything else -> undefined (a bad setting must not stop a run). */
export function parseThinkingLevel(value: string | undefined): ThinkingLevel | undefined {
  const normalized = value?.trim().toLowerCase().replace(/[\s_-]+/g, "");
  if (!normalized) return undefined;
  if (normalized === "extrahigh") return "xhigh";
  return (THINKING_LEVELS as string[]).includes(normalized) ? (normalized as ThinkingLevel) : undefined;
}

export function loadConfig(overrides: Partial<FoundryConfig> = {}): FoundryConfig {
  // With OpenAI as the code writer (the default), the OpenAI key and model win; ASTRA_* is for a custom provider
  // (Settings › API keys & providers sets FOUNDRY_SYNTHESIZER to its id). A stale ASTRA_API_KEY used to shadow
  // a newly saved OPENAI_API_KEY.
  const openAiWrites = (env("FOUNDRY_SYNTHESIZER") ?? "openai").toLowerCase() === "openai";
  const raw: Record<string, unknown> = {
    offlineMode: envBool("FOUNDRY_OFFLINE_MODE"),

    anthropicApiKey: env("ANTHROPIC_API_KEY"),
    anthropicModel: env("ANTHROPIC_MODEL"),
    anthropicBaseUrl: env("ANTHROPIC_BASE_URL"),
    anthropicMaxTokens: envInt("ANTHROPIC_MAX_TOKENS"),
    anthropicRefusalFallbacks: envBool("ANTHROPIC_REFUSAL_FALLBACKS"),

    geminiApiKey: env("GEMINI_API_KEY"),
    geminiModel: env("GEMINI_MODEL"),
    geminiEmbeddingModel: env("GEMINI_EMBEDDING_MODEL"),
    geminiBaseUrl: env("GEMINI_BASE_URL"),
    geminiMaxOutputTokens: envInt("GEMINI_MAX_OUTPUT_TOKENS"),

    // ASTRA_* is the role-specific setting; OPENAI_* is the shared fallback.
    astraApiKey: openAiWrites ? env("OPENAI_API_KEY") ?? env("ASTRA_API_KEY") : env("ASTRA_API_KEY") ?? env("OPENAI_API_KEY"),
    astraModel: openAiWrites ? env("OPENAI_MODEL") ?? env("ASTRA_MODEL") : env("ASTRA_MODEL") ?? env("OPENAI_MODEL"),
    astraBaseUrl: env("ASTRA_BASE_URL"),
    astraMaxCompletionTokens: envInt("ASTRA_MAX_TOKENS"),

    thinkingLevel: parseThinkingLevel(env("FOUNDRY_THINKING")),
    architectProvider: parseRoleProvider(env("FOUNDRY_ARCHITECT")),
    auditorProvider: parseRoleProvider(env("FOUNDRY_AUDITOR")),
    writerProvider: parseRoleProvider(env("FOUNDRY_WRITER")),
    requestTimeoutMs: envInt("REQUEST_TIMEOUT_MS"),
    requestMaxRetries: envInt("REQUEST_MAX_RETRIES"),

    maxBudgetUsd: envFloat("MAX_BUDGET_USD"),
    maxModuleBudgetUsd: envFloat("MAX_MODULE_BUDGET_USD"),
    maxConvergenceIterations: envInt("MAX_CONVERGENCE_ITERATIONS"),
    minMutationKillRatio: envFloat("MIN_MUTATION_KILL_RATIO"),
    holdoutTests: envBool("FOUNDRY_HOLDOUT_TESTS"),
    thrashLimit: envInt("FOUNDRY_THRASH_LIMIT"),
    promptCaching: envBool("FOUNDRY_PROMPT_CACHING"),

    sqliteDbPath: env("FOUNDRY_SQLITE_PATH"),
    freezeStatePath: env("FOUNDRY_FREEZE_PATH"),
    sandboxImageName: env("FOUNDRY_SANDBOX_IMAGE"),
    sandboxTimeoutMs: envInt("FOUNDRY_SANDBOX_TIMEOUT_MS"),
    dockerMemoryMb: envInt("DOCKER_MEMORY_MB"),
    dockerCpus: envFloat("DOCKER_CPUS"),
    lspDiagnosticTimeoutMs: envInt("FOUNDRY_LSP_TIMEOUT_MS"),
    autoGitCommit: envBool("AUTO_GIT_COMMIT"),

    ouroborosEnabled: envBool("OUROBOROS_AUTO_HEAL"),
    ouroborosMaxEventLoopLagMs: envFloat("OUROBOROS_MAX_LAG_MS"),
    ouroborosMaxAstParseMs: envFloat("OUROBOROS_MAX_AST_MS"),
    ouroborosMaxHeapMb: envFloat("OUROBOROS_MAX_HEAP_MB"),
    ouroborosAutoRestart: envBool("OUROBOROS_AUTO_RESTART"),
    foundryRootDir: env("FOUNDRY_ROOT_DIR"),
    interactiveDashboard: process.env.FOUNDRY_DASHBOARD !== "false",
    ...overrides
  };

  const cleaned = Object.fromEntries(Object.entries(raw).filter(([, value]) => value !== undefined));
  const config = ConfigSchema.parse(cleaned);
  // Unless set explicitly, thinking models get their full output window (see defaultOutputTokens).
  if (cleaned.geminiMaxOutputTokens === undefined) {
    config.geminiMaxOutputTokens = defaultOutputTokens(config.geminiModel, config.geminiMaxOutputTokens);
  }
  if (cleaned.astraMaxCompletionTokens === undefined) {
    config.astraMaxCompletionTokens = defaultOutputTokens(config.astraModel, config.astraMaxCompletionTokens);
  }
  for (const key of ["sqliteDbPath", "freezeStatePath", "foundryRootDir"] as const) {
    config[key] = path.resolve(config[key]);
  }
  config.anthropicBaseUrl = anthropicSdkBaseUrl(config.anthropicBaseUrl);
  config.astraBaseUrl = completeEndpoint(config.astraBaseUrl, "/chat/completions", "/v1");
  config.geminiBaseUrl = /\/v1(beta|alpha)?$/.test(config.geminiBaseUrl.replace(/\/+$/, ""))
    ? config.geminiBaseUrl.replace(/\/+$/, "")
    : `${config.geminiBaseUrl.replace(/\/+$/, "")}/v1beta`;
  return config;
}

/**
 * Thinking models (Gemini 2.5 and 3, OpenAI's gpt-5 and o-series) spend part of their output cap on reasoning
 * before the answer, so a 16k cap truncates large answers such as a module's test suite. They get 65,536 tokens,
 * which every one of them accepts; others keep the schema default. Unused cap costs nothing.
 */
export function defaultOutputTokens(model: string, fallback: number): number {
  const name = model.trim();
  if (/argon/i.test(name)) return 32768; // Gemini 4 Argon's output limit
  return /^(models\/)?(gemini-(2\.5|[3-9])|gpt-5|o[1-9])/i.test(name) ? 65536 : fallback;
}

/**
 * Accepts either a full endpoint URL or a provider base URL (e.g. the
 * `https://api.anthropic.com` form many tools put in ANTHROPIC_BASE_URL)
 * and returns the full endpoint.
 */
function completeEndpoint(url: string, endpointSuffix: string, versionSegment: string): string {
  const trimmed = url.replace(/\/+$/, "");
  if (trimmed.endsWith(endpointSuffix)) return trimmed;
  if (/\/v\d+$/.test(trimmed)) return `${trimmed}${endpointSuffix}`;
  return `${trimmed}${versionSegment}${endpointSuffix}`;
}

/**
 * The Anthropic SDK takes the API base URL and appends `/v1/messages` itself,
 * while existing .env files (and the GUI) store the full endpoint. Accepts the
 * base, `/v1` and `/v1/messages` forms and returns the base.
 */
function anthropicSdkBaseUrl(url: string): string {
  return url.replace(/\/+$/, "").replace(/\/messages$/, "").replace(/\/v1$/, "");
}

/**
 * Lists the credentials a live (non-offline) run is missing. The synthesizer
 * role falls back to the Anthropic key when no Astra/OpenAI key is configured.
 */
export function missingCredentials(config: FoundryConfig): string[] {
  if (config.offlineMode) return [];
  const missing: string[] = [];
  const roles: Array<[ModelRoleProvider, string]> = [
    [config.architectProvider, "architect"],
    [config.auditorProvider, "auditor"],
    [config.writerProvider, "code writer"]
  ];
  const needs = (provider: ModelRoleProvider): string => roles.filter(([p]) => p === provider).map(([, r]) => r).join(", ");
  if (needs("claude") && !config.anthropicApiKey) missing.push(`ANTHROPIC_API_KEY (${needs("claude")})`);
  if (needs("gemini") && !config.geminiApiKey) missing.push(`GEMINI_API_KEY (${needs("gemini")})`);
  if (needs("openai") && !config.astraApiKey) {
    // The code writer alone may fall back to Claude; the architect or auditor on OpenAI needs the key.
    const onlyWriter = config.architectProvider !== "openai" && config.auditorProvider !== "openai";
    if (!(onlyWriter && config.anthropicApiKey)) missing.push(`OPENAI_API_KEY (${needs("openai")})`);
  }
  return missing;
}
