import { FoundryConfig } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { completeWithAstra, completeWithClaude, completeWithGemini, CompletionRequest } from "./providers";

/**
 * The project chat: the user talks with the three models about what Foundry should build or change,
 * before pressing Build. Each reply is one completion over the whole conversation (rendered as a
 * transcript), so all three providers work the same way and earlier replies stay in view.
 *
 * The same machinery serves the prompt studio ("prompt" mode): a conversation whose product is a
 * prompt the user takes elsewhere, with the conversation itself as the prompt's memory.
 */
export type ChatAgent = "claude" | "gemini" | "openai";
export const CHAT_AGENTS: ChatAgent[] = ["claude", "gemini", "openai"];

export type ChatMode = "project" | "prompt" | "job";
export const CHAT_MODES: ChatMode[] = ["project", "prompt", "job"];

export interface ChatMessage {
  role: "user" | "assistant";
  agent?: ChatAgent;
  text: string;
}

export const AGENT_LABELS: Record<ChatAgent, string> = {
  claude: "Claude (architect)",
  gemini: "Gemini (auditor)",
  openai: "OpenAI (synthesizer)"
};

const SHARED_SYSTEM =
  "You are part of Foundry, an autonomous software engineering engine run by three AI models: Claude (the architect, " +
  "who designs the plan), Gemini (the auditor, who looks for edge cases and decides what must be tested) and OpenAI " +
  "(the synthesizer, who writes the code). In this chat the user decides what Foundry should build or change in their " +
  "project. When they press Build, Foundry plans, tests and writes the code by itself, so do not write the whole " +
  "program here; a short snippet is fine when it helps. The user may not be a programmer: keep replies short, " +
  "friendly and in plain English, use short paragraphs or bullet points, and avoid jargon.";

const ROLE_SYSTEM: Record<ChatAgent, string> = {
  claude:
    "You are Claude, the architect. Help the user pin down what they want. If something important is unclear, ask at " +
    "most two short questions; otherwise propose a short, concrete plan: what it does, how it is used, its main parts, " +
    "and the language if that matters. When the plan is clear, tell them they can press Build.",
  gemini:
    "You are Gemini, the auditor. Point out what could go wrong or is still unclear: edge cases, bad input, data loss, " +
    "security or privacy risks, and what Foundry's tests should check. Be brief and practical, and skip anything the " +
    "conversation already covers.",
  openai:
    "You are the synthesizer. Suggest how the code should be built: its structure, files, language and libraries, and " +
    "anything that makes it simpler to build and run. Be brief and practical, and skip anything the conversation already covers."
};

const PROMPT_SHARED =
  "You are part of Foundry's prompt studio, where the user writes and refines prompts for AI models: Claude, GPT, " +
  "Gemini, image or voice models, coding agents, or Foundry's own project chat. The prompt is the product: the user " +
  "copies it out and uses it elsewhere, so it must stand on its own. This conversation is the prompt's memory: " +
  "everything the user said here still applies, so build on it instead of asking again. Keep replies short and in " +
  "plain English, use short paragraphs or bullet points, and avoid jargon.";

const PROMPT_ROLE: Record<ChatAgent, string> = {
  claude:
    "You are Claude, the lead prompt writer. First make sure you know what the prompt is for: which model or tool " +
    "reads it, what it must produce, for whom, in what format, under what constraints, and whether examples exist. " +
    "If something important is missing, ask at most two short questions; otherwise write the prompt. Put the whole " +
    "prompt alone in a code block marked prompt, for example\n```prompt\nYou are ...\n```\n" +
    "Use what works: a clear role, the context the model needs, the task in plain steps, the exact output format, " +
    "the constraints, and one or two examples when they help. After the block, say in two or three lines what you " +
    "changed or why it is shaped that way. When the user asks for changes, rewrite the whole prompt in a new prompt block.",
  gemini:
    "You are Gemini, the critic. Read the latest prompt and point out what a model could misread: ambiguous wording, " +
    "missing context, formats left unspecified, conflicting instructions, and inputs it would handle badly. Suggest " +
    "precise fixes. Be brief, and do not rewrite the whole prompt unless asked.",
  openai:
    "You are the alternative writer. Offer a different take on the latest prompt: a tighter version, a different " +
    "structure, or a variant for a different model, alone in a code block marked prompt, and say in one or two lines " +
    "how it differs. Keep what already works."
};

/** Keeps prompts bounded; the newest messages are kept. */
export const MAX_TRANSCRIPT_CHARS = 60000;

/** How a model asks to run a command; the GUI shows it with a Run button and sends the output back. */
export function commandInstructions(platform: NodeJS.Platform = process.platform): string {
  const where = platform === "win32" ? "Windows PowerShell on the user's Windows computer" : "bash on the user's computer";
  return (
    "You can also run one command in the project's folder: put it alone in a code block marked run, for example\n" +
    "```run\nnpm test\n```\n" +
    `It runs in ${where}, but only after the user clicks Run, and its output comes back to you in the next message. ` +
    "Use it to look around (list the files, read one, check git history) or to run the project or its tests. " +
    "One command per block, and never propose anything that deletes or overwrites files unless the user asked for it."
  );
}

/** The architect starts Builds itself: there is no separate Build button, only the conversation. */
export const BUILD_INSTRUCTIONS =
  "You also start the build. When the user has asked for something to be made or changed and you know enough to do " +
  "it well, start it yourself: put the build request alone in a code block marked build, for example\n" +
  "```build\nCommand-line to-do list app\nAdd, list, complete and delete tasks; saved to tasks.json; Python 3, no extra packages.\n```\n" +
  "Its first line is a short title; the rest is everything Foundry needs to build it (what it does, how it is used, " +
  "its parts, the language). Foundry then designs, tests and writes the code within the user's spending limit, " +
  "and the user watches it here and can stop it. If something important is unclear, ask first instead. Don't start a " +
  "build to answer a question, and start at most one per reply.";

/**
 * What a planner must know about how Foundry builds, drawn from the engine itself (src/engine/convergence.ts,
 * src/swarm/claude_architect.ts planModules, src/swarm/harness.ts, src/engine/mutation.ts). Keep in sync with them.
 */
export function describeFoundryPipeline(config?: Pick<FoundryConfig, "maxConvergenceIterations" | "minMutationKillRatio">): string {
  const attempts = config?.maxConvergenceIterations ?? 8;
  const ratio = Math.round((config?.minMutationKillRatio ?? 0.85) * 100);
  return [
    "How Foundry builds (the engine these requests are for):",
    "- A Build starts from one request: a one-line title, then the spec. The architect (Claude) turns it into an ordered plan of " +
      "modules. Each module is ONE implementation file plus ONE test file, built, tested and committed one at a time, and a " +
      "module may import only the modules before it. Fewer modules converge faster; one is fine for a small change.",
    "- Per module, three design branches are tried (simplest, most robust, fastest). For each, the auditor (Gemini) writes the " +
      "tests FIRST from the module's contract, then the code writer implements until they pass. The module passes only when the " +
      `tests also catch at least ${ratio}% of deliberately broken copies of the code (mutation testing) and the language server ` +
      `reports no errors. Up to ${attempts} attempts per module; a module that never passes stops the Build (finished modules are kept).`,
    "- Languages Foundry can test: TypeScript (tests with Node built-ins only, no Jest), Python (pytest, hypothesis), Rust " +
      "(cargo test, no new crates), Go (go test), C++ (g++ -std=c++20). Tests run with no network and no new packages; anything " +
      "that needs the network, a UI, secrets or hardware must be isolated behind an interface the tests can fake.",
    "- Each Build has a spending limit and its request text is capped at 12,000 characters. Foundry remembers earlier runs in the " +
      "folder (what was built, what failed), so later Builds build on earlier ones. One Build runs at a time.",
    "- A Thinking level (low, medium, high, xhigh, max) applies to every model call: low or medium is quicker and cheaper for " +
      "boilerplate and small, well-specified parts; high or xhigh for tricky logic.",
    "What makes a Build fast and likely to pass: 1 to 3 modules with crisp contracts (the file, its public functions with input " +
      "and output types, behavior on bad input), pure logic separated from I/O, acceptance criteria a test can check, an explicit " +
      "language and runtime, a short list of allowed packages (or none), and nothing vague like \"make it nice\"."
  ].join("\n");
}

const JOB_SHARED =
  "You are part of Foundry's prompt studio in job mode. Foundry is an autonomous coding engine run by three models (Claude the " +
  "architect, Gemini the auditor, OpenAI or another model as the code writer). The user has a job that is too big or too vague " +
  "for one Build; the product is a JOB PLAN: an ordered list of steps, each a complete Build request Foundry runs well, one " +
  "after another, so the whole job gets done faster and cheaper than one sprawling Build. This conversation is the plan's " +
  "memory: everything the user said here still applies. The user may not be a programmer: keep replies short and in plain " +
  "English, and avoid jargon outside the plan itself.";

/** The planner's output: JSON the GUI turns into a job card with Start this job / Send to project. */
export const JOB_FORMAT =
  "Put the plan alone in a code block marked foundry, as JSON of exactly this shape:\n" +
  "```foundry\n" +
  '{"job": "<short name>", "project": "new" or "existing", "language": "<language and runtime>", ' +
  '"steps": [{"title": "<short>", "thinking": "low|medium|high|xhigh|max", "limit": <dollars as a number>, ' +
  '"build": "<the complete Build request: its first line is the title; then what it does, how it is used, its parts (files and ' +
  'public functions), the language, acceptance criteria the tests must check, and what is out of scope>", "optional": false}], ' +
  '"notes": "<one or two lines: what to check between steps, and what the user must provide>"}\n' +
  "```\n" +
  "Before the block, say in two or three lines how you split the job and why. After it, nothing. When the user asks for " +
  "changes, rewrite the whole plan in a new foundry block.";

const JOB_RULES =
  "Planning rules:\n" +
  "- Each step fits ONE Build: 1 to 3 modules, done in one pass, with its own tests. Split anything bigger.\n" +
  "- Order the steps so each builds on the ones before it: data types and pure logic first, storage and I/O next, the command " +
  "line or interface last; name the shared files so later steps target them.\n" +
  "- Every step's acceptance criteria must be checkable by tests under Foundry's harness; isolate the network, UI, secrets and " +
  "hardware behind an interface the tests can fake.\n" +
  "- Give each step a Thinking level (low or medium for boilerplate and small, well-specified parts; high for tricky logic; " +
  "xhigh only when correctness is subtle) and a limit in dollars ($1 to $3 small, $5 typical, $10 or more large).\n" +
  "- Mark steps that are nice-to-have as optional. Keep the whole plan to the fewest steps that still keep each Build small.\n" +
  "- Write each build text exactly as the user would type it to Claude in a project chat: concrete, complete, self-contained.\n" +
  "- If something important is unknown (what it must do, the language, whether the project already exists), ask at most two " +
  "short questions first instead of guessing.";

const JOB_ROLE: Record<ChatAgent, string> = {
  claude: `You are Claude, the planner. Write the job plan.\n${JOB_RULES}\n\n${JOB_FORMAT}`,
  gemini:
    "You are Gemini, the reviewer. Read the latest job plan and find the steps that will fail or waste budget: too big for one " +
    "Build, untestable under Foundry's harness, dependent on a later step, unclear contracts, needs the network, a UI or secrets " +
    "in its tests, or a Thinking level or limit that does not fit the step. Suggest the split or rewording for each. Be brief, " +
    "and do not rewrite the whole plan unless asked.",
  openai:
    "You are the alternative planner. Offer a different split of the latest job plan: fewer steps, a different order, or a " +
    "different first deliverable, in a new foundry block, and say in one or two lines how it differs.\n" +
    JOB_FORMAT
};

