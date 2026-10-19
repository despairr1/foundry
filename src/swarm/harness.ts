import * as path from "path";
import { SupportedLanguage } from "../ast/parser";

/** The extension-less relative specifier one workspace TypeScript file uses to import another. */
export function typescriptImportPath(fromFile: string, toFile: string): string {
  const importPath = path.posix.relative(path.posix.dirname(fromFile), toFile).replace(/\.(ts|tsx|js|jsx)$/, "");
  return importPath.startsWith(".") ? importPath : `./${importPath}`;
}

/** The dotted import path of a workspace Python file, as seen from the workspace root. */
export function pythonModulePath(filePath: string): string {
  return filePath.replace(/\.py$/, "").split("/").join(".");
}

/**
 * True when the harness command for `language` runs every test in the
 * workspace (cargo test, go test ./...), so one test run already covers the
 * suites of earlier modules. Keep in sync with SandboxRunner.buildTestCommand.
 */
export function harnessRunsWholeSuite(language: SupportedLanguage): boolean {
  return language === "rust" || language === "go";
}

/**
 * How the sandbox (src/sandbox/runner.ts) executes each language's tests.
 * Both the auditor and the synthesizer are told this so that generated tests
 * and implementations actually run under the harness. Keep in sync with
 * SandboxRunner.buildTestCommand.
 */
export function describeTestHarness(
  language: SupportedLanguage,
  targetFilePath: string,
  testFilePath: string
): string {
  switch (language) {
    case "typescript": {
      const importPath = typescriptImportPath(testFilePath, targetFilePath);
      return [
        `The test file is transpiled to CommonJS and executed directly with Node.js (no Jest/Vitest/Mocha is installed).`,
        `- Use only Node built-ins: \`import assert from "node:assert/strict";\`.`,
        `- Do NOT use describe/it/test/expect globals. Write top-level code or plain functions that you call, so a failing assertion throws and the process exits non-zero.`,
        `- Import the implementation with \`import { ... } from "${importPath}";\` (no file extension).`,
        `- Async checks must be awaited inside an async main() whose rejection sets process.exitCode = 1.`
      ].join("\n");
    }
    case "python":
      return [
        `Tests run from the workspace root with \`python -m pytest -q ${testFilePath}\` (pytest and hypothesis are available).`,
        `- Import the implementation by its dotted module path from the workspace root (for "${targetFilePath}" use \`${pythonModulePath(
          targetFilePath
        )}\`).`,
        `- Write test functions named test_*.`
      ].join("\n");
    case "rust":
      return [
        `Tests run from the workspace root with \`cargo test\` (no network access, so no new crates.io dependencies).`,
        `- The workspace must contain a Cargo.toml; the synthesizer creates it with CREATE_FILE if it is missing.`,
        `- Integration tests under tests/ are separate crates: import the library with \`use <crate_name>::*;\` (crate name from Cargo.toml, hyphens become underscores), not \`use super::*\`.`
      ].join("\n");
    case "go":
      return [
        `Tests run from the workspace root with \`go test ./...\` (no network access).`,
        `- The workspace must contain a go.mod; the synthesizer creates it with CREATE_FILE if it is missing.`,
        `- The test file must end in _test.go, live in the same directory as the code it tests, and use the standard "testing" package.`
      ].join("\n");
    case "cpp":
      return [
        `The test file is compiled together with every non-test .cpp/.cc/.cxx source in the workspace: \`g++ -std=c++20 -I. <test> <sources> -o foundry_test_bin && ./foundry_test_bin\`.`,
        `- The test file defines main() and returns non-zero (or aborts) on failure; implementation files must NOT define main().`,
        `- Share declarations through a header and #include it with a path relative to the workspace root.`
      ].join("\n");
  }
}

export const BRANCH_STRATEGIES: Record<string, string> = {
  Alpha: "Favor the simplest correct design: minimal surface area, straightforward control flow, strict input validation.",
  Beta: "Favor robustness: explicit error types, defensive handling of every boundary and failure mode, clear invariants.",
  Gamma: "Favor performance: efficient data structures and algorithms with tight complexity bounds, while staying correct."
};
