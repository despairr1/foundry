import { describe, it, expect, afterEach } from "vitest";
import { resolvePricing } from "../src/engine/economics";
import { defaultOutputTokens, loadConfig, parseThinkingLevel } from "../src/config";
import { geminiThinkingConfig, openAiReasoningEffort } from "../src/swarm/providers";

const saved = { gemini: process.env.GEMINI_MAX_OUTPUT_TOKENS, astra: process.env.ASTRA_MAX_TOKENS };

afterEach(() => {
  for (const [name, value] of [["GEMINI_MAX_OUTPUT_TOKENS", saved.gemini], ["ASTRA_MAX_TOKENS", saved.astra]] as const) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
});

describe("thinking level", () => {
  it("reads friendly names and ignores bad values instead of failing the run", () => {
    expect(parseThinkingLevel("High")).toBe("high");
    expect(parseThinkingLevel("extra high")).toBe("xhigh");
    expect(parseThinkingLevel(" MAX ")).toBe("max");
    expect(parseThinkingLevel("turbo")).toBeUndefined();
    expect(parseThinkingLevel(undefined)).toBeUndefined();
  });

  it("maps each level to what Gemini and OpenAI models accept", () => {
    expect(geminiThinkingConfig("gemini-3.1-pro-preview", "medium")).toEqual({ thinkingLevel: "medium" });
    expect(geminiThinkingConfig("gemini-3.1-pro-preview", "max")).toEqual({ thinkingLevel: "high" });
    expect(geminiThinkingConfig("gemini-3-pro-preview", "medium")).toEqual({ thinkingLevel: "high" });
    expect(geminiThinkingConfig("gemini-2.0-flash", "high")).toBeUndefined();
    expect(geminiThinkingConfig("gemini-3.1-pro-preview", undefined)).toBeUndefined();
    expect(openAiReasoningEffort("gpt-5", "low")).toBe("low");
    expect(openAiReasoningEffort("gpt-5", "max")).toBe("high");
    expect(openAiReasoningEffort("gpt-5.5", "xhigh")).toBe("xhigh");
    expect(openAiReasoningEffort("o3", "medium")).toBe("medium");
    expect(openAiReasoningEffort("gpt-4o", "high")).toBeUndefined();
  });
});

describe("custom synthesizer pricing", () => {
  afterEach(() => {
    delete process.env.ASTRA_INPUT_PRICE_PER_M;
    delete process.env.ASTRA_OUTPUT_PRICE_PER_M;
  });

  it("prices an unknown OpenAI-compatible model by the settings instead of the most expensive tier", () => {
    expect(resolvePricing("astra", "deepseek-chat").inputCostPerMillionUsd).toBe(15);
    process.env.ASTRA_INPUT_PRICE_PER_M = "0.27";
    process.env.ASTRA_OUTPUT_PRICE_PER_M = "1.1";
    expect(resolvePricing("astra", "deepseek-chat")).toEqual({ inputCostPerMillionUsd: 0.27, outputCostPerMillionUsd: 1.1 });
    expect(resolvePricing("claude", "claude-haiku-5-5").inputCostPerMillionUsd).toBe(0.1); // other providers are untouched
    process.env.ASTRA_INPUT_PRICE_PER_M = "not a number";
    expect(resolvePricing("astra", "deepseek-chat").inputCostPerMillionUsd).toBe(15);
  });
});

describe("output token caps", () => {
  it("gives thinking models their full output window, since thinking counts against the cap", () => {
    for (const model of ["gemini-3.1-pro-preview", "gemini-2.5-flash", "models/gemini-3-pro", "gpt-5", "gpt-5-mini", "o3", "o4-mini"]) {
      expect(defaultOutputTokens(model, 16384), model).toBe(65536);
    }
    for (const model of ["gemini-2.0-flash", "gpt-4o", "gpt-4.1", "deepseek-coder"]) {
      expect(defaultOutputTokens(model, 16384), model).toBe(16384);
    }
  });

  it("applies the model-aware cap only when none is set", () => {
    delete process.env.GEMINI_MAX_OUTPUT_TOKENS;
    delete process.env.ASTRA_MAX_TOKENS;
    const auto = loadConfig({ geminiModel: "gemini-3.1-pro-preview", astraModel: "gpt-5" });
    expect(auto.geminiMaxOutputTokens).toBe(65536);
    expect(auto.astraMaxCompletionTokens).toBe(65536);
    expect(loadConfig({ geminiModel: "gemini-2.0-flash", astraModel: "gpt-4o" }).geminiMaxOutputTokens).toBe(16384);

    process.env.GEMINI_MAX_OUTPUT_TOKENS = "20000";
    expect(loadConfig({ geminiModel: "gemini-3.1-pro-preview" }).geminiMaxOutputTokens).toBe(20000);
    expect(loadConfig({ astraModel: "gpt-5", astraMaxCompletionTokens: 9000 }).astraMaxCompletionTokens).toBe(9000);
  });
});
