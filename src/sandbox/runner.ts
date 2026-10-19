import { exec, execFile } from "child_process";
import { promisify } from "util";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import * as ts from "typescript";
import { SupportedLanguage } from "../ast/parser";
import { ParsedExecutionResult, SandboxLogParser } from "./log_parser";
import { logger } from "../utils/logger";
import { normalizeWorkspacePath, resolveInsideRoot } from "../utils/paths";

const execFileAsync = promisify(execFile);
const execAsync = promisify(exec);

export interface SandboxOptions {
  timeoutMs?: number;
  dockerMemoryMb?: number;
  dockerCpus?: number;
}

/**
 * Per-test-run configuration. `timeout` is interpreted as milliseconds and is stored verbatim;
 * validation happens only in resolveTestTimeoutMs.
 */
export interface TestConfig {
  timeout?: number;
}

/** Default child-process timeout (120 seconds). */
export const DEFAULT_TEST_TIMEOUT_MS: number = 120000;

/**
 * Pure, defensively-validated resolver for the child-process timeout.
 * Returns config.timeout only when it is a finite number strictly greater than zero; otherwise
 * returns DEFAULT_TEST_TIMEOUT_MS. Never throws and never coerces non-number values.
 */
export function resolveTestTimeoutMs(config: Pick<TestConfig, "timeout"> | undefined | null): number {
  if (config === null || config === undefined || typeof config !== "object") {
    return DEFAULT_TEST_TIMEOUT_MS;
  }
  const candidate: unknown = (config as { timeout?: unknown }).timeout;
  if (typeof candidate === "number" && Number.isFinite(candidate) && candidate > 0) {
    return candidate;
  }
  return DEFAULT_TEST_TIMEOUT_MS;
}


interface ExecFailure {
  code?: number | string;
  killed?: boolean;
  signal?: string;
  stdout?: string;
  stderr?: string;
  message?: string;
}

const CPP_SOURCE = /\.(cpp|cc|cxx)$/i;

export class SandboxRunner {
  private sandboxImageName: string;
  private timeoutMs: number;
  private dockerMemoryMb: number;
  private dockerCpus: number;
  private logParser: SandboxLogParser;
  private dockerAvailable: boolean | null = null;
  private dependencyRoot: string | null = null;

  /**
   * The project the tests belong to. Local TypeScript runs resolve installed packages from its
   * node_modules (the sandbox copy only contains source files), so code that imports
   * dependencies can be tested, which matters most when Foundry tests its own source.
   */
  public setDependencyRoot(projectDir: string | null): void {
    this.dependencyRoot = projectDir;
  }

  constructor(sandboxImageName: string, options: SandboxOptions = {}) {
    this.sandboxImageName = sandboxImageName;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TEST_TIMEOUT_MS;
    this.dockerMemoryMb = options.dockerMemoryMb ?? 1024;
    this.dockerCpus = options.dockerCpus ?? 2.0;
    this.logParser = new SandboxLogParser();
  }

