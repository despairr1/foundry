export interface StackTraceFrame {
  filePath: string;
  line: number;
  column: number;
  functionName: string;
  rawLine: string;
}

export interface ParsedExecutionResult {
  passed: boolean;
  exitCode: number;
  passedTestsCount: number;
  failedTestsCount: number;
  compilerErrors: string[];
  stackFrames: StackTraceFrame[];
  summaryText: string;
}

export class SandboxLogParser {
  public parseExecutionOutput(
    exitCode: number,
    stdout: string,
    stderr: string
  ): ParsedExecutionResult {
    const combined = `${stdout}\n${stderr}`.trim();
    const lines = combined.split(/\r?\n/);

    const compilerErrors: string[] = [];
    const stackFrames: StackTraceFrame[] = [];

    // Allows Windows drive letters ("C:\...") in file paths.
    const nodeFrameRegex = /at\s+(?:(.+?)\s+\()?((?:[A-Za-z]:)?[^():]+):(\d+):(\d+)\)?/;
    const rustFrameRegex = /-->\s+([^:]+):(\d+):(\d+)/;
    const pyFrameRegex = /File\s+"([^"]+)",\s+line\s+(\d+),\s+in\s+([^\s]+)/;

    for (const line of lines) {
      if (
        line.includes("error TS") ||
        line.includes("error[E") ||
        line.includes("SyntaxError:") ||
        line.includes("AssertionError") ||
        line.includes("FAILED") ||
        line.includes("panicked at") ||
        line.includes("--- FAIL") ||
        /^\s*E\s{2,}/.test(line) ||
        /\berror:/.test(line)
      ) {
        compilerErrors.push(line.trim());
      }

      const nodeMatch = nodeFrameRegex.exec(line);
      if (nodeMatch) {
        stackFrames.push({
          functionName: nodeMatch[1] ?? "<anonymous>",
          filePath: nodeMatch[2],
          line: parseInt(nodeMatch[3], 10),
          column: parseInt(nodeMatch[4], 10),
          rawLine: line.trim()
        });
        continue;
      }

      const rustMatch = rustFrameRegex.exec(line);
      if (rustMatch) {
        stackFrames.push({
          functionName: "rust_item",
          filePath: rustMatch[1],
          line: parseInt(rustMatch[2], 10),
          column: parseInt(rustMatch[3], 10),
          rawLine: line.trim()
        });
        continue;
      }

      const pyMatch = pyFrameRegex.exec(line);
      if (pyMatch) {
        stackFrames.push({
          functionName: pyMatch[3],
          filePath: pyMatch[1],
          line: parseInt(pyMatch[2], 10),
          column: 1,
          rawLine: line.trim()
        });
      }
    }

    const passed = exitCode === 0;
    const failedTestsCount = passed ? 0 : Math.max(1, compilerErrors.length);
    const passedTestsCount = passed ? 1 : 0;

    const summaryText = passed
      ? "SANDBOX_GREEN: All compilation checks and test suites passed."
      : [
          `Exit Code: ${exitCode}`,
          `Compiler/Assertion Errors:\n${compilerErrors.slice(0, 8).join("\n")}`,
          `Stack Trace:\n${stackFrames
            .slice(0, 6)
            .map((f) => `  ${f.functionName} (${f.filePath}:${f.line}:${f.column})`)
            .join("\n")}`,
          `Raw Output Tail:\n${lines.slice(-12).join("\n")}`
        ].join("\n\n");

    return {
      passed,
      exitCode,
      passedTestsCount,
      failedTestsCount,
      compilerErrors,
      stackFrames,
      summaryText
    };
  }
}