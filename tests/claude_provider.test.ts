import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import Anthropic from "@anthropic-ai/sdk";
import { FoundryConfig, loadConfig } from "../src/config";
import { BudgetExceededError, EconomicsEngine } from "../src/engine/economics";
import { NonRetryableError } from "../src/utils/logger";
import { completeWithClaude } from "../src/swarm/providers";

// Every test stubs global fetch and uses a fake key, so nothing reaches the real API.
const FAKE_KEY = "sk-ant-test-not-a-real-key";

interface CapturedRequest {
  url: string;
  headers: Headers;
  body: Record<string, unknown>;
}

let requests: CapturedRequest[];
let responses: Array<() => Response>;
let freezeFile: string;

beforeEach(() => {
  requests = [];
  responses = [];
  freezeFile = path.join(os.tmpdir(), `foundry-claude-${process.pid}-${Date.now()}.json`);
  vi.stubGlobal("fetch", async (url: string | URL | Request, init?: RequestInit) => {
    requests.push({
      url: String(url),
      headers: new Headers(init?.headers),
      body: JSON.parse(String(init?.body ?? "{}"))
    });
    const next = responses.shift();
    if (!next) throw new Error("test made more requests than it queued responses for");
    return next();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  fs.rmSync(freezeFile, { force: true });
});

function testConfig(overrides: Partial<FoundryConfig> = {}): FoundryConfig {
  return loadConfig({
    anthropicApiKey: FAKE_KEY,
    anthropicModel: "claude-opus-4-8",
    anthropicBaseUrl: "https://claude.test/v1/messages",
    anthropicMaxTokens: 1000,
    anthropicRefusalFallbacks: true,
    requestTimeoutMs: 5000,
    requestMaxRetries: 1,
    ...overrides
  });
}

interface StreamSpec {
  model?: string;
  blocks?: Array<{ type: "thinking" | "text" | "fallback"; text?: string; from?: string; to?: string }>;
  stopReason?: string;
  stopDetails?: Record<string, unknown> | null;
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheWriteTokens?: number;
  iterations?: unknown[];
  errorAfterStart?: string;
}

// The SDK's beta namespace adds ?beta=true to the request URL.
function endpoint(url: string): string {
  const parsed = new URL(url);
  return `${parsed.origin}${parsed.pathname}`;
}

function sseEvent(data: { type: string }): string {
  return `event: ${data.type}\ndata: ${JSON.stringify(data)}\n\n`;
}

function streamResponse(spec: StreamSpec): () => Response {
  const events: string[] = [
    sseEvent({
      type: "message_start",
      message: {
        id: "msg_test",
        type: "message",
        role: "assistant",
        model: spec.model ?? "claude-opus-4-8",
        content: [],
        stop_reason: null,
        stop_sequence: null,
        stop_details: null,
        usage: {
          input_tokens: spec.inputTokens ?? 100,
          output_tokens: 1,
          cache_creation_input_tokens: spec.cacheWriteTokens ?? 0,
          cache_read_input_tokens: spec.cacheReadTokens ?? 0
        }
      }
    } as { type: string })
  ];
  if (spec.errorAfterStart) {
    events.push(sseEvent({ type: "error", error: { type: spec.errorAfterStart, message: "Overloaded" } } as { type: string }));
  } else {
    (spec.blocks ?? []).forEach((block, index) => {
      if (block.type === "fallback") {
        events.push(sseEvent({ type: "content_block_start", index, content_block: { type: "fallback", from: { model: block.from }, to: { model: block.to } } } as { type: string }));
      } else if (block.type === "thinking") {
        events.push(sseEvent({ type: "content_block_start", index, content_block: { type: "thinking", thinking: "", signature: "" } } as { type: string }));
        events.push(sseEvent({ type: "content_block_delta", index, delta: { type: "signature_delta", signature: "sig" } } as { type: string }));
      } else {
        events.push(sseEvent({ type: "content_block_start", index, content_block: { type: "text", text: "" } } as { type: string }));
        events.push(sseEvent({ type: "content_block_delta", index, delta: { type: "text_delta", text: block.text ?? "" } } as { type: string }));
      }
      events.push(sseEvent({ type: "content_block_stop", index } as { type: string }));
    });
    events.push(
      sseEvent({
        type: "message_delta",
        delta: { stop_reason: spec.stopReason ?? "end_turn", stop_sequence: null, stop_details: spec.stopDetails ?? null },
        usage: { output_tokens: spec.outputTokens ?? 20, ...(spec.iterations ? { iterations: spec.iterations } : {}) }
      } as { type: string })
    );
    events.push(sseEvent({ type: "message_stop" }));
  }
  return () => new Response(events.join(""), { status: 200, headers: { "content-type": "text/event-stream" } });
}

function errorResponse(status: number, type: string): () => Response {
  return () =>
    new Response(JSON.stringify({ type: "error", error: { type, message: type } }), {
      status,
      headers: { "content-type": "application/json", "retry-after-ms": "1" }
    });
}

describe("Claude provider (SDK streaming)", () => {
  it("streams the reply, joins every text block after the thinking block, and records usage", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(streamResponse({ blocks: [{ type: "thinking" }, { type: "text", text: "Hello " }, { type: "text", text: "world" }], outputTokens: 42 }));

    const text = await completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi", system: "be brief" });

    expect(text).toBe("Hello world");
    expect(requests).toHaveLength(1);
    // A full-endpoint ANTHROPIC_BASE_URL still reaches the same endpoint through the SDK.
    expect(endpoint(requests[0].url)).toBe("https://claude.test/v1/messages");
    expect(requests[0].headers.get("x-api-key")).toBe(FAKE_KEY);
    expect(requests[0].headers.get("anthropic-beta")).toBeNull();
    expect(requests[0].body).toMatchObject({ model: "claude-opus-4-8", max_tokens: 1000, stream: true, system: "be brief" });
    expect(requests[0].body.fallbacks).toBeUndefined();
    expect(economics.getLedger().map((e) => [e.model, e.promptTokens, e.completionTokens])).toEqual([["claude-opus-4-8", 100, 42]]);
  });

  it("opts into server-side fallbacks on the Claude API and records every billed attempt", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(
      streamResponse({
        model: "claude-opus-4-8",
        blocks: [{ type: "fallback", from: "claude-fable-5-1", to: "claude-opus-4-8" }, { type: "text", text: "rescued" }],
        iterations: [
          { type: "message", model: "claude-fable-5-1", input_tokens: 50, output_tokens: 10, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, cache_creation: null },
          { type: "fallback_message", model: "claude-opus-4-8", input_tokens: 50, output_tokens: 30, cache_creation_input_tokens: 0, cache_read_input_tokens: 0, cache_creation: null }
        ]
      })
    );

    const config = testConfig({ anthropicModel: "claude-fable-5-1", anthropicBaseUrl: "https://api.anthropic.com" });
    const text = await completeWithClaude(config, economics, { label: "t", prompt: "hi" });

    expect(text).toBe("rescued");
    expect(endpoint(requests[0].url)).toBe("https://api.anthropic.com/v1/messages");
    expect(requests[0].headers.get("anthropic-beta")).toContain("server-side-fallback-2026-07-01");
    expect(requests[0].body.fallbacks).toBe("default");
    expect(requests[0].body.betas).toBeUndefined();
    expect(economics.getLedger().map((e) => [e.model, e.promptTokens, e.completionTokens])).toEqual([
      ["claude-fable-5-1", 50, 10],
      ["claude-opus-4-8", 50, 30]
    ]);
  });

  it("does not send fallbacks to a proxy or for models without them", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(streamResponse({ blocks: [{ type: "text", text: "ok" }] }));
    await completeWithClaude(testConfig({ anthropicModel: "claude-fable-5-1" }), economics, { label: "t", prompt: "hi" });
    expect(requests[0].body.fallbacks).toBeUndefined();
    expect(requests[0].headers.get("anthropic-beta")).toBeNull();
  });

  it("treats refusal and max_tokens as final", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(streamResponse({ stopReason: "refusal", stopDetails: { type: "refusal", category: "cyber", explanation: null } }));
    const refusal = completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi" });
    await expect(refusal).rejects.toBeInstanceOf(NonRetryableError);
    await expect(refusal).rejects.toThrow("category: cyber");
    expect(requests).toHaveLength(1);

    responses.push(streamResponse({ blocks: [{ type: "text", text: "partial" }], stopReason: "max_tokens" }));
    await expect(completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi" })).rejects.toThrow("max_tokens=1000");
    expect(requests).toHaveLength(2);
    // Both attempts were billed, so both are on the ledger.
    expect(economics.getLedger()).toHaveLength(2);
  });

  it("leaves HTTP retries to the SDK and surfaces its typed errors without retrying them again", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(errorResponse(400, "invalid_request_error"));
    await expect(completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi" })).rejects.toBeInstanceOf(
      Anthropic.BadRequestError
    );
    expect(requests).toHaveLength(1);

    // requestMaxRetries=1: the SDK makes one retry, and the outer loop adds none.
    requests = [];
    responses.push(errorResponse(529, "overloaded_error"), errorResponse(529, "overloaded_error"));
    const overloaded = completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi" });
    await expect(overloaded).rejects.toBeInstanceOf(Anthropic.APIError);
    await expect(overloaded).rejects.toMatchObject({ status: 529 });
    expect(requests).toHaveLength(2);
    expect(economics.getLedger()).toHaveLength(0);
  });

  it("retries an error event that arrives mid-stream, re-running the budget pre-flight", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    const preFlight = vi.spyOn(economics, "enforceBudgetPreFlight");
    responses.push(streamResponse({ errorAfterStart: "overloaded_error" }), streamResponse({ blocks: [{ type: "text", text: "second try" }] }));

    const text = await completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi" });

    expect(text).toBe("second try");
    expect(requests).toHaveLength(2);
    expect(preFlight).toHaveBeenCalledTimes(2);
  });

  it("sends the thinking level as effort, and nothing when it isn't set", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(streamResponse({ blocks: [{ type: "text", text: "a" }] }), streamResponse({ blocks: [{ type: "text", text: "b" }] }));

    await completeWithClaude(testConfig({ thinkingLevel: "xhigh" }), economics, { label: "t", prompt: "hi" });
    // The user's own FOUNDRY_THINKING (from .env) must not leak into "not set".
    await completeWithClaude({ ...testConfig(), thinkingLevel: undefined }, economics, { label: "t", prompt: "hi" });

    expect(requests[0].body.output_config).toEqual({ effort: "xhigh" });
    expect(requests[0].body.thinking).toBeUndefined(); // thinking is always on; only its depth is set
    expect(requests[1].body.output_config).toBeUndefined();
  });

  it("hands streamed text to onText and marks each attempt, so a failed attempt's text can be dropped", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(
      streamResponse({ errorAfterStart: "overloaded_error" }),
      streamResponse({ blocks: [{ type: "thinking" }, { type: "text", text: "Hello " }, { type: "text", text: "there" }] })
    );
    const seen: string[] = [];

    const text = await completeWithClaude(testConfig(), economics, {
      label: "t",
      prompt: "hi",
      onAttempt: (n) => seen.push(`attempt ${n}`),
      onText: (delta) => seen.push(delta)
    });

    expect(text).toBe("Hello there");
    expect(seen).toEqual(["attempt 1", "attempt 2", "Hello ", "there"]);
  });

  it("never retries a budget trip, before or after the request", async () => {
    const broke = new EconomicsEngine(0.01, freezeFile, false);
    await expect(completeWithClaude(testConfig(), broke, { label: "t", prompt: "hi" })).rejects.toBeInstanceOf(BudgetExceededError);
    expect(requests).toHaveLength(0);

    // Pre-flight projects ~$0.10 (4096 output tokens at $25/M), so it passes; the recorded 10k output tokens ($0.25) trip it.
    const tight = new EconomicsEngine(0.2, freezeFile, false);
    responses.push(streamResponse({ blocks: [{ type: "text", text: "expensive" }], outputTokens: 10_000 }));
    await expect(completeWithClaude(testConfig(), tight, { label: "t", prompt: "hi" })).rejects.toBeInstanceOf(BudgetExceededError);
    expect(requests).toHaveLength(1);
  });

  it("marks a long system prompt as a cached prefix and bills cache reads at a tenth of the input price", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(streamResponse({ blocks: [{ type: "text", text: "ok" }], inputTokens: 200, outputTokens: 10, cacheReadTokens: 5000, cacheWriteTokens: 0 }));
    const longSystem = "The contract, harness and tests. ".repeat(400); // well over the 1,024-token minimum

    await completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi", system: longSystem });

    expect(requests[0].body.system).toEqual([{ type: "text", text: longSystem, cache_control: { type: "ephemeral" } }]);
    const [entry] = economics.getLedger();
    expect([entry.promptTokens, entry.cacheReadTokens, entry.cacheWriteTokens, entry.completionTokens]).toEqual([200, 5000, 0, 10]);
    // claude-opus-4-8: $5/M in, $25/M out. 200 fresh + 5000 at 10% = 700 token-equivalents.
    expect(entry.costUsd).toBeCloseTo((700 / 1_000_000) * 5 + (10 / 1_000_000) * 25, 10);
    expect(economics.getTokenTotals()).toEqual({ inputTokens: 5200, outputTokens: 10, cachedTokens: 5000 });
  });

  it("sends a short system prompt as plain text, and every system prompt as text with caching off", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    responses.push(streamResponse({ blocks: [{ type: "text", text: "a" }] }), streamResponse({ blocks: [{ type: "text", text: "b" }] }));
    const longSystem = "The contract, harness and tests. ".repeat(400);

    await completeWithClaude(testConfig(), economics, { label: "t", prompt: "hi", system: "be brief" });
    await completeWithClaude(testConfig({ promptCaching: false }), economics, { label: "t", prompt: "hi", system: longSystem });

    expect(requests[0].body.system).toBe("be brief");
    expect(requests[1].body.system).toBe(longSystem);
  });

  it("fails fast without an API key", async () => {
    const economics = new EconomicsEngine(100, freezeFile, false);
    await expect(completeWithClaude(testConfig({ anthropicApiKey: "" }), economics, { label: "t", prompt: "hi" })).rejects.toThrow(
      "ANTHROPIC_API_KEY is not set"
    );
    expect(requests).toHaveLength(0);
  });
});
