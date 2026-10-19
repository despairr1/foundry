import Anthropic from "@anthropic-ai/sdk";
import { FoundryConfig, ThinkingLevel } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { NonRetryableError, withExponentialBackoff } from "../utils/logger";

/** A retryable HTTP failure (408/409/429/5xx). Non-retryable statuses throw NonRetryableError instead. */
export class HttpStatusError extends Error {
  public readonly status: number;
  public readonly retryAfterMs?: number;

  constructor(message: string, status: number, retryAfterMs?: number) {
    super(message);
    this.name = "HttpStatusError";
    this.status = status;
    this.retryAfterMs = retryAfterMs;
  }
}

const RETRYABLE_STATUSES = new Set([408, 409, 425, 429, 500, 502, 503, 504, 529]);

/** The provider's account is out of credits: no retry will help, and later calls in this run are pointless. */
export class QuotaExhaustedError extends NonRetryableError {
  public readonly provider: string;

  constructor(provider: string, message: string) {
    super(message);
    this.name = "QuotaExhaustedError";
    this.provider = provider;
  }
}

const exhaustedProviders = new Map<string, string>();

/** True once a provider answered "no credits remaining" in this process. */
export function providerExhausted(provider: string): string | undefined {
  return exhaustedProviders.get(provider);
}

export function markProviderExhausted(provider: string, reason: string): void {
  exhaustedProviders.set(provider, reason);
}

/** For tests. */
export function resetProviderState(): void {
  exhaustedProviders.clear();
}

function isQuotaExhausted(status: number, snippet: string): boolean {
  return status === 429 && /insufficient_quota|no credits remaining|exceeded your current quota/i.test(snippet);
}

function parseRetryAfter(header: string | null): number | undefined {
  if (!header) return undefined;
  const seconds = Number(header);
  if (Number.isFinite(seconds)) return Math.min(60_000, Math.max(0, seconds * 1000));
  const date = Date.parse(header);
  return Number.isNaN(date) ? undefined : Math.min(60_000, Math.max(0, date - Date.now()));
}

export async function postJson<T>(
  label: string,
  url: string,
  headers: Record<string, string>,
  body: unknown,
  timeoutMs: number
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs)
    });
  } catch (err) {
    if (err instanceof Error && err.name === "TimeoutError") {
      throw new Error(`${label} timed out after ${timeoutMs}ms (raise REQUEST_TIMEOUT_MS for long generations)`);
    }
    throw new Error(`${label} network error: ${err instanceof Error ? err.message : String(err)}`);
  }

  if (!res.ok) {
    const snippet = (await res.text().catch(() => "")).replace(/\s+/g, " ").slice(0, 400);
    const message = `${label} HTTP ${res.status}${snippet ? `: ${snippet}` : ""}`;
    if (isQuotaExhausted(res.status, snippet)) {
      throw new QuotaExhaustedError(label, `${label}: the account has no credits remaining (HTTP 429 insufficient_quota). Add credits, or pick another code writer in Settings.`);
    }
    if (RETRYABLE_STATUSES.has(res.status)) {
      throw new HttpStatusError(message, res.status, parseRetryAfter(res.headers.get("retry-after")));
    }
    throw new NonRetryableError(message);
  }

  return (await res.json()) as T;
}

/**
 * Thinking level -> each provider's own control. Claude takes all five as `output_config.effort`. Gemini 3 and 2.5
 * take low/medium/high (Gemini 3 Pro only low/high; older models have no control). OpenAI's reasoning models take
 * low/medium/high, plus xhigh on GPT-5.5 and later. Levels a model lacks map to its nearest one.
 */
export function geminiThinkingConfig(model: string, level: ThinkingLevel | undefined): Record<string, unknown> | undefined {
  if (!level || !/gemini-(2\.5|[3-9])/i.test(model)) return undefined;
  const capped = level === "xhigh" || level === "max" ? "high" : level;
  if (/gemini-3-pro/i.test(model) && capped === "medium") return { thinkingLevel: "high" }; // 3 Pro: low or high
  return { thinkingLevel: capped };
}

export function openAiReasoningEffort(model: string, level: ThinkingLevel | undefined): string | undefined {
  if (!level || !/^(gpt-5|o[1-9])/i.test(model.trim())) return undefined;
  const hasXhigh = /^gpt-5\.([5-9]|\d\d)/i.test(model.trim());
  if (level === "xhigh" || level === "max") return hasXhigh ? "xhigh" : "high";
  return level;
}

function backoffOptions(config: FoundryConfig): { maxAttempts: number } {
  return { maxAttempts: config.requestMaxRetries + 1 };
}