export function chatSystemPrompt(agent: ChatAgent, platform: NodeJS.Platform = process.platform, mode: ChatMode = "project"): string {
  if (mode === "prompt") return `${PROMPT_SHARED}\n\n${PROMPT_ROLE[agent]}`;
  if (mode === "job") return `${JOB_SHARED}\n\n${describeFoundryPipeline()}\n\n${JOB_ROLE[agent]}`;
  const build = agent === "claude" ? `\n\n${BUILD_INSTRUCTIONS}` : "";
  return `${SHARED_SYSTEM}\n\n${ROLE_SYSTEM[agent]}${build}\n\n${commandInstructions(platform)}`;
}

export function formatTranscript(messages: ChatMessage[], maxChars = MAX_TRANSCRIPT_CHARS): string {
  const lines = messages.map((m) => `${m.role === "user" ? "User" : AGENT_LABELS[m.agent ?? "claude"]}: ${m.text.trim()}`);
  const kept: string[] = [];
  let total = 0;
  for (let i = lines.length - 1; i >= 0; i--) {
    total += lines[i].length + 2;
    if (total > maxChars && kept.length > 0) break;
    kept.unshift(lines[i].length > maxChars ? `${lines[i].slice(0, maxChars)} [cut]` : lines[i]);
  }
  return `${kept.length < lines.length ? "[Earlier messages left out]\n\n" : ""}${kept.join("\n\n")}`;
}

export function composeChatPrompt(agent: ChatAgent, messages: ChatMessage[], projectContext = ""): string {
  return [
    projectContext.trim() ? `About the project:\n${projectContext.trim()}` : "",
    `Conversation so far:\n\n${formatTranscript(messages)}`,
    `Reply as ${AGENT_LABELS[agent]} to the user's latest message. Write only your reply, with no name in front of it.`
  ]
    .filter(Boolean)
    .join("\n\n");
}

/** Deterministic stand-in for offline mode (demos and tests): no API is called. */
export function offlineChatReply(agent: ChatAgent, messages: ChatMessage[], mode: ChatMode = "project"): string {
  const last = [...messages].reverse().find((m) => m.role === "user")?.text.trim() ?? "";
  const topic = last.split(/\r?\n/)[0].slice(0, 80);
  if (mode === "job") {
    if (agent === "gemini") {
      return `Offline demo reply. In the plan for "${topic}", step 2 depends on the storage format; name the file and its functions in step 1.`;
    }
    const plan = {
      job: topic,
      project: "new",
      language: "TypeScript (Node.js)",
      steps: [
        { title: "Core logic with tests", thinking: "medium", limit: 2,
          build: `Core logic for: ${topic}\nPure functions in src/core.ts, no I/O: parse the input, compute the result, return plain data. TypeScript on Node.js, no packages. Tests cover empty, large and invalid input.` },
        { title: "Command line", thinking: "low", limit: 1,
          build: `Command line for: ${topic}\nsrc/cli.ts uses src/core.ts: reads the arguments, prints the result, exits non-zero on bad input. TypeScript on Node.js, no packages. Tests call main() with sample arguments.` }
      ],
      notes: "Run step 2 after step 1 finished; the command line imports src/core.ts."
    };
    const fence = "```";
    return `Offline demo reply. Two small steps: the logic first, then the command line on top of it.\n${fence}foundry\n${JSON.stringify(plan, null, 2)}\n${fence}`;
  }
  if (mode === "prompt") {
    if (agent === "gemini") {
      return `Offline demo reply. The prompt for "${topic}" should say what format the answer takes and what to do when information is missing.`;
    }
    return (
      `Offline demo reply. Here is a prompt for "${topic}":\n` +
      `\`\`\`prompt\nYou are a careful assistant. Task: ${topic}.\nAnswer in plain English, in short paragraphs, and say when you are unsure.\n\`\`\`\n` +
      `It names the role, the task and the format.`
    );
  }
  // Demo hooks for the command flow: "run: <command>" proposes it; a command's output gets an acknowledgement.
  const ran = /^I ran `[\s\S]*?Exit code (-?\d+)/.exec(last);
  if (ran) return `Offline demo reply. The command finished with exit code ${ran[1]}.`;
  const runRequest = /^run:\s*(.+)$/im.exec(last);
  if (runRequest) return `Offline demo reply. Let's check:\n\`\`\`run\n${runRequest[1].trim()}\n\`\`\``;
  if (agent === "claude" && /\b(build it|go ahead|start the build|do it)\b|^build\b/i.test(last)) {
    const request = messages.find((m) => m.role === "user")?.text.trim() ?? last;
    const title = request.split(/\r?\n/)[0].slice(0, 80);
    return `Offline demo reply. Starting the build.\n\`\`\`build\n${title}\n${request}\n\`\`\``;
  }
  const lines: Record<ChatAgent, string> = {
    claude: `Offline demo reply. A plan for "${topic}": one small program with a clear entry point, plus tests. Press Build when it looks right.`,
    gemini: `Offline demo reply. For "${topic}", the tests should cover empty input, very large input and invalid values.`,
    openai: `Offline demo reply. "${topic}" fits in a single module with one public function and a short README.`
  };
  return lines[agent];
}

