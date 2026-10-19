import { fileURLToPath } from "url";

export interface RawLSPDiagnostic {
  range: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
  severity?: number;
  code?: string | number;
  source?: string;
  message: string;
}

export interface DiagnosticFrame {
  filePath: string;
  line: number;
  column: number;
  endLine: number;
  endColumn: number;
  severity: "ERROR" | "WARNING" | "INFO" | "HINT";
  code: string;
  source: string;
  message: string;
  codeSnippet: string;
}

export class DiagnosticParser {
  public parseLSPPublishDiagnostics(
    uri: string,
    diagnostics: RawLSPDiagnostic[],
    virtualSource?: string
  ): DiagnosticFrame[] {
    let filePath = uri;
    try {
      filePath = fileURLToPath(uri);
    } catch {
      filePath = uri.replace(/^file:\/\//, "");
    }
    const lines = virtualSource ? virtualSource.split(/\r?\n/) : [];

    return diagnostics.map((diag) => {
      const severityMap: Record<number, DiagnosticFrame["severity"]> = {
        1: "ERROR",
        2: "WARNING",
        3: "INFO",
        4: "HINT"
      };
      const severity = severityMap[diag.severity ?? 1] ?? "ERROR";
      const lineIdx = diag.range.start.line;
      const snippetStart = Math.max(0, lineIdx - 1);
      const snippetEnd = Math.min(lines.length - 1, lineIdx + 1);
      const snippetLines: string[] = [];

      for (let i = snippetStart; i <= snippetEnd; i += 1) {
        if (lines[i] !== undefined) {
          const marker = i === lineIdx ? ">" : " ";
          snippetLines.push(`${marker} ${i + 1} | ${lines[i]}`);
        }
      }

      return {
        filePath,
        line: lineIdx + 1,
        column: diag.range.start.character + 1,
        endLine: diag.range.end.line + 1,
        endColumn: diag.range.end.character + 1,
        severity,
        code: diag.code !== undefined ? String(diag.code) : "LSP_DIAG",
        source: diag.source ?? "language-server",
        message: diag.message,
        codeSnippet: snippetLines.join("\n")
      };
    });
  }

  public formatFramesForPrompt(frames: DiagnosticFrame[]): string {
    const errorsOnly = frames.filter((f) => f.severity === "ERROR");
    if (errorsOnly.length === 0) {
      return "ZERO_LSP_ERRORS";
    }
    return errorsOnly
      .map(
        (f) =>
          `[${f.severity}] ${f.filePath}:${f.line}:${f.column} (${f.source}:${f.code}) -> ${f.message}\n${f.codeSnippet}`
      )
      .join("\n\n");
  }
}