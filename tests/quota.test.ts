import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import * as os from "os";
import * as path from "path";
import { loadConfig } from "../src/config";
import { EconomicsEngine } from "../src/engine/economics";
import { completeWithAstra, providerExhausted, QuotaExhaustedError, resetProviderState } from "../src/swarm/providers";

const quotaBody = JSON.stringify({
  error: { message: "You have no credits remaining. Add credits to continue using the API.", type: "insufficient_quota", code: "insufficient_quota" }
});

describe("an out-of-credits code writer", () => {
  const realFetch = global.fetch;
  let calls = 0;

  beforeEach(() => {
    resetProviderState();
    calls = 0;
    global.fetch = vi.fn(async () => {
      calls += 1;
      return new Response(quotaBody, { status: 429, headers: { "content-type": "application/json" } });
    }) as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = realFetch;
    resetProviderState();
  });

  it("fails once without retrying, then skips the provider for the rest of the run", async () => {
    const config = loadConfig({ offlineMode: false, astraApiKey: "sk-test", astraModel: "gpt-5", requestMaxRetries: 3 });
    const economics = new EconomicsEngine(5, path.join(os.tmpdir(), "foundry-quota-freeze.json"), false);
    const request = { label: "AstraSynthesizer[Alpha]", prompt: "write code" };
    await expect(completeWithAstra(config, economics, request)).rejects.toBeInstanceOf(QuotaExhaustedError);
    expect(calls).toBe(1); // a 429 with insufficient_quota is not retried
    expect(providerExhausted("astra")).toMatch(/no credits/);
    await expect(completeWithAstra(config, economics, { ...request, label: "AstraSynthesizer[Beta]" })).rejects.toThrow(/no credits remaining/);
    expect(calls).toBe(1); // the second call never reached the network
    expect(economics.getTotalSpentUsd()).toBe(0);
  });

  it("still retries an ordinary 429 (rate limit)", async () => {
    global.fetch = vi.fn(async () => {
      calls += 1;
      return new Response(JSON.stringify({ error: { message: "Rate limit reached", type: "rate_limit_error" } }), { status: 429 });
    }) as unknown as typeof fetch;
    const config = loadConfig({ offlineMode: false, astraApiKey: "sk-test", astraModel: "gpt-5", requestMaxRetries: 1 });
    const economics = new EconomicsEngine(5, path.join(os.tmpdir(), "foundry-quota-freeze.json"), false);
    await expect(completeWithAstra(config, economics, { label: "AstraSynthesizer[Alpha]", prompt: "x" })).rejects.toThrow(/HTTP 429/);
    expect(calls).toBe(2);
    expect(providerExhausted("astra")).toBeUndefined();
  }, 30000);
});
