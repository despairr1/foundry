import * as fs from "fs";
import * as path from "path";
import * as ts from "typescript";
import { performance } from "perf_hooks";
import { pathToFileURL } from "url";
import { LSPClient } from "./client";
import { DiagnosticFrame, DiagnosticParser, RawLSPDiagnostic } from "./diagnostic_parser";
import { ASTParser, SupportedLanguage } from "../ast/parser";
import { logger } from "../utils/logger";

interface VirtualFileEntry {
  filePath: string;
  language: SupportedLanguage;
  version: number;
  content: string;
}

const SERVER_COMMANDS: Record<SupportedLanguage, { cmd: string; args: string[] }> = {
  typescript: { cmd: "typescript-language-server", args: ["--stdio"] },
  rust: { cmd: "rust-analyzer", args: [] },
  go: { cmd: "gopls", args: ["serve"] },
  python: { cmd: "pyright-langserver", args: ["--stdio"] },
  cpp: { cmd: "clangd", args: ["--log=error"] }
};

/**
 * TypeScript diagnostics that only mean "dependencies/type packages are not
 * installed in the target directory". Dependencies may only exist inside the
 * sandbox image, so these are reported as warnings instead of blocking errors.
 */
const ENVIRONMENT_DIAGNOSTIC_CODES = new Set([2307, 2792, 7016, 2580, 2591, 2582, 2593, 2584, 2867, 2868]);

const TS_SEMANTIC_EXTENSIONS = /\.(ts|tsx|mts|cts)$/i;

/**
 * In-memory TypeScript project over the virtual file overlay. Files not in the
 * overlay (lib files, node_modules typings) are read from disk, so semantic
 * checks see type mismatches without writing anything to the target directory.
 */
class VirtualTypeScriptProject {
  private rootPath: string;
  private files = new Map<string, { content: string; version: number }>();
  private service: ts.LanguageService;

  constructor(rootPath: string) {
    this.rootPath = rootPath;
    const options = this.loadCompilerOptions();
    const host: ts.LanguageServiceHost = {
      getScriptFileNames: () => Array.from(this.files.keys()).filter((f) => TS_SEMANTIC_EXTENSIONS.test(f)),
      getScriptVersion: (fileName) => String(this.files.get(this.key(fileName))?.version ?? 0),
      getScriptSnapshot: (fileName) => {
        const text = this.files.get(this.key(fileName))?.content ?? ts.sys.readFile(fileName);
        return text === undefined ? undefined : ts.ScriptSnapshot.fromString(text);
      },
      getCurrentDirectory: () => this.rootPath,
      getCompilationSettings: () => options,
      getDefaultLibFileName: (opts) => ts.getDefaultLibFilePath(opts),
      fileExists: (fileName) => this.files.has(this.key(fileName)) || ts.sys.fileExists(fileName),
      readFile: (fileName, encoding) => this.files.get(this.key(fileName))?.content ?? ts.sys.readFile(fileName, encoding),
      readDirectory: ts.sys.readDirectory,
      directoryExists: (dirName) => {
        const prefix = this.key(dirName).replace(/\/?$/, "/");
        for (const key of this.files.keys()) if (key.startsWith(prefix)) return true;
        return ts.sys.directoryExists(dirName);
      },
      getDirectories: ts.sys.getDirectories
    };
    this.service = ts.createLanguageService(host, ts.createDocumentRegistry());
  }

  public sync(workspace: Map<string, string>): void {
    for (const [relPath, content] of workspace) {
      const key = this.key(path.resolve(this.rootPath, relPath));
      const existing = this.files.get(key);
      if (!existing || existing.content !== content) {
        this.files.set(key, { content, version: (existing?.version ?? 0) + 1 });
      }
    }
  }

  public diagnostics(relPath: string): RawLSPDiagnostic[] {
    const fileName = this.key(path.resolve(this.rootPath, relPath));
    const diags = [...this.service.getSyntacticDiagnostics(fileName), ...this.service.getSemanticDiagnostics(fileName)];
    return diags.map((d) => toRawDiagnostic(d, ENVIRONMENT_DIAGNOSTIC_CODES.has(d.code)));
  }