  public async executeWorkspaceTests(
    files: Map<string, string>,
    language: SupportedLanguage,
    testFilePath: string,
    config?: TestConfig | null
  ): Promise<ParsedExecutionResult> {
    // Resolved exactly once per run; callers that omit `timeout` get the instance default (120000 ms unless overridden).
    const timeoutMs = resolveTestTimeoutMs({ timeout: config?.timeout ?? this.timeoutMs });
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-sandbox-"));

    try {
      const safeTestPath = normalizeWorkspacePath(testFilePath);
      for (const [relPath, content] of files.entries()) {
        const dest = resolveInsideRoot(tempDir, relPath);
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        fs.writeFileSync(dest, content, "utf8");
      }

      const useDocker = await this.isDockerImageReady();
      if (useDocker) {
        return await this.runInsideDocker(tempDir, language, safeTestPath, files, timeoutMs);
      }
      return await this.runIsolatedLocalSubprocess(tempDir, language, safeTestPath, files, timeoutMs);
    } catch (err) {
      return this.logParser.parseExecutionOutput(1, "", `Sandbox setup failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      try {
        fs.rmSync(tempDir, { recursive: true, force: true });
      } catch {
        // Temp cleanup is best-effort.
      }
    }
  }

  private async isDockerImageReady(): Promise<boolean> {
    if (process.env.FOUNDRY_FORCE_LOCAL_SANDBOX === "true") {
      return false;
    }
    if (this.dockerAvailable !== null) {
      return this.dockerAvailable;
    }
    try {
      await execFileAsync("docker", ["image", "inspect", this.sandboxImageName], { timeout: 5000, windowsHide: true });
      this.dockerAvailable = true;
      logger.info("SandboxRunner", `Using Docker sandbox image ${this.sandboxImageName}.`);
    } catch {
      this.dockerAvailable = false;
      logger.warn(
        "SandboxRunner",
        `Docker image ${this.sandboxImageName} unavailable; generated tests will run directly on this machine WITHOUT isolation. ` +
          `Build it with: docker build -f Dockerfile.sandbox -t ${this.sandboxImageName} .`
      );
    }
    return this.dockerAvailable;
  }

  private async runInsideDocker(
    workspaceDir: string,
    language: SupportedLanguage,
    testFilePath: string,
    files: Map<string, string>,
    timeoutMs: number
  ): Promise<ParsedExecutionResult> {
    const command = this.buildTestCommand(language, testFilePath, files, "docker");
    try {
      const { stdout, stderr } = await execFileAsync(
        "docker",
        [
          "run",
          "--rm",
          "--network",
          "none",
          "--memory",
          `${this.dockerMemoryMb}m`,
          "--cpus",
          String(this.dockerCpus),
          "--mount",
          `type=bind,source=${workspaceDir},target=/workspace`,
          "-w",
          "/workspace",
          this.sandboxImageName,
          "/bin/bash",
          "-c",
          command
        ],
        { timeout: timeoutMs, maxBuffer: 10 * 1024 * 1024, windowsHide: true }
      );
      return this.logParser.parseExecutionOutput(0, stdout, stderr);
    } catch (err: unknown) {
      return this.failureResult(err as ExecFailure, "Container execution failed", timeoutMs);
    }
  }

  private async runIsolatedLocalSubprocess(
    workspaceDir: string,
    language: SupportedLanguage,
    testFilePath: string,
    files: Map<string, string>,
    timeoutMs: number
  ): Promise<ParsedExecutionResult> {
    if (language === "typescript") {
      return await this.compileAndRunTypeScriptLocally(workspaceDir, testFilePath, timeoutMs);
    }

    const command = this.buildTestCommand(language, testFilePath, files, "local");
    try {
      // exec uses cmd.exe on Windows and /bin/sh elsewhere; paths are pre-validated to shell-safe characters.
      const { stdout, stderr } = await execAsync(command, {
        cwd: workspaceDir,
        timeout: timeoutMs,
        maxBuffer: 10 * 1024 * 1024,
        windowsHide: true
      });
      return this.logParser.parseExecutionOutput(0, stdout, stderr);
    } catch (err: unknown) {
      return this.failureResult(err as ExecFailure, "Local execution error", timeoutMs);
    }
  }

  private async compileAndRunTypeScriptLocally(
    workspaceDir: string,
    testFilePath: string,
    timeoutMs: number
  ): Promise<ParsedExecutionResult> {
    const allTsFiles: string[] = [];
    const walk = (dir: string): void => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "node_modules") walk(full);
        } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts")) {
          allTsFiles.push(full);
        }
      }
    };
    walk(workspaceDir);

    for (const file of allTsFiles) {
      const src = fs.readFileSync(file, "utf8");
      const transpiled = ts.transpileModule(src, {
        fileName: file,
        reportDiagnostics: true,
        compilerOptions: {
          target: ts.ScriptTarget.ES2022,
          module: ts.ModuleKind.CommonJS,
          esModuleInterop: true,
          jsx: ts.JsxEmit.ReactJSX
        }
      });

      if (transpiled.diagnostics && transpiled.diagnostics.length > 0) {
        const msg = transpiled.diagnostics
          .map((d) => `${path.relative(workspaceDir, file)}: ${ts.flattenDiagnosticMessageText(d.messageText, "\n")}`)
          .join("\n");
        return this.logParser.parseExecutionOutput(1, "", `error TS: ${msg}`);
      }

      fs.writeFileSync(file.replace(/\.tsx?$/, ".js"), transpiled.outputText, "utf8");
    }

    // Compiled output is CommonJS; make sure an ancestor package.json with "type": "module" cannot flip it to ESM.
    fs.writeFileSync(path.join(workspaceDir, "package.json"), JSON.stringify({ type: "commonjs" }), "utf8");

    const compiledTestJs = path.join(workspaceDir, testFilePath.replace(/\.tsx?$/, ".js"));
    const nodeModules = this.dependencyRoot ? path.join(this.dependencyRoot, "node_modules") : null;
    const env =
      nodeModules && fs.existsSync(nodeModules)
        ? { ...process.env, NODE_PATH: [nodeModules, process.env.NODE_PATH].filter(Boolean).join(path.delimiter) }
        : process.env;
    try {
      const { stdout, stderr } = await execFileAsync(process.execPath, [compiledTestJs], {
        cwd: workspaceDir,
        env,
        timeout: timeoutMs,
        maxBuffer: 10 * 1024 * 1024,
        windowsHide: true
      });
      return this.logParser.parseExecutionOutput(0, stdout, stderr);
    } catch (err: unknown) {
      return this.failureResult(err as ExecFailure, "Assertion failure", timeoutMs);
    }
  }

  private failureResult(err: ExecFailure, fallbackMessage: string, timeoutMs: number = this.timeoutMs): ParsedExecutionResult {
    const timedOut = err.killed === true && err.signal === "SIGTERM";
    const stderr = [
      err.stderr ?? "",
      timedOut ? `Test run exceeded the ${timeoutMs}ms sandbox timeout and was killed.` : "",
      !err.stdout && !err.stderr ? err.message ?? fallbackMessage : ""
    ]
      .filter(Boolean)
      .join("\n");
    return this.logParser.parseExecutionOutput(typeof err.code === "number" ? err.code : 1, err.stdout ?? "", stderr);
  }

  /** Must match the conventions described to the models in src/swarm/harness.ts. */
  private buildTestCommand(
    language: SupportedLanguage,
    testFilePath: string,
    files: Map<string, string>,
    target: "docker" | "local"
  ): string {
    const python = target === "docker" ? "python3" : process.platform === "win32" ? "python" : "python3";
    switch (language) {
      case "typescript":
        // Force CommonJS so a workspace tsconfig targeting ESM cannot break the harness (mirrors the local path).
        return `TS_NODE_COMPILER_OPTIONS='{"module":"commonjs","esModuleInterop":true}' ts-node --transpile-only ${testFilePath}`;
      case "rust":
        return `cargo test`;
      case "python":
        return `${python} -m pytest -q ${testFilePath}`;
      case "go":
        return `go test ./...`;
      case "cpp": {
        const sources = Array.from(files.keys())
          .filter((f) => CPP_SOURCE.test(f) && f !== testFilePath && !/(^|\/)tests?\/|_test\.|\.test\./.test(f))
          .map((f) => normalizeWorkspacePath(f));
        const binary = process.platform === "win32" && target === "local" ? "foundry_test_bin.exe" : "./foundry_test_bin";
        return `g++ -std=c++20 -I. ${testFilePath} ${sources.join(" ")} -o ${binary} && ${binary}`;
      }
    }
  }
}
