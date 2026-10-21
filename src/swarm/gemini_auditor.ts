import { FoundryConfig } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { ModuleArchitectureContract, ModuleDependency, describeModuleDependencies } from "./claude_architect";
import { describeTestHarness, pythonModulePath, typescriptImportPath } from "./harness";
import { completeWithProvider } from "./providers";

export interface TestSuiteSpec {
  moduleName: string;
  testFilePath: string;
  testCode: string;
  targetedInvariants: string[];
}

/** Returns the code inside the largest fenced block, or the whole text when there are no fences. */
export function stripMarkdownCodeFences(raw: string): string {
  const blocks = Array.from(raw.matchAll(/```[^\n`]*\n([\s\S]*?)```/g), (m) => m[1]);
  if (blocks.length === 0) return raw.trim();
  return blocks.reduce((longest, block) => (block.length > longest.length ? block : longest)).trim();
}

export class GeminiAuditor {
  private config: FoundryConfig;
  private economics: EconomicsEngine;

  constructor(config: FoundryConfig, economics: EconomicsEngine) {
    this.config = config;
    this.economics = economics;
  }

  /** The stable part of an auditor prompt for a module (cached across the public, hardened and hidden suites). */
  private moduleContext(contract: ModuleArchitectureContract, testFilePath: string, dependencies: ModuleDependency[]): string {
    return [
      `You are the Invariant Auditor of an autonomous software engineering engine. You write the tests; a separate code writer implements the module, and it never sees the tests before they run.`,
      `Module "${contract.moduleName}" (${contract.language}). Implementation path: ${contract.targetFilePath}. Test file path: ${testFilePath}`,
      `Formal contract:\n${JSON.stringify(contract, null, 2)}`,
      `Test harness:\n${describeTestHarness(contract.language, contract.targetFilePath, testFilePath)}`,
      ...(dependencies.length > 0
        ? [
            `This module builds on earlier modules that are already implemented and verified. Test this module's own contract, using the dependencies' real behavior when computing expected values:\n${describeModuleDependencies(
              dependencies
            )}`
          ]
        : []),
      `Test only behavior the contract specifies. Return the complete test file in a single fenced code block and nothing else.`
    ].join("\n\n");
  }

  private async requestSuite(label: string, system: string, prompt: string): Promise<string> {
    return completeWithProvider<string>(this.config.auditorProvider, this.config, this.economics, {
      label,
      system,
      prompt,
      parse: (text) => {
        const code = stripMarkdownCodeFences(text);
        if (!code) throw new Error("The auditor returned an empty test suite");
        return code;
      }
    });
  }

  public async generateMutationLethalTests(
    contract: ModuleArchitectureContract,
    feedbackFromMutationEngine?: string,
    dependencies: ModuleDependency[] = []
  ): Promise<TestSuiteSpec> {
    if (this.config.offlineMode) {
      return this.buildDeterministicTestSuite(contract, dependencies);
    }

    const prompt = [
      `Write property-based and boundary tests that will kill operator-inversion, constant-perturbation, and guard-deletion mutants.`,
      feedbackFromMutationEngine
        ? `These mutants survived the previous suite. Add assertions that fail on each of them while still passing on a correct implementation:\n${feedbackFromMutationEngine}`
        : `Use exact-value assertions at every boundary named in the contract (including just inside and just outside each limit), and assert that every precondition violation is rejected.`
    ].join("\n\n");

    const testCode = await this.requestSuite(
      `GeminiAuditor[${contract.moduleName}]`,
      this.moduleContext(contract, contract.testFilePath, dependencies),
      prompt
    );

    return {
      moduleName: contract.moduleName,
      testFilePath: contract.testFilePath,
      testCode,
      targetedInvariants: contract.boundaryInvariants
    };
  }

  /**
   * GEF's holdout workload: a second, hidden acceptance suite for the module. The code writer never sees it (it is
   * kept out of every branch workspace), so passing it means the contract was met, not that the visible tests were
   * fitted. On a failure only the shrunken counterexample goes back to the writer.
   */
  public async generateHoldoutTests(
    contract: ModuleArchitectureContract,
    holdoutFilePath: string,
    dependencies: ModuleDependency[] = []
  ): Promise<TestSuiteSpec> {
    if (this.config.offlineMode) {
      return this.buildDeterministicHoldoutSuite(contract, holdoutFilePath, dependencies);
    }

    const prompt = [
      `Write the HIDDEN acceptance suite for this module. It is kept from the code writer, which only ever sees a visible suite of boundary tests, so it must catch an implementation that fits those obvious cases without meeting the contract.`,
      `Exercise the public API the way its real callers would: realistic end-to-end workloads, sequences of calls, representative and awkward inputs (empty, large, unicode, repeated, out of order), and values that are not the textbook boundary numbers. Derive every expected value from the contract alone, never from an implementation you imagine.`,
      `Do not test implementation details, performance, or anything the contract leaves open. Keep it deterministic (no network, clock or randomness without a fixed seed), and give each assertion a message that names the expectation.`
    ].join("\n\n");

    const testCode = await this.requestSuite(
      `GeminiAuditor[${contract.moduleName}:holdout]`,
      this.moduleContext(contract, holdoutFilePath, dependencies),
      prompt
    );

    return {
      moduleName: contract.moduleName,
      testFilePath: holdoutFilePath,
      testCode,
      targetedInvariants: contract.boundaryInvariants
    };
  }

  /** Offline holdout suite: the same contract as the visible suite, probed at other values. */
  private buildDeterministicHoldoutSuite(
    contract: ModuleArchitectureContract,
    holdoutFilePath: string,
    dependencies: ModuleDependency[]
  ): TestSuiteSpec {
    const symbol = contract.contracts[0]?.symbolName ?? "execute_core_pipeline";
    const dependency = dependencies[0]?.contract;
    const dependencySymbol = dependency?.contracts[0]?.symbolName;
    let testCode = "";

    if (contract.language === "typescript") {
      const importLines = [
        `import * as assert from "assert";`,
        `import { ${symbol} } from "${typescriptImportPath(holdoutFilePath, contract.targetFilePath)}";`,
        ...(dependency && dependencySymbol
          ? [`import { ${dependencySymbol} } from "${typescriptImportPath(holdoutFilePath, dependency.targetFilePath)}";`]
          : [])
      ];
      const checks =
        dependency && dependencySymbol
          ? [
              `  for (const input of [3, 7, 42]) {`,
              `    assert.strictEqual(${symbol}(input), ${dependencySymbol}(input) + ${dependencySymbol}(input + 1), "hidden: composition at " + input);`,
              `  }`
            ]
          : [
              `  assert.strictEqual(${symbol}(3), 7, "hidden: 2*3 + 1");`,
              `  assert.strictEqual(${symbol}(100), 201, "hidden: 2*100 + 1");`,
              `  assert.throws(() => ${symbol}(-7), "hidden: negative input must be rejected");`
            ];
      testCode = [...importLines, ``, `export function runHiddenSuite(): void {`, ...checks, `}`, ``, `runHiddenSuite();`].join("\n");
    } else if (contract.language === "python") {
      const importLines = [
        `import pytest`,
        `from ${pythonModulePath(contract.targetFilePath)} import ${symbol}`,
        ...(dependency && dependencySymbol ? [`from ${pythonModulePath(dependency.targetFilePath)} import ${dependencySymbol}`] : [])
      ];
      const checks =
        dependency && dependencySymbol
          ? [`    for value in [3, 7, 42]:`, `        assert ${symbol}(value) == ${dependencySymbol}(value) + ${dependencySymbol}(value + 1)`]
          : [`    assert ${symbol}(3) == 7`, `    assert ${symbol}(100) == 201`, `    with pytest.raises(ValueError):`, `        ${symbol}(-7)`];
      testCode = [...importLines, ``, `def test_hidden_acceptance():`, ...checks].join("\n");
    } else {
      testCode = `// Hidden suite for ${symbol}`;
    }

    return {
      moduleName: contract.moduleName,
      testFilePath: holdoutFilePath,
      testCode,
      targetedInvariants: contract.boundaryInvariants
    };
  }

  /** Offline-mode stand-in used for demos and the end-to-end test; makes no API calls and records no spend. */
  private buildDeterministicTestSuite(contract: ModuleArchitectureContract, dependencies: ModuleDependency[]): TestSuiteSpec {
    const symbol = contract.contracts[0]?.symbolName ?? "execute_core_pipeline";
    const dependency = dependencies[0]?.contract;
    const dependencySymbol = dependency?.contracts[0]?.symbolName;
    let testCode = "";

    if (dependency && dependencySymbol && contract.language === "typescript") {
      // Mirrors the offline synthesizer: a dependent module returns dep(x) + dep(x + 1).
      testCode = [
        `import * as assert from "assert";`,
        `import { ${symbol} } from "${typescriptImportPath(contract.testFilePath, contract.targetFilePath)}";`,
        `import { ${dependencySymbol} } from "${typescriptImportPath(contract.testFilePath, dependency.targetFilePath)}";`,
        ``,
        `export function runCompositionSuite(): void {`,
        `  for (const input of [0, 1, 10, 25]) {`,
        `    assert.strictEqual(${symbol}(input), ${dependencySymbol}(input) + ${dependencySymbol}(input + 1), "Must combine the dependency at input and input + 1");`,
        `  }`,
        `  let threwOnRejectedInput = false;`,
        `  try {`,
        `    ${symbol}(-1);`,
        `  } catch {`,
        `    threwOnRejectedInput = true;`,
        `  }`,
        `  assert.strictEqual(threwOnRejectedInput, true, "Inputs the dependency rejects must be rejected");`,
        `}`,
        ``,
        `runCompositionSuite();`
      ].join("\n");
    } else if (dependency && dependencySymbol && contract.language === "python") {
      testCode = [
        `import pytest`,
        `from ${pythonModulePath(contract.targetFilePath)} import ${symbol}`,
        `from ${pythonModulePath(dependency.targetFilePath)} import ${dependencySymbol}`,
        ``,
        `def test_composes_dependency():`,
        `    for value in [0, 1, 10, 25]:`,
        `        assert ${symbol}(value) == ${dependencySymbol}(value) + ${dependencySymbol}(value + 1)`,
        `    with pytest.raises(ValueError):`,
        `        ${symbol}(-1)`
      ].join("\n");
    } else if (contract.language === "typescript") {
      testCode = [
        `import * as assert from "assert";`,
        `import { ${symbol} } from "${typescriptImportPath(contract.testFilePath, contract.targetFilePath)}";`,
        ``,
        `export function runBoundarySuite(): void {`,
        `  assert.strictEqual(${symbol}(0), 1, "Zero boundary must return 1");`,
        `  assert.strictEqual(${symbol}(10), 21, "Positive input must compute 2*x + 1");`,
        `  assert.strictEqual(${symbol}(25), 51, "Linear scaling property check");`,
        `  let threwOnNegative = false;`,
        `  try {`,
        `    ${symbol}(-1);`,
        `  } catch {`,
        `    threwOnNegative = true;`,
        `  }`,
        `  assert.strictEqual(threwOnNegative, true, "Negative input guard must throw");`,
        `}`,
        ``,
        `runBoundarySuite();`
      ].join("\n");
    } else if (contract.language === "python") {
      testCode = [
        `import pytest`,
        `from ${pythonModulePath(contract.targetFilePath)} import ${symbol}`,
        ``,
        `def test_boundary_invariants():`,
        `    assert ${symbol}(0) == 1`,
        `    assert ${symbol}(10) == 21`,
        `    with pytest.raises(ValueError):`,
        `        ${symbol}(-1)`
      ].join("\n");
    } else if (contract.language === "rust") {
      testCode = [
        `#[cfg(test)]`,
        `mod tests {`,
        `    use super::*;`,
        `    #[test]`,
        `    fn test_invariants() {`,
        `        assert_eq!(${symbol}(0), Ok(1));`,
        `        assert_eq!(${symbol}(10), Ok(21));`,
        `        assert!(${symbol}(-1).is_err());`,
        `    }`,
        `}`
      ].join("\n");
    } else {
      testCode = `// Boundary suite for ${symbol}`;
    }

    return {
      moduleName: contract.moduleName,
      testFilePath: contract.testFilePath,
      testCode,
      targetedInvariants: contract.boundaryInvariants
    };
  }
}