  private key(fileName: string): string {
    const normalized = path.resolve(fileName).replace(/\\/g, "/");
    return ts.sys.useCaseSensitiveFileNames ? normalized : normalized.toLowerCase();
  }

  private loadCompilerOptions(): ts.CompilerOptions {
    const defaults: ts.CompilerOptions = {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      moduleResolution: ts.ModuleResolutionKind.Node10,
      lib: ["lib.es2022.d.ts", "lib.dom.d.ts"],
      strict: true,
      esModuleInterop: true,
      skipLibCheck: true,
      jsx: ts.JsxEmit.ReactJSX,
      noEmit: true
    };
    const tsconfigPath = path.join(this.rootPath, "tsconfig.json");
    if (!fs.existsSync(tsconfigPath)) return defaults;
    const read = ts.readConfigFile(tsconfigPath, ts.sys.readFile);
    if (read.error) return defaults;
    const parsed = ts.parseJsonConfigFileContent(read.config, ts.sys, this.rootPath);
    return { ...parsed.options, noEmit: true, skipLibCheck: true };
  }
}

function toRawDiagnostic(d: ts.Diagnostic, downgrade: boolean): RawLSPDiagnostic {
  let line = 0;
  let character = 0;
  let endLine = 0;
  let endCharacter = 1;
  if (d.file && typeof d.start === "number") {
    const start = d.file.getLineAndCharacterOfPosition(d.start);
    const end = d.file.getLineAndCharacterOfPosition(d.start + (d.length ?? 1));
    line = start.line;
    character = start.character;
    endLine = end.line;
    endCharacter = end.character;
  }
  const isError = d.category === ts.DiagnosticCategory.Error && !downgrade;
  return {
    range: { start: { line, character }, end: { line: endLine, character: endCharacter } },
    severity: isError ? 1 : 2,
    code: `TS${d.code}`,
    source: "ts-in-memory",
    message: ts.flattenDiagnosticMessageText(d.messageText, "\n")
  };
}

export class LSPMultiplexer {
  private rootPath: string;
  private astParser: ASTParser;
  private diagParser: DiagnosticParser;
  private clients = new Map<SupportedLanguage, LSPClient>();
  private virtualFs = new Map<string, VirtualFileEntry>();
  private tsProject: VirtualTypeScriptProject | null = null;
  private timeoutMs: number;
  private lastLatencyMs = 0;

  constructor(rootPath: string, astParser: ASTParser, timeoutMs = 1200) {
    this.rootPath = path.resolve(rootPath);
    this.astParser = astParser;
    this.diagParser = new DiagnosticParser();
    this.timeoutMs = timeoutMs;
  }

  public updateVirtualFile(filePath: string, content: string): VirtualFileEntry {
    const normalizedPath = (path.isAbsolute(filePath) ? path.relative(this.rootPath, filePath) : filePath)
      .split(path.sep)
      .join("/");
    const existing = this.virtualFs.get(normalizedPath);
    const language = this.astParser.detectLanguage(normalizedPath);
    const entry: VirtualFileEntry = {
      filePath: normalizedPath,
      language,
      version: existing ? existing.version + 1 : 1,
      content
    };
    this.virtualFs.set(normalizedPath, entry);
    return entry;
  }

  public getVirtualFiles(): Map<string, string> {
    const out = new Map<string, string>();
    for (const [k, v] of this.virtualFs.entries()) {
      out.set(k, v.content);
    }
    return out;
  }

