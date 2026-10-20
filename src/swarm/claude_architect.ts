import { z } from "zod";
import { FoundryConfig } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { SupportedLanguage } from "../ast/parser";
import { normalizeWorkspacePath } from "../utils/paths";
import { BRANCH_STRATEGIES, describeTestHarness } from "./harness";
import { completeWithProvider, extractJson, extractJsonArray } from "./providers";

export interface StateSchema {
  name: string;
  fields: Record<string, string>;
  invariants: string[];
}

export interface InterfaceContract {
  symbolName: string;
  signature: string;
  preconditions: string[];
  postconditions: string[];
  complexityBound: string;
}

export interface ModuleArchitectureContract {
  branchStrategy: string;
  moduleName: string;
  targetFilePath: string;
  testFilePath: string;
  language: SupportedLanguage;
  /** moduleNames of earlier modules in the plan that this module imports. */
  dependsOn: string[];
  stateSchemas: StateSchema[];
  contracts: InterfaceContract[];
  boundaryInvariants: string[];
}

/** An earlier, already-converged module that the module being built imports. */
export interface ModuleDependency {
  contract: ModuleArchitectureContract;
  source: string;
}

const MAX_DEPENDENCY_SOURCE_CHARS = 12000;

/** Renders dependencies for the auditor and synthesizer prompts. */
export function describeModuleDependencies(dependencies: ModuleDependency[]): string {
  return dependencies
    .map(({ contract, source }) => {
      const api = contract.contracts.map((c) => c.signature || c.symbolName).join("; ");
      const body = source.length > MAX_DEPENDENCY_SOURCE_CHARS ? `${source.slice(0, MAX_DEPENDENCY_SOURCE_CHARS)}\n... (truncated)` : source;
      return `- ${contract.moduleName} (${contract.targetFilePath}) exports: ${api}\n  Current source of ${contract.targetFilePath}:\n${body || "(empty)"}`;
    })
    .join("\n\n");
}

const workspacePath = z.string().transform((value, ctx) => {
  try {
    return normalizeWorkspacePath(value);
  } catch (err) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: err instanceof Error ? err.message : String(err) });
    return z.NEVER;
  }
});

const ContractSchema = z.object({
  branchStrategy: z.string().default(""),
  moduleName: z.string().min(1),
  targetFilePath: workspacePath,
  testFilePath: workspacePath,
  language: z.enum(["typescript", "rust", "python", "go", "cpp"]),
  dependsOn: z.array(z.string().min(1)).default([]),
  stateSchemas: z
    .array(
      z.object({
        name: z.string(),
        fields: z.record(z.string()).default({}),
        invariants: z.array(z.string()).default([])
      })
    )
    .default([]),
  contracts: z
    .array(
      z.object({
        symbolName: z.string().min(1),
        signature: z.string().default(""),
        preconditions: z.array(z.string()).default([]),
        postconditions: z.array(z.string()).default([]),
        complexityBound: z.string().default("")
      })
    )
    .min(1),
  boundaryInvariants: z.array(z.string()).default([])
});

/** The part of a planned module a branch may redesign; identity, paths and dependencies stay as planned. */
const BranchDesignSchema = ContractSchema.pick({
  branchStrategy: true,
  stateSchemas: true,
  contracts: true,
  boundaryInvariants: true
});

function formatIssues(error: z.ZodError): string {
  return error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
}

/**
 * Parses and validates an ordered module plan. Modules are built in order, so
 * a module may only depend on modules listed before it; names and file paths
 * must be unique across the plan.
 */
export function parseArchitectureContracts(raw: string, expectedLanguage: SupportedLanguage): ModuleArchitectureContract[] {
  const items = extractJsonArray(raw, "modules");
  const contracts = items.map((item, idx) => {
    const parsed = ContractSchema.safeParse(item);
    if (!parsed.success) {
      throw new Error(`Contract #${idx + 1} is invalid: ${formatIssues(parsed.error)}`);
    }
    return parsed.data;
  });
  return validateModulePlan(contracts, expectedLanguage);
}

export function validateModulePlan(
  contracts: ModuleArchitectureContract[],
  expectedLanguage: SupportedLanguage
): ModuleArchitectureContract[] {
  if (contracts.length === 0) {
    throw new Error("Architect returned an empty module plan");
  }
  const earlierModules = new Set<string>();
  const pathOwners = new Map<string, string>();
  return contracts.map((contract, idx) => {
    const label = `Module #${idx + 1} ("${contract.moduleName}")`;
    if (contract.language !== expectedLanguage) {
      throw new Error(`Contract language "${contract.language}" does not match the project language "${expectedLanguage}"`);
    }
    if (contract.targetFilePath === contract.testFilePath) {
      throw new Error("Contract targetFilePath and testFilePath must differ");
    }
    if (earlierModules.has(contract.moduleName)) {
      throw new Error(`${label} duplicates an earlier moduleName`);
    }
    for (const filePath of [contract.targetFilePath, contract.testFilePath]) {
      const owner = pathOwners.get(filePath);
      if (owner) throw new Error(`${label} reuses ${filePath}, which belongs to "${owner}"`);
    }
    const dependsOn = Array.from(new Set(contract.dependsOn));
    for (const dependency of dependsOn) {
      if (!earlierModules.has(dependency)) {
        throw new Error(`${label} depends on "${dependency}", which is not an earlier module in the plan`);
      }
    }
    earlierModules.add(contract.moduleName);
    pathOwners.set(contract.targetFilePath, contract.moduleName);
    pathOwners.set(contract.testFilePath, contract.moduleName);
    return { ...contract, dependsOn };
  });
}

/**
 * Parses one branch's design of a planned module. The planned identity
 * (name, paths, language, dependencies) always wins, and every planned public
 * symbol must survive so later modules can rely on it.
 */
export function parseBranchModuleDesign(raw: string, planned: ModuleArchitectureContract): ModuleArchitectureContract {
  const value = extractJson(raw);
  if (Array.isArray(value) && value.length !== 1) {
    throw new Error(`Expected exactly one module design, got ${value.length}`);
  }
  const parsed = BranchDesignSchema.safeParse(Array.isArray(value) ? value[0] : value);
  if (!parsed.success) {
    throw new Error(`Module design is invalid: ${formatIssues(parsed.error)}`);
  }
  const designed = new Set(parsed.data.contracts.map((c) => c.symbolName));
  const dropped = planned.contracts.map((c) => c.symbolName).filter((symbol) => !designed.has(symbol));
  if (dropped.length > 0) {
    throw new Error(`Design of "${planned.moduleName}" dropped planned symbol(s): ${dropped.join(", ")}`);
  }
  return { ...planned, ...parsed.data };
}

const CONTRACT_SHAPE = `{
  "branchStrategy": string,            // leave empty in the plan; branches fill it in
  "moduleName": string,                // snake_case module identifier, unique in the plan
  "targetFilePath": string,            // workspace-relative implementation path, forward slashes
  "testFilePath": string,              // workspace-relative test path, forward slashes
  "language": "typescript" | "rust" | "python" | "go" | "cpp",
  "dependsOn": [string],               // moduleNames of EARLIER modules in this plan that this module imports
  "stateSchemas": [{ "name": string, "fields": { "<field>": "<type>" }, "invariants": [string] }],
  "contracts": [{ "symbolName": string, "signature": string, "preconditions": [string], "postconditions": [string], "complexityBound": string }],
  "boundaryInvariants": [string]
}`;

const DESIGN_SHAPE = `{
  "branchStrategy": string,            // one sentence describing this branch's approach
  "stateSchemas": [{ "name": string, "fields": { "<field>": "<type>" }, "invariants": [string] }],
  "contracts": [{ "symbolName": string, "signature": string, "preconditions": [string], "postconditions": [string], "complexityBound": string }],
  "boundaryInvariants": [string]
}`;

function describeModulePlan(plan: ModuleArchitectureContract[], currentIndex: number): string {
  return plan
    .map((m, idx) => {
      const state = idx < currentIndex ? "converged, code is in the workspace" : idx === currentIndex ? "DESIGN THIS ONE NOW" : "later";
      const imports = m.dependsOn.length > 0 ? `, imports ${m.dependsOn.join(", ")}` : "";
      return `${idx + 1}. ${m.moduleName} -> ${m.targetFilePath} (tests: ${m.testFilePath}${imports}) [${state}]`;
    })
    .join("\n");
}

export class ClaudeArchitect {
  private config: FoundryConfig;
  private economics: EconomicsEngine;

  constructor(config: FoundryConfig, economics: EconomicsEngine) {
    this.config = config;
    this.economics = economics;
  }

