import { FoundryConfig } from "../config";
import { EconomicsEngine } from "../engine/economics";
import { ModuleArchitectureContract, ModuleDependency, describeModuleDependencies } from "./claude_architect";
import { describeTestHarness, pythonModulePath, typescriptImportPath } from "./harness";
import { completeWithGemini } from "./providers";

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

  public async generateMutationLethalTests(
    contract: ModuleArchitectureContract,
    feedbackFromMutationEngine?: string,
    dependencies: ModuleDependency[] = []
  ): Promise<TestSuiteSpec> {
    if (this.config.offlineMode) {
      return this.buildDeterministicTestSuite(contract, dependencies);
    }

    const prompt = [
      `You are the Invariant Auditor of an autonomous software engineering engine.`,
      `Write property-based and boundary tests for module "${contract.moduleName}" (${contract.language}) that will kill operator-inversion, constant-perturbation, and guard-deletion mutants.`,
      `Implementation path: ${contract.targetFilePath}`,
      `Test file path: ${contract.testFilePath}`,
      `Formal contract:\n${JSON.stringify(contract, null, 2)}`,
      `Test harness:\n${describeTestHarness(contract.language, contract.targetFilePath, contract.testFilePath)}`,
      ...(dependencies.length > 0
        ? [
            `This module builds on earlier modules that are already implemented and verified. Test this module's own contract, using the dependencies' real behavior when computing expected values:\n${describeModuleDependencies(
              dependencies
            )}`
          ]
        : []),
      feedbackFromMutationEngine
        ? `These mutants survived the previous suite. Add assertions that fail on each of them while still passing on a correct implementation:\n${feedbackFromMutationEngine}`
        : `Use exact-value assertions at every boundary named in the contract (including just inside and just outside each limit), and assert that every precondition violation is rejected.`,
      `Test only behavior the contract specifies. Return the complete test file in a single fenced code block and nothing else.`
    ].join("\n\n");

    const testCode = await completeWithGemini<string>(this.config, this.economics, {
      label: `GeminiAuditor[${contract.moduleName}]`,
      prompt,
      parse: (text) => {
        const code = stripMarkdownCodeFences(text);
        if (!code) throw new Error("Gemini returned an empty test suite");
        return code;
      }
    });

    return {
      moduleName: contract.moduleName,
      testFilePath: contract.testFilePath,
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