  /**
   * Validates one file of a virtual workspace. `workspace` supplies the other
   * files the target imports so semantic checks resolve against the patched
   * overlay rather than whatever is on disk.
   */
  public async validateFile(
    filePath: string,
    content: string,
    workspace?: Map<string, string>
  ): Promise<DiagnosticFrame[]> {
    const start = performance.now();
    const entry = this.updateVirtualFile(filePath, content);
    const frames: DiagnosticFrame[] = [];
    const uri = pathToFileURL(path.resolve(this.rootPath, entry.filePath)).href;

    const parsedAst = this.astParser.parse(content, entry.language, entry.filePath);
    if (parsedAst.hasSyntaxError) {
      for (const errNode of parsedAst.syntaxErrorNodes) {
        frames.push({
          filePath: entry.filePath,
          line: errNode.startPosition.row + 1,
          column: errNode.startPosition.column + 1,
          endLine: errNode.endPosition.row + 1,
          endColumn: errNode.endPosition.column + 1,
          severity: "ERROR",
          code: "AST_SYNTAX",
          source: "tree-sitter",
          message: `Syntax error near token "${errNode.text.slice(0, 32)}"`,
          codeSnippet: errNode.text
        });
      }
    }

    if (entry.language === "typescript") {
      frames.push(
        ...this.diagParser.parseLSPPublishDiagnostics(uri, this.runTypeScriptCheck(entry.filePath, content, workspace), content)
      );
    }

    const client = this.getOrCreateClient(entry.language);
    if (client.isAvailable()) {
      const rawDiags = await client.syncDocumentAndAwaitDiagnostics(entry.filePath, content, this.timeoutMs);
      if (rawDiags.length > 0) {
        frames.push(...this.diagParser.parseLSPPublishDiagnostics(uri, rawDiags, content));
      }
    }

    this.lastLatencyMs = performance.now() - start;
    logger.updateState({ lspLatencyMs: this.lastLatencyMs });
    return this.deduplicateDiagnostics(frames).map((f) => ({ ...f, filePath: entry.filePath }));
  }

  public async validateWorkspace(files: Map<string, string>): Promise<DiagnosticFrame[]> {
    const allFrames: DiagnosticFrame[] = [];
    for (const [filePath, content] of files.entries()) {
      if (this.astParser.languageForPath(filePath) === null) continue;
      allFrames.push(...(await this.validateFile(filePath, content, files)));
    }
    return allFrames;
  }

  public getLastLatencyMs(): number {
    return this.lastLatencyMs;
  }

  public async shutdownAll(): Promise<void> {
    for (const client of this.clients.values()) {
      await client.shutdown();
    }
    this.clients.clear();
  }

  private getOrCreateClient(language: SupportedLanguage): LSPClient {
    let client = this.clients.get(language);
    if (!client) {
      const spec = SERVER_COMMANDS[language];
      client = new LSPClient(spec.cmd, spec.args, this.rootPath, language);
      this.clients.set(language, client);
    }
    return client;
  }

  private runTypeScriptCheck(
    relPath: string,
    content: string,
    workspace?: Map<string, string>
  ): RawLSPDiagnostic[] {
    if (!TS_SEMANTIC_EXTENSIONS.test(relPath)) {
      return this.runSyntacticJavaScriptCheck(relPath, content);
    }
    try {
      if (!this.tsProject) this.tsProject = new VirtualTypeScriptProject(this.rootPath);
      const overlay = new Map(workspace ?? []);
      overlay.set(relPath, content);
      this.tsProject.sync(overlay);
      return this.tsProject.diagnostics(relPath);
    } catch (err) {
      logger.warn("LSPMultiplexer", `In-memory TypeScript check failed (${String(err)}); falling back to syntax-only.`);
      return this.runSyntacticJavaScriptCheck(relPath, content);
    }
  }

  private runSyntacticJavaScriptCheck(filePath: string, content: string): RawLSPDiagnostic[] {
    const transpileResult = ts.transpileModule(content, {
      fileName: filePath,
      reportDiagnostics: true,
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, allowJs: true }
    });
    return (transpileResult.diagnostics ?? []).map((d) => toRawDiagnostic(d, false));
  }

  private deduplicateDiagnostics(frames: DiagnosticFrame[]): DiagnosticFrame[] {
    const seen = new Set<string>();
    const unique: DiagnosticFrame[] = [];
    for (const f of frames) {
      const key = `${f.line}:${f.column}:${f.message}`;
      if (!seen.has(key)) {
        seen.add(key);
        unique.push(f);
      }
    }
    return unique;
  }
}
