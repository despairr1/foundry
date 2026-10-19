import { z } from "zod";
import { FoundryConfig } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { ModuleArchitectureContract, ModuleDependency, describeModuleDependencies } from "./claude_architect";
import { TestSuiteSpec } from "./gemini_auditor";
import { SemanticPatch } from "../ast/semantic_patcher";
import { logger } from "../utils/logger";
import { normalizeWorkspacePath } from "../utils/paths";
import { describeTestHarness, pythonModulePath, typescriptImportPath } from "./harness";
import { completeWithAstra, completeWithClaude, extractJsonArray } from "./providers";

const PATCH_ACTIONS = [
  "REPLACE_FUNCTION",
  "REPLACE_CLASS",
  "REPLACE_STRUCT",
  "REPLACE_NODE",
  "INSERT_AFTER",
  "INSERT_BEFORE",
  "DELETE_NODE",
  "UPSERT_IMPORT",
  "CREATE_FILE"
] as const;

const PatchSchema = z.object({
  filePath: z.string(),
  action: z.enum(PATCH_ACTIONS),
  target: z.string().default(""),
  content: z.string().default("")
});

export function parseSemanticPatches(raw: string): SemanticPatch[] {
  const items = extractJsonArray(raw, "patches");
  const patches = items.map((item, idx) => {
    const parsed = PatchSchema.safeParse(item);
    if (!parsed.success) {
      throw new Error(`Patch #${idx + 1} is invalid: ${parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`);
    }
    return { ...parsed.data, filePath: normalizeWorkspacePath(parsed.data.filePath) };
  });
  if (patches.length === 0) {
    throw new Error("Synthesizer returned no patches");
  }
  return patches;
}

const PATCH_PROTOCOL = [
  `Respond with ONLY a JSON object of the form {"patches": [ { "filePath": string, "action": string, "target": string, "content": string } ]}.`,
  `Do NOT emit diffs or line numbers. Patches address structural AST nodes by name:`,
  `- REPLACE_FUNCTION / REPLACE_CLASS / REPLACE_STRUCT / REPLACE_NODE: replace the declaration named "target" with "content" (the complete new declaration, including export/pub modifiers). If no such declaration exists it is appended to the file.`,
  `- INSERT_BEFORE / INSERT_AFTER: insert "content" next to the declaration named "target".`,
  `- DELETE_NODE: remove the declaration named "target" ("content" is ignored).`,
  `- UPSERT_IMPORT: add the import statement in "content" if it is not already present ("target" is ignored).`,
  `- CREATE_FILE: write "content" as the entire file (use for new files and manifests such as Cargo.toml or go.mod).`,
  `filePath is workspace-relative with forward slashes. Never modify test files; they are owned by the auditor.`
].join("\n");

export class AstraSynthesizer {
  private config: FoundryConfig;
  private economics: EconomicsEngine;

  constructor(config: FoundryConfig, economics: EconomicsEngine) {
    this.config = config;
    this.economics = economics;
  }

  public async synthesizeSemanticPatches(params: {
    branchId: string;
    contract: ModuleArchitectureContract;
    testSuite: TestSuiteSpec;
    currentSource: string;
    feedbackText: string;
    episodicMemoryContext: string;
    workspaceFilePaths: string[];
    /** Earlier modules this one imports, with their current source. */
    dependencies: ModuleDependency[];
  }): Promise<SemanticPatch[]> {
    if (this.config.offlineMode) {
      return this.buildDeterministicSemanticPatch(params);
    }

    const prompt = [
      `You are Astra, the Code Synthesizer for Branch ${params.branchId} of an autonomous software engineering engine.`,
      `Implement the contract so that the auditor's test suite passes.`,
      `Contract:\n${JSON.stringify(params.contract, null, 2)}`,
      `Test harness:\n${describeTestHarness(params.contract.language, params.contract.targetFilePath, params.contract.testFilePath)}`,
      `Test suite (${params.testSuite.testFilePath}):\n${params.testSuite.testCode}`,
      `Workspace files:\n${params.workspaceFilePaths.join("\n") || "(none)"}`,
      ...(params.dependencies.length > 0
        ? [
            `Earlier modules this one builds on (already implemented and verified; import them instead of re-implementing them, and keep their public API and tests passing):\n${describeModuleDependencies(
              params.dependencies
            )}`
          ]
        : []),
      `Current source of ${params.contract.targetFilePath}:\n${params.currentSource || "(file does not exist yet)"}`,
      `Feedback from the previous attempt (diagnostics, sandbox output, rejected patches):\n${params.feedbackText}`,
      `Episodic memory (fixes that worked in past runs):\n${params.episodicMemoryContext}`,
      PATCH_PROTOCOL
    ].join("\n\n");

    const request = { prompt, parse: parseSemanticPatches };

    if (this.config.astraApiKey) {
      try {
        return await completeWithAstra<SemanticPatch[]>(this.config, this.economics, {
          ...request,
          label: `AstraSynthesizer[${params.branchId}]`,
          jsonObject: true
        });
      } catch (err) {
        if ((err as Error)?.name === "BudgetExceededError" || !this.config.anthropicApiKey) throw err;
        if ((err as Error)?.name === "QuotaExhaustedError") {
          logger.warn("AstraSynthesizer", `The code writer's account has no credits remaining; Claude writes the code for the rest of this run.`);
        } else {
          logger.warn(
            "AstraSynthesizer",
            `Astra failed on Branch ${params.branchId} (${err instanceof Error ? err.message : String(err)}); falling back to Claude.`
          );
        }
      }
    }

    return completeWithClaude<SemanticPatch[]>(this.config, this.economics, {
      ...request,
      label: `ClaudeSynthesizer[${params.branchId}]`
    });
  }

