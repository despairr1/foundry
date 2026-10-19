import { describe, it, expect, afterEach } from "vitest";
import { loadConfig } from "../src/config";

const KEYS = ["OPENAI_API_KEY", "ASTRA_API_KEY", "OPENAI_MODEL", "ASTRA_MODEL", "FOUNDRY_SYNTHESIZER"] as const;
const saved = Object.fromEntries(KEYS.map((k) => [k, process.env[k]]));

function setEnv(values: Partial<Record<(typeof KEYS)[number], string>>): void {
  for (const k of KEYS) {
    if (values[k] === undefined) delete process.env[k];
    else process.env[k] = values[k];
  }
}

describe("which key the code writer uses", () => {
  afterEach(() => {
    for (const k of KEYS) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  });

  it("uses the OpenAI key saved in Settings, not an older ASTRA key, while OpenAI writes the code", () => {
    setEnv({ OPENAI_API_KEY: "sk-new", ASTRA_API_KEY: "sk-old", OPENAI_MODEL: "gpt-5", ASTRA_MODEL: "gpt-4o", FOUNDRY_SYNTHESIZER: "openai" });
    expect(loadConfig().astraApiKey).toBe("sk-new");
    expect(loadConfig().astraModel).toBe("gpt-5");
    setEnv({ OPENAI_API_KEY: "sk-new", ASTRA_API_KEY: "sk-old" }); // no choice recorded: OpenAI is the default
    expect(loadConfig().astraApiKey).toBe("sk-new");
  });

  it("uses the ASTRA settings when a custom provider writes the code", () => {
    setEnv({ OPENAI_API_KEY: "sk-openai", ASTRA_API_KEY: "sk-deepseek", ASTRA_MODEL: "deepseek-chat", FOUNDRY_SYNTHESIZER: "deepseek" });
    expect(loadConfig().astraApiKey).toBe("sk-deepseek");
    expect(loadConfig().astraModel).toBe("deepseek-chat");
  });

  it("falls back to whichever key exists", () => {
    setEnv({ ASTRA_API_KEY: "sk-only-astra", FOUNDRY_SYNTHESIZER: "openai" });
    expect(loadConfig().astraApiKey).toBe("sk-only-astra");
  });
});