export interface CompletionRequest {
  label: string;
  prompt: string;
  system?: string;
  /** Validates/parses the raw text; throwing here retries the request (counts against the budget). */
  parse?: (text: string) => unknown;
  /** Claude only: receives the reply text as it streams in (used by the project chat). */
  onText?: (delta: string) => void;
  /** Claude only: called before every attempt (1, 2, ...), so a streaming caller can drop a failed attempt's text. */
  onAttempt?: (attempt: number) => void;
}

// Models that accept the server-side refusal fallback ("default" routing) on the Claude API.
const CLAUDE_FALLBACK_MODELS = new Set(["claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"]);

// Error events the API can send mid-stream, after the HTTP 200, where the SDK's own retries no longer apply.
const RETRYABLE_STREAM_ERRORS = new Set(["overloaded_error", "api_error", "rate_limit_error", "timeout_error"]);

function createClaudeClient(config: FoundryConfig): Anthropic {
  return new Anthropic({
    apiKey: config.anthropicApiKey,
    // Authenticate with the configured key only, never ANTHROPIC_AUTH_TOKEN or a CLI profile.
    authToken: null,
    // Always explicit: left unset, the SDK reads ANTHROPIC_BASE_URL, which may hold the full /v1/messages endpoint.
    baseURL: config.anthropicBaseUrl,
    // On a streaming request this bounds the wait for response headers, not the whole generation.
    timeout: config.requestTimeoutMs,
    maxRetries: config.requestMaxRetries
  });
}

/**
 * The SDK already retries HTTP-level failures (408/409/429/5xx and connection
 * errors, honoring retry-after), so an HTTP or connection error that reaches the
 * outer loop is final. What is left to retry: transient error events mid-stream,
 * dropped stream bodies, empty replies and `parse` failures. Budget trips never are.
 */
function isRetryableClaudeError(err: unknown): boolean {
  if (err instanceof NonRetryableError) return false;
  if (err instanceof Error && err.name === "BudgetExceededError") return false;
  if (err instanceof Anthropic.APIUserAbortError || err instanceof Anthropic.APIConnectionError) return false;
  if (err instanceof Anthropic.APIError) {
    return err.status === undefined && (err.type === null || RETRYABLE_STREAM_ERRORS.has(err.type));
  }
  return true;
}

/**
 * With server-side fallbacks, top-level usage covers only the attempt that produced
 * the message; `usage.iterations` lists every billed attempt and the model that ran it.
 */
function recordClaudeUsage(economics: EconomicsEngine, requestedModel: string, message: Anthropic.Beta.BetaMessage): void {
  const iterations = message.usage.iterations ?? [];
  if (iterations.length === 0) {
    economics.recordUsage("claude", message.model || requestedModel, message.usage.input_tokens, message.usage.output_tokens);
    return;
  }
  for (const iteration of iterations) {
    const model = "model" in iteration && iteration.model ? iteration.model : requestedModel;
    economics.recordUsage("claude", model, iteration.input_tokens, iteration.output_tokens);
  }
}

export async function completeWithClaude<T = string>(
  config: FoundryConfig,
  economics: EconomicsEngine,
  request: CompletionRequest
): Promise<T> {
  if (!config.anthropicApiKey) {
    throw new NonRetryableError(`${request.label}: ANTHROPIC_API_KEY is not set`);
  }
  const model = config.anthropicModel;
  const useFallbacks =
    config.anthropicRefusalFallbacks &&
    CLAUDE_FALLBACK_MODELS.has(model) &&
    /^https:\/\/api\.anthropic\.com(\/|$)/.test(config.anthropicBaseUrl);
  const client = createClaudeClient(config);
  let attempt = 0;

  return withExponentialBackoff(
    request.label,
    async () => {
      // Runs before every outer attempt. The SDK's internal retries skip it, but they
      // only follow failed requests, which record no usage, so the outcome is unchanged.
      economics.enforceBudgetPreFlight("claude", model, `${request.system ?? ""}${request.prompt}`);
      request.onAttempt?.(++attempt);

      // Thinking models can run for minutes, so stream and let the SDK assemble the
      // final message. Sampling parameters (temperature/top_p) are rejected by current
      // Claude models, so branch diversity comes from the prompt rather than temperature.
      const stream = client.beta.messages.stream({
        model,
        max_tokens: config.anthropicMaxTokens,
        messages: [{ role: "user", content: request.prompt }],
        ...(request.system ? { system: request.system } : {}),
        // Thinking is always on for current Claude models; effort sets how deep it goes.
        ...(config.thinkingLevel ? { output_config: { effort: config.thinkingLevel } } : {}),
        ...(useFallbacks ? { betas: ["server-side-fallback-2026-07-01"], fallbacks: "default" as const } : {})
      });
      const onText = request.onText;
      if (onText) stream.on("text", (delta) => onText(delta));
      const message = await stream.finalMessage();

      // Thinking-enabled models put thinking blocks before the text, so collect every text block.
      const text = message.content
        .filter((block): block is Anthropic.Beta.BetaTextBlock => block.type === "text")
        .map((block) => block.text)
        .join("");
      recordClaudeUsage(economics, model, message);

      if (message.stop_reason === "refusal") {
        const category = message.stop_details?.category ?? "unspecified";
        throw new NonRetryableError(`${request.label}: Claude declined the request (category: ${category})`);
      }
      if (message.stop_reason === "max_tokens") {
        throw new NonRetryableError(
          `${request.label}: response truncated at max_tokens=${config.anthropicMaxTokens}; raise ANTHROPIC_MAX_TOKENS`
        );
      }
      if (!text.trim()) {
        throw new Error(`${request.label}: Claude returned no text content`);
      }
      return (request.parse ? request.parse(text) : text) as T;
    },
    { ...backoffOptions(config), isRetryable: isRetryableClaudeError }
  );
}

