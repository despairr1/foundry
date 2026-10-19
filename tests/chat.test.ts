import { describe, it, expect } from "vitest";
import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { loadConfig } from "../src/config";
import { EconomicsEngine } from "../src/engine/economics";
import { chatSystemPrompt, composeChatPrompt, describeFoundryPipeline, formatTranscript, offlineChatReply, replyToChat } from "../src/swarm/chat";

const conversation = [
  { role: "user" as const, text: "A to-do list app for the command line" },
  { role: "assistant" as const, agent: "claude" as const, text: "Should tasks have due dates?" },
  { role: "user" as const, text: "Yes, and sort by them" }
];

describe("project chat", () => {
  it("renders the conversation with who said what, newest last", () => {
    const transcript = formatTranscript(conversation);
    expect(transcript).toBe(
      "User: A to-do list app for the command line\n\nClaude (architect): Should tasks have due dates?\n\nUser: Yes, and sort by them"
    );
  });

  it("keeps the newest messages when the conversation is too long", () => {
    const long = Array.from({ length: 50 }, (_, i) => ({ role: "user" as const, text: `message ${i} ${"x".repeat(100)}` }));
    const transcript = formatTranscript(long, 1000);
    expect(transcript.startsWith("[Earlier messages left out]")).toBe(true);
    expect(transcript).toContain("message 49");
    expect(transcript).not.toContain("message 0 ");
  });

  it("gives each model its role and the project's context", () => {
    expect(chatSystemPrompt("gemini")).toContain("You are Gemini, the auditor.");
    expect(chatSystemPrompt("claude")).toContain("press Build");
    const prompt = composeChatPrompt("openai", conversation, "A new project; its folder (C:\\p) has no code yet.");
    expect(prompt).toContain("About the project:\nA new project");
    expect(prompt).toContain("Reply as OpenAI (synthesizer)");
    expect(prompt.indexOf("Yes, and sort by them")).toBeGreaterThan(prompt.indexOf("Should tasks have due dates?"));
  });

  it("tells the models how to propose a command, for the user's shell", () => {
    expect(chatSystemPrompt("claude", "win32")).toContain("```run\nnpm test\n```");
    expect(chatSystemPrompt("claude", "win32")).toContain("Windows PowerShell");
    expect(chatSystemPrompt("openai", "linux")).toContain("bash");
    expect(chatSystemPrompt("gemini")).toContain("only after the user clicks Run");
  });

  it("lets only the architect start builds", () => {
    expect(chatSystemPrompt("claude")).toContain("```build");
    expect(chatSystemPrompt("gemini")).not.toContain("```build");
    const go = [{ role: "user" as const, text: "A weather app" }, { role: "assistant" as const, text: "Plan..." }, { role: "user" as const, text: "ok, build it" }];
    expect(offlineChatReply("claude", go)).toContain("```build\nA weather app\n");
    expect(offlineChatReply("gemini", go)).not.toContain("```build");
  });

  it("has a prompt-studio mode where the product is a prompt, not a build", () => {
    const studio = chatSystemPrompt("claude", "win32", "prompt");
    expect(studio).toContain("prompt studio");
    expect(studio).toContain("```prompt");
    expect(studio).toContain("memory");
    expect(studio).not.toContain("```build");
    expect(studio).not.toContain("```run");
    expect(chatSystemPrompt("gemini", "win32", "prompt")).toContain("critic");
    const ask = [{ role: "user" as const, text: "A prompt that summarizes meeting notes" }];
    expect(offlineChatReply("claude", ask, "prompt")).toContain("```prompt\n");
    expect(offlineChatReply("claude", ask, "prompt")).not.toContain("```build");
  });

  it("has a job mode whose planner knows how Foundry builds and answers with a foundry plan", () => {
    const planner = chatSystemPrompt("claude", "win32", "job");
    expect(planner).toContain("```foundry");
    expect(planner).toContain("ONE implementation file plus ONE test file");
    expect(planner).toContain("mutation testing");
    expect(planner).toContain("12,000 characters");
    expect(planner).not.toContain("```build");
    expect(chatSystemPrompt("gemini", "win32", "job")).toContain("reviewer");
    expect(describeFoundryPipeline({ maxConvergenceIterations: 4, minMutationKillRatio: 0.9 })).toContain("at least 90%");
    const ask = [{ role: "user" as const, text: "A command-line weather app" }];
    const reply = offlineChatReply("claude", ask, "job");
    const block = /```foundry\n([\s\S]*?)```/.exec(reply);
    expect(block).not.toBeNull();
    const plan = JSON.parse(block![1]);
    expect(plan.steps).toHaveLength(2);
    expect(plan.steps[0].build.startsWith("Core logic for: A command-line weather app")).toBe(true);
    expect(["low", "medium", "high", "xhigh", "max"]).toContain(plan.steps[0].thinking);
  });

  it("proposes and acknowledges commands in offline demos", () => {
    const ask = [{ role: "user" as const, text: "run: npm test" }];
    expect(offlineChatReply("claude", ask)).toContain("```run\nnpm test\n```");
    const result = [...ask, { role: "assistant" as const, text: "..." }, { role: "user" as const, text: "I ran `npm test` in the project folder. Exit code 1. Output:\n```\nfail\n```" }];
    expect(offlineChatReply("claude", result)).toBe("Offline demo reply. The command finished with exit code 1.");
  });

  it("answers offline without calling any API", async () => {
    const config = loadConfig({ offlineMode: true, anthropicApiKey: "", geminiApiKey: "", astraApiKey: "" });
    const economics = new EconomicsEngine(1, path.join(os.tmpdir(), "foundry-chat-freeze.json"), false);
    for (const agent of ["claude", "gemini", "openai"] as const) {
      const reply = await replyToChat(config, economics, agent, conversation);
      expect(reply).toEqual({ text: offlineChatReply(agent, conversation), model: "offline" });
      expect(reply.text).toContain('"Yes, and sort by them"');
    }
    expect(economics.getTotalSpentUsd()).toBe(0);
  });

  it("refuses a model whose key is missing instead of calling it", async () => {
    const config = loadConfig({ offlineMode: false, anthropicApiKey: "", geminiApiKey: "", astraApiKey: "" });
    const economics = new EconomicsEngine(1, path.join(os.tmpdir(), "foundry-chat-freeze.json"), false);
    await expect(replyToChat(config, economics, "gemini", conversation)).rejects.toThrow("No Gemini key");
    await expect(replyToChat(config, economics, "openai", conversation)).rejects.toThrow("No Claude key");
  });
});