  /** Decomposes the specification into an ordered plan of modules, each of which may import the ones before it. */
  public async planModules(params: {
    specification: string;
    language: SupportedLanguage;
    callGraphSummary: string;
    episodicMemoryContext: string;
    existingFiles: Map<string, string>;
  }): Promise<ModuleArchitectureContract[]> {
    if (this.config.offlineMode) {
      return validateModulePlan(this.synthesizeDeterministicPlan(params), params.language);
    }

    const fileList = Array.from(params.existingFiles.keys()).slice(0, 200);
    const prompt = [
      `You are the Lead Architect of an autonomous software engineering engine.`,
      `Target language: ${params.language}`,
      `Specification:\n${params.specification}`,
      `Existing workspace files:\n${fileList.length > 0 ? fileList.join("\n") : "(empty workspace)"}`,
      `Call graph context:\n${params.callGraphSummary}`,
      `Episodic memory (fixes that worked in past runs):\n${params.episodicMemoryContext}`,
      `Test harness each module's tests will run under:\n${describeTestHarness(params.language, "<targetFilePath>", "<testFilePath>")}`,
      [
        `Decompose the specification into an ordered plan of modules. Each module is one implementation file with its own test file; modules are implemented, tested and committed one at a time, in plan order.`,
        `A module may import only modules listed before it, and must name them in "dependsOn". Use as few modules as the specification needs (a single module is fine for a small change).`,
        `When the workspace already contains the code to change, target that existing file. No two modules may share a file.`,
        `Respond with ONLY a JSON array of module objects in build order, each of this shape (no prose, no comments):`,
        CONTRACT_SHAPE
      ].join("\n")
    ].join("\n\n");

    return completeWithProvider<ModuleArchitectureContract[]>(this.config.architectProvider, this.config, this.economics, {
      label: "ClaudeArchitect[plan]",
      prompt,
      parse: (text) => parseArchitectureContracts(text, params.language)
    });
  }

  /** Designs one planned module for one search branch, against the workspace as earlier modules left it. */
  public async designBranchArchitecture(params: {
    branchId: string;
    specification: string;
    plan: ModuleArchitectureContract[];
    moduleIndex: number;
    language: SupportedLanguage;
    callGraphSummary: string;
    episodicMemoryContext: string;
    existingFiles: Map<string, string>;
  }): Promise<ModuleArchitectureContract> {
    const planned = params.plan[params.moduleIndex];
    if (this.config.offlineMode) {
      return { ...planned, branchStrategy: `Branch_${params.branchId}_StrictInvariants` };
    }

    const fileList = Array.from(params.existingFiles.keys()).slice(0, 200);
    const prompt = [
      `You are the Lead Architect for Branch ${params.branchId} of an autonomous software engineering engine.`,
      `Branch strategy: ${BRANCH_STRATEGIES[params.branchId] ?? "Choose a distinct, well-justified design."}`,
      `Target language: ${params.language}`,
      `Specification:\n${params.specification}`,
      `Module plan (built in order; each module may import the ones before it):\n${describeModulePlan(params.plan, params.moduleIndex)}`,
      `Planned contract of the module to design:\n${JSON.stringify(planned, null, 2)}`,
      `Existing workspace files:\n${fileList.length > 0 ? fileList.join("\n") : "(empty workspace)"}`,
      `Call graph context:\n${params.callGraphSummary}`,
      `Episodic memory (fixes that worked in past runs):\n${params.episodicMemoryContext}`,
      `Test harness the generated tests will run under:\n${describeTestHarness(params.language, planned.targetFilePath, planned.testFilePath)}`,
      [
        `Refine this module's contract according to your branch strategy. Keep every planned symbolName so later modules can rely on it; you may tighten signatures and invariants and add helper symbols.`,
        `The module name, file paths, language and dependencies are fixed by the plan. Build on the earlier modules' actual code shown in the call graph rather than re-implementing it.`,
        `Respond with ONLY a JSON object of this shape (no prose, no comments):`,
        DESIGN_SHAPE
      ].join("\n")
    ].join("\n\n");

    return completeWithProvider<ModuleArchitectureContract>(this.config.architectProvider, this.config, this.economics, {
      label: `ClaudeArchitect[${params.branchId}:${planned.moduleName}]`,
      prompt,
      parse: (text) => parseBranchModuleDesign(text, planned)
    });
  }