export async function completeWithGemini<T = string>(
  config: FoundryConfig,
  economics: EconomicsEngine,
  request: CompletionRequest
): Promise<T> {
  const model = config.geminiModel;
  return withExponentialBackoff(
    request.label,
    async () => {
      economics.enforceBudgetPreFlight("gemini", model, `${request.system ?? ""}${request.prompt}`);

      const generationConfig: Record<string, unknown> = { maxOutputTokens: config.geminiMaxOutputTokens };
      // Gemini 3 models are tuned for the default temperature; lowering it can cause looping.
      if (!/gemini-3/i.test(model)) generationConfig.temperature = 0.2;
      const thinkingConfig = geminiThinkingConfig(model, config.thinkingLevel);
      if (thinkingConfig) generationConfig.thinkingConfig = thinkingConfig;
      const body: Record<string, unknown> = {
        contents: [{ role: "user", parts: [{ text: request.prompt }] }],
        generationConfig
      };
      if (request.system) body.systemInstruction = { parts: [{ text: request.system }] };

      const data = await postJson<{
        candidates?: Array<{
          content?: { parts?: Array<{ text?: string; thought?: boolean }> };
          finishReason?: string;
        }>;
        promptFeedback?: { blockReason?: string };
        usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number; thoughtsTokenCount?: number };
      }>(
        request.label,
        `${config.geminiBaseUrl}/models/${model}:generateContent`,
        { "x-goog-api-key": config.geminiApiKey },
        body,
        config.requestTimeoutMs
      );

      const candidate = data.candidates?.[0];
      const text = (candidate?.content?.parts ?? [])
        .filter((part) => !part.thought && typeof part.text === "string")
        .map((part) => part.text)
        .join("");
      // Thinking tokens are billed as output tokens.
      const outTokens =
        (data.usageMetadata?.candidatesTokenCount ?? economics.estimateTokens(text)) +
        (data.usageMetadata?.thoughtsTokenCount ?? 0);
      economics.recordUsage(
        "gemini",
        model,
        data.usageMetadata?.promptTokenCount ?? economics.estimateTokens(request.prompt),
        outTokens
      );

      if (data.promptFeedback?.blockReason) {
        throw new NonRetryableError(`${request.label}: Gemini blocked the prompt (${data.promptFeedback.blockReason})`);
      }
      if (candidate?.finishReason === "MAX_TOKENS") {
        throw new NonRetryableError(
          `${request.label}: response truncated at maxOutputTokens=${config.geminiMaxOutputTokens}; raise GEMINI_MAX_OUTPUT_TOKENS`
        );
      }
      if (candidate?.finishReason && !["STOP", "FINISH_REASON_UNSPECIFIED"].includes(candidate.finishReason)) {
        throw new NonRetryableError(`${request.label}: Gemini stopped with finishReason ${candidate.finishReason}`);
      }
      if (!text.trim()) {
        throw new Error(`${request.label}: Gemini returned no text content`);
      }
      return (request.parse ? request.parse(text) : text) as T;
    },
    backoffOptions(config)
  );
}

function openAiSupportsTemperature(model: string): boolean {
  // Reasoning families (o-series, gpt-5*) only accept the default temperature.
  return /^(gpt-4|gpt-3\.5)/i.test(model);
}