const dist = path.resolve(__dirname, "..", "dist", "index.js");

describe.skipIf(!fs.existsSync(dist))("foundry chat command (offline)", () => {
  it("prints one reply per model with --agent all, each seeing the earlier ones", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-chat-"));
    try {
      const file = path.join(dir, "chat.json");
      fs.writeFileSync(file, JSON.stringify({ messages: conversation }));
      const run = spawnSync(process.execPath, [dist, "chat", "--conversation", file, "--agent", "all", "--offline", "--progress", "--target", dir], {
        encoding: "utf8",
        env: { ...process.env, FOUNDRY_LOG_PATH: path.join(dir, "engine.log"), FOUNDRY_FREEZE_PATH: path.join(dir, "freeze.json") },
        timeout: 60000
      });
      const events = run.stdout
        .split(/\r?\n/)
        .filter((l) => l.startsWith("@@FOUNDRY "))
        .map((l) => JSON.parse(l.slice("@@FOUNDRY ".length)));
      expect(run.status).toBe(0);
      expect(events.filter((e) => e.type === "chat-reply").map((e) => e.agent)).toEqual(["claude", "gemini", "openai"]);
      // Claude's reply streams in first, piece by piece, and the pieces add up to the reply.
      const deltas = events.filter((e) => e.type === "chat-delta");
      expect(deltas.length).toBeGreaterThan(3);
      expect(deltas.every((e) => e.agent === "claude" && e.attempt === 1)).toBe(true);
      expect(deltas.map((e) => e.text).join("")).toBe(events.find((e) => e.type === "chat-reply" && e.agent === "claude")?.text);
      expect(events.at(-1)).toMatchObject({ type: "finished", outcome: "done", spentUsd: 0 });
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  it("rejects a conversation that doesn't end with the user", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-chat-"));
    try {
      const file = path.join(dir, "chat.json");
      fs.writeFileSync(file, JSON.stringify({ messages: conversation.slice(0, 2) }));
      const run = spawnSync(process.execPath, [dist, "chat", "--conversation", file, "--offline", "--progress"], {
        encoding: "utf8",
        env: { ...process.env, FOUNDRY_LOG_PATH: path.join(dir, "engine.log") },
        timeout: 60000
      });
      expect(run.status).toBe(1);
      expect(run.stdout).toContain("must end with a message from the user");
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