  /**
   * Offline-mode stand-in used for demos and the end-to-end tests; makes no API calls and records no spend.
   * The first symbol named in the spec becomes the core module. Every further `symbol <name>` becomes
   * another module that imports the one before it and combines it as f(x) + f(x + 1).
   */
  private synthesizeDeterministicPlan(params: { specification: string; language: SupportedLanguage }): ModuleArchitectureContract[] {
    const defaultPaths: Record<SupportedLanguage, { src: string; test: string }> = {
      typescript: { src: "src/module.ts", test: "tests/module.test.ts" },
      rust: { src: "src/lib.rs", test: "tests/integration_test.rs" },
      python: { src: "src/module.py", test: "tests/test_module.py" },
      go: { src: "module.go", test: "module_test.go" },
      cpp: { src: "src/module.cpp", test: "tests/module_test.cpp" }
    };
    const dependentPaths = (symbol: string): Record<SupportedLanguage, { src: string; test: string }> => ({
      typescript: { src: `src/${symbol}.ts`, test: `tests/${symbol}.test.ts` },
      rust: { src: `src/${symbol}.rs`, test: `tests/${symbol}_test.rs` },
      python: { src: `src/${symbol}.py`, test: `tests/test_${symbol}.py` },
      go: { src: `${symbol}.go`, test: `${symbol}_test.go` },
      cpp: { src: `src/${symbol}.cpp`, test: `tests/${symbol}_test.cpp` }
    });

    // A carried-over contract (continuing an unfinished round) names its symbol explicitly; prefer it over the
    // first "function X" in the text, which may belong to the earlier attempt's test code.
    const carriedSymbol = /"symbolName":\s*"([a-zA-Z_][a-zA-Z0-9_]*)"/.exec(params.specification);
    const fnMatch = carriedSymbol ?? /(?:function|fn|def|func|symbol)\s+([a-zA-Z_][a-zA-Z0-9_]*)/i.exec(params.specification);
    const primarySymbol = fnMatch ? fnMatch[1] : "execute_core_pipeline";
    const furtherSymbols = Array.from(params.specification.matchAll(/\bsymbol\s+([a-zA-Z_][a-zA-Z0-9_]*)/gi), (m) => m[1]);
    const symbols = Array.from(new Set([primarySymbol, ...furtherSymbols]));

    const plan: ModuleArchitectureContract[] = [
      {
        branchStrategy: "",
        moduleName: "core_module",
        targetFilePath: defaultPaths[params.language].src,
        testFilePath: defaultPaths[params.language].test,
        language: params.language,
        dependsOn: [],
        stateSchemas: [
          {
            name: "PipelineState",
            fields: { capacity: "number", activeCount: "number" },
            invariants: ["capacity > 0", "activeCount >= 0", "activeCount <= capacity"]
          }
        ],
        contracts: [
          {
            symbolName: primarySymbol,
            signature: `${primarySymbol}(input: number): number`,
            preconditions: ["Number.isFinite(input)", "input >= 0"],
            postconditions: ["result >= input", "Number.isFinite(result)"],
            complexityBound: "O(1) time, O(1) space"
          }
        ],
        boundaryInvariants: [
          "Reject negative inputs with explicit error",
          "Enforce deterministic arithmetic under boundary values 0 and MAX_SAFE_INTEGER"
        ]
      }
    ];

    for (const symbol of symbols.slice(1)) {
      const previous = plan[plan.length - 1];
      const dependencySymbol = previous.contracts[0].symbolName;
      const paths = dependentPaths(symbol)[params.language];
      plan.push({
        branchStrategy: "",
        moduleName: `${symbol}_module`,
        targetFilePath: paths.src,
        testFilePath: paths.test,
        language: params.language,
        dependsOn: [previous.moduleName],
        stateSchemas: [],
        contracts: [
          {
            symbolName: symbol,
            signature: `${symbol}(input: number): number`,
            preconditions: [`input is accepted by ${dependencySymbol}`],
            postconditions: [`result === ${dependencySymbol}(input) + ${dependencySymbol}(input + 1)`],
            complexityBound: `O(1) calls to ${dependencySymbol}`
          }
        ],
        boundaryInvariants: [`Inputs rejected by ${dependencySymbol} are rejected, not swallowed`]
      });
    }
    return plan;
  }
}