export interface ChatReply {
  text: string;
  model: string;
}

/** Live text for a chat window. Only Claude streams; the other models' replies arrive whole. */
export interface ChatStream {
  onText: (delta: string) => void;
  /** A new attempt started (1, 2, ...): text from an earlier, failed attempt is void. */
  onAttempt?: (attempt: number) => void;
}

export async function replyToChat(
  config: FoundryConfig,
  economics: EconomicsEngine,
  agent: ChatAgent,
  messages: ChatMessage[],
  projectContext = "",
  stream?: ChatStream,
  mode: ChatMode = "project",
  extraSystem = ""
): Promise<ChatReply> {
  if (config.offlineMode) {
    const text = offlineChatReply(agent, messages, mode);
    if (stream && agent === "claude") {
      // Streams word by word, like Claude does, so offline demos exercise the live view too.
      stream.onAttempt?.(1);
      for (const piece of text.match(/\S+\s*/g) ?? []) stream.onText(piece);
    }
    return { text, model: "offline" };
  }
  const request: CompletionRequest = {
    label: `Chat (${AGENT_LABELS[agent]})`,
    system: `${chatSystemPrompt(agent, process.platform, mode)}${extraSystem.trim() ? `\n\n${extraSystem.trim()}` : ""}`,
    prompt: composeChatPrompt(agent, messages, projectContext),
    ...(stream ? { onText: stream.onText, onAttempt: stream.onAttempt } : {})
  };
  if (agent === "gemini") {
    if (!config.geminiApiKey) throw new Error("No Gemini key. Add GEMINI_API_KEY in Settings.");
    return { text: (await completeWithGemini<string>(config, economics, request)).trim(), model: config.geminiModel };
  }
  if (agent === "openai" && config.astraApiKey) {
    return { text: (await completeWithAstra<string>(config, economics, request)).trim(), model: config.astraModel };
  }
  // Claude, and the synthesizer's stand-in when there is no OpenAI key (as in the build loop).
  if (!config.anthropicApiKey) throw new Error("No Claude key. Add ANTHROPIC_API_KEY in Settings.");
  const text = (await completeWithClaude<string>(config, economics, request)).trim();
  return { text, model: agent === "openai" ? `${config.anthropicModel} (standing in for OpenAI)` : config.anthropicModel };
}