  /** Offline-mode stand-in used for demos and the end-to-end test; makes no API calls and records no spend. */
  private buildDeterministicSemanticPatch(params: {
    contract: ModuleArchitectureContract;
    dependencies: ModuleDependency[];
  }): SemanticPatch[] {
    const symbol = params.contract.contracts[0]?.symbolName ?? "execute_core_pipeline";
    const { targetFilePath } = params.contract;
    const dependency = params.dependencies[0]?.contract;
    const dependencySymbol = dependency?.contracts[0]?.symbolName;
    let content = "";

    // A dependent module imports the module before it and returns dep(x) + dep(x + 1).
    if (dependency && dependencySymbol && params.contract.language === "typescript") {
      return [
        {
          filePath: targetFilePath,
          action: "REPLACE_FUNCTION",
          target: symbol,
          content: [
            `export function ${symbol}(input: number): number {`,
            `  return ${dependencySymbol}(input) + ${dependencySymbol}(input + 1);`,
            `}`
          ].join("\n")
        },
        {
          filePath: targetFilePath,
          action: "UPSERT_IMPORT",
          target: "",
          content: `import { ${dependencySymbol} } from "${typescriptImportPath(targetFilePath, dependency.targetFilePath)}";`
        }
      ];
    }
    if (dependency && dependencySymbol && params.contract.language === "python") {
      return [
        {
          filePath: targetFilePath,
          action: "REPLACE_FUNCTION",
          target: symbol,
          content: [
            `def ${symbol}(input_val: int) -> int:`,
            `    return ${dependencySymbol}(input_val) + ${dependencySymbol}(input_val + 1)`
          ].join("\n")
        },
        {
          filePath: targetFilePath,
          action: "UPSERT_IMPORT",
          target: "",
          content: `from ${pythonModulePath(dependency.targetFilePath)} import ${dependencySymbol}`
        }
      ];
    }

    if (params.contract.language === "typescript") {
      content = [
        `export function ${symbol}(input: number): number {`,
        `  if (!Number.isFinite(input) || input < 0) {`,
        `    throw new RangeError("Input must be a non-negative finite number");`,
        `  }`,
        `  return input * 2 + 1;`,
        `}`
      ].join("\n");
    } else if (params.contract.language === "python") {
      content = [
        `def ${symbol}(input_val: int) -> int:`,
        `    if input_val < 0:`,
        `        raise ValueError("Input must be non-negative")`,
        `    return input_val * 2 + 1`
      ].join("\n");
    } else if (params.contract.language === "rust") {
      content = [
        `pub fn ${symbol}(input: i64) -> Result<i64, &'static str> {`,
        `    if input < 0 {`,
        `        return Err("Input must be non-negative");`,
        `    }`,
        `    Ok(input * 2 + 1)`,
        `}`
      ].join("\n");
    } else {
      content = `// Synthesized ${symbol}`;
    }

    return [
      {
        filePath: targetFilePath,
        action: "REPLACE_FUNCTION",
        target: symbol,
        content
      }
    ];
  }
}