export async function completeWithAstra<T = string>(
  config: FoundryConfig,
  economics: EconomicsEngine,
  request: CompletionRequest & { jsonObject?: boolean }
): Promise<T> {
  const model = config.astraModel;
  const exhausted = providerExhausted("astra");
  if (exhausted) throw new QuotaExhaustedError("astra", `${request.label}: ${exhausted}`);
  return withExponentialBackoff(
    request.label,
    async () => {
      economics.enforceBudgetPreFlight("astra", model, `${request.system ?? ""}${request.prompt}`);

      const messages: Array<{ role: string; content: string }> = [];
      if (request.system) messages.push({ role: "system", content: request.system });
      messages.push({ role: "user", content: request.prompt });
      const body: Record<string, unknown> = {
        model,
        messages,
        max_completion_tokens: config.astraMaxCompletionTokens
      };
      if (openAiSupportsTemperature(model)) body.temperature = 0.15;
      const effort = openAiReasoningEffort(model, config.thinkingLevel);
      if (effort) body.reasoning_effort = effort;
      if (request.jsonObject && /api\.openai\.com/.test(config.astraBaseUrl)) {
        body.response_format = { type: "json_object" };
      }

      let data: {
        choices?: Array<{ message?: { content?: string | null; refusal?: string | null }; finish_reason?: string }>;
        usage?: { prompt_tokens?: number; completion_tokens?: number };
      };
      try {
        data = await postJson(request.label, config.astraBaseUrl, { Authorization: `Bearer ${config.astraApiKey}` }, body, config.requestTimeoutMs);
      } catch (err) {
        if (err instanceof QuotaExhaustedError) {
          // Remember it for the rest of the run: every later call skips this provider at once.
          markProviderExhausted("astra", "the account has no credits remaining (HTTP 429 insufficient_quota); add credits, or pick another code writer in Settings");
        }
        throw err;
      }

      const choice = data.choices?.[0];
      const text = choice?.message?.content ?? "";
      economics.recordUsage(
        "astra",
        model,
        data.usage?.prompt_tokens ?? economics.estimateTokens(request.prompt),
        data.usage?.completion_tokens ?? economics.estimateTokens(text)
      );

      if (choice?.message?.refusal) {
        throw new NonRetryableError(`${request.label}: model refused (${choice.message.refusal.slice(0, 200)})`);
      }
      if (choice?.finish_reason === "length") {
        throw new NonRetryableError(
          `${request.label}: response truncated at max_completion_tokens=${config.astraMaxCompletionTokens}; raise ASTRA_MAX_TOKENS`
        );
      }
      if (!text.trim()) {
        throw new Error(`${request.label}: model returned no text content`);
      }
      return (request.parse ? request.parse(text) : text) as T;
    },
    backoffOptions(config)
  );
}

function findBalancedEnd(text: string, start: number): number {
  const stack: string[] = [];
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "[" || ch === "{") stack.push(ch === "[" ? "]" : "}");
    else if (ch === "]" || ch === "}") {
      if (stack.pop() !== ch) return -1;
      if (stack.length === 0) return i;
    }
  }
  return -1;
}

/**
 * Extracts the first parseable JSON value from model output: the whole text,
 * then fenced blocks, then the first balanced array/object found in prose.
 */
export function extractJson(raw: string): unknown {
  const candidates = [raw.trim()];
  for (const match of raw.matchAll(/```(?:json|JSON)?[^\S\n]*\n([\s\S]*?)```/g)) {
    candidates.push(match[1].trim());
  }
  for (const candidate of candidates) {
    try {
      return JSON.parse(candidate);
    } catch {
      // Try the next candidate.
    }
  }
  for (let i = 0; i < raw.length; i += 1) {
    if (raw[i] !== "[" && raw[i] !== "{") continue;
    const end = findBalancedEnd(raw, i);
    if (end < 0) continue;
    try {
      return JSON.parse(raw.slice(i, end + 1));
    } catch {
      // Keep scanning.
    }
  }
  throw new Error("Model output did not contain parseable JSON");
}

/** Accepts `[...]`, `{ "<key>": [...] }`, or a single object, and returns the array of items. */
export function extractJsonArray(raw: string, key: string): unknown[] {
  const value = extractJson(raw);
  if (Array.isArray(value)) return value;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (Array.isArray(record[key])) return record[key] as unknown[];
    const arrays = Object.values(record).filter(Array.isArray);
    if (arrays.length === 1) return arrays[0] as unknown[];
    return [value];
  }
  throw new Error("Model output JSON was not an array or object");
}
