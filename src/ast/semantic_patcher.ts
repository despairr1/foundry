import { ASTNodeInfo, ASTParser, SupportedLanguage } from "./parser";
import { logger } from "../utils/logger";

export type SemanticPatchAction =
  | "REPLACE_FUNCTION"
  | "REPLACE_CLASS"
  | "REPLACE_STRUCT"
  | "REPLACE_NODE"
  | "INSERT_AFTER"
  | "INSERT_BEFORE"
  | "DELETE_NODE"
  | "UPSERT_IMPORT"
  | "CREATE_FILE";

export interface SemanticPatch {
  filePath: string;
  action: SemanticPatchAction;
  target: string;
  content: string;
}

export interface PatchApplicationResult {
  success: boolean;
  filePath: string;
  updatedSource: string;
  byteOffsetStart: number;
  byteOffsetEnd: number;
  hasSyntaxErrorAfterPatch: boolean;
  errorDetails?: string;
}

const REPLACE_ACTIONS = new Set<SemanticPatchAction>(["REPLACE_FUNCTION", "REPLACE_CLASS", "REPLACE_STRUCT", "REPLACE_NODE"]);

const WRAPPER_PREFIX: Record<NonNullable<ASTNodeInfo["wrapperKind"]>, RegExp> = {
  export: /^\s*export\b/,
  decorator: /^\s*@/,
  attribute: /^\s*#\[/
};

/**
 * When the replacement omits the wrapper the original declaration had (e.g.
 * `export`, decorators, Rust attributes), only the bare declaration is
 * swapped so the wrapper is preserved.
 */
function replacementRange(node: ASTNodeInfo, content: string): { start: number; end: number } {
  if (!node.wrapperKind || WRAPPER_PREFIX[node.wrapperKind].test(content)) {
    return { start: node.startIndex, end: node.endIndex };
  }
  return { start: node.declStartIndex, end: node.declEndIndex };
}

export function importInsertionOffset(source: string, language: SupportedLanguage): number {
  if (language === "go") {
    const pkg = /^package\s+\w+[^\n]*\n?/m.exec(source);
    if (pkg) return pkg.index + pkg[0].length;
  }
  if (source.startsWith("#!")) {
    const newline = source.indexOf("\n");
    return newline === -1 ? source.length : newline + 1;
  }
  return 0;
}

/**
 * Compact patch shape accepted by `applyPatch(source, patch, language?)`.
 * `action` may be a full SemanticPatchAction or the shorthand "REPLACE".
 */
export interface SimpleSemanticPatch {
  filePath: string;
  action: SemanticPatchAction | "REPLACE" | string;
  targetSymbol?: string;
  newCode: string;
}

/**
 * Compact result shape returned for SimpleSemanticPatch inputs.
 * success === true  => typeof updatedSource === "string"
 * success === false => typeof error === "string" && error.length > 0
 */
export interface SimplePatchResult {
  success: boolean;
  updatedSource?: string;
  error?: string;
}

function isSimplePatch(value: unknown): value is SimpleSemanticPatch {
  return typeof value === "object" && value !== null && "newCode" in (value as Record<string, unknown>);
}

function normalizeSimpleAction(action: string): SemanticPatchAction {
  switch (action) {
    case "REPLACE_FUNCTION":
    case "REPLACE_CLASS":
    case "REPLACE_STRUCT":
    case "REPLACE_NODE":
    case "INSERT_AFTER":
    case "INSERT_BEFORE":
    case "DELETE_NODE":
    case "UPSERT_IMPORT":
    case "CREATE_FILE":
      return action;
    default:
      return "REPLACE_NODE";
  }
}

function toSimpleResult(result: PatchApplicationResult): SimplePatchResult {
  if (result.success) {
    return { success: true, updatedSource: result.updatedSource };
  }
  const error =
    result.errorDetails && result.errorDetails.length > 0
      ? result.errorDetails
      : `Failed to apply patch to ${result.filePath}`;
  return { success: false, error };
}


export class SemanticPatcher {
  private parser: ASTParser;

  constructor(parser?: ASTParser) {
    this.parser = parser ?? new ASTParser();
  }

  public applyPatch(currentSource: string, patch: SimpleSemanticPatch, language?: SupportedLanguage): SimplePatchResult;
  public applyPatch(currentSource: string, language: SupportedLanguage, patch: SemanticPatch): PatchApplicationResult;
  public applyPatch(
    currentSource: string,
    second: SupportedLanguage | SimpleSemanticPatch,
    third?: SemanticPatch | SupportedLanguage
  ): PatchApplicationResult | SimplePatchResult {
    if (isSimplePatch(second)) {
      return this.applySimplePatch(currentSource, second, third as SupportedLanguage | undefined);
    }
    return this.applyStructuralPatch(currentSource, second as SupportedLanguage, third as SemanticPatch);
  }

  private resolveLanguage(filePath: string, language?: SupportedLanguage): SupportedLanguage {
    if (language) return language;
    const detected = this.parser.languageForPath(filePath);
    if (detected) return detected;
    return "typescript" as SupportedLanguage;
  }

  private applySimplePatch(currentSource: string, patch: SimpleSemanticPatch, language?: SupportedLanguage): SimplePatchResult {
    try {
      const lang = this.resolveLanguage(patch.filePath, language);
      const action = normalizeSimpleAction(String(patch.action));
      const target = patch.targetSymbol ?? "";
      const structural: SemanticPatch = {
        filePath: patch.filePath,
        action,
        target,
        content: patch.newCode
      };

      if (action !== "CREATE_FILE" && action !== "UPSERT_IMPORT") {
        const node = this.parser.findTargetNode(currentSource, lang, target);
        if (!node) {
          return {
            success: false,
            error: `Structural AST node "${target}" not found in ${patch.filePath}`
          };
        }
      }

      return toSimpleResult(this.applyStructuralPatch(currentSource, lang, structural));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return { success: false, error: message.length > 0 ? message : "Unknown patch error" };
    }
  }

  private applyStructuralPatch(currentSource: string, language: SupportedLanguage, patch: SemanticPatch): PatchApplicationResult {
    if (patch.action === "CREATE_FILE") {
      const parsed = this.parser.parse(patch.content, language, patch.filePath);
      return {
        success: !parsed.hasSyntaxError,
        filePath: patch.filePath,
        updatedSource: patch.content,
        byteOffsetStart: 0,
        byteOffsetEnd: patch.content.length,
        hasSyntaxErrorAfterPatch: parsed.hasSyntaxError,
        errorDetails: parsed.hasSyntaxError
          ? `Syntax error in created file at line ${(parsed.syntaxErrorNodes[0]?.startPosition.row ?? 0) + 1}`
          : undefined
      };
    }

    if (patch.action === "UPSERT_IMPORT") {
      const trimmedImport = patch.content.trim();
      if (!trimmedImport || currentSource.includes(trimmedImport)) {
        return {
          success: true,
          filePath: patch.filePath,
          updatedSource: currentSource,
          byteOffsetStart: 0,
          byteOffsetEnd: 0,
          hasSyntaxErrorAfterPatch: false
        };
      }
      const offset = importInsertionOffset(currentSource, language);
      const updatedSource = `${currentSource.slice(0, offset)}${trimmedImport}\n${currentSource.slice(offset)}`;
      const parsed = this.parser.parse(updatedSource, language, patch.filePath);
      return {
        success: !parsed.hasSyntaxError,
        filePath: patch.filePath,
        updatedSource,
        byteOffsetStart: offset,
        byteOffsetEnd: offset + trimmedImport.length,
        hasSyntaxErrorAfterPatch: parsed.hasSyntaxError,
        errorDetails: parsed.hasSyntaxError ? `Import "${trimmedImport}" introduced syntax errors.` : undefined
      };
    }

    const targetNode = this.parser.findTargetNode(currentSource, language, patch.target);

    if (!targetNode) {
      if (REPLACE_ACTIONS.has(patch.action)) {
        logger.info(
          "ASTPatcher",
          `Target node "${patch.target}" not found in ${patch.filePath}; appending declaration to module tail.`
        );
        const separator = currentSource.length === 0 ? "" : currentSource.endsWith("\n") ? "\n" : "\n\n";
        const updatedSource = `${currentSource}${separator}${patch.content}\n`;
        const parsed = this.parser.parse(updatedSource, language, patch.filePath);
        return {
          success: !parsed.hasSyntaxError,
          filePath: patch.filePath,
          updatedSource,
          byteOffsetStart: currentSource.length,
          byteOffsetEnd: updatedSource.length,
          hasSyntaxErrorAfterPatch: parsed.hasSyntaxError,
          errorDetails: parsed.hasSyntaxError ? `Appended node "${patch.target}" introduced syntax errors.` : undefined
        };
      }

      return {
        success: false,
        filePath: patch.filePath,
        updatedSource: currentSource,
        byteOffsetStart: -1,
        byteOffsetEnd: -1,
        hasSyntaxErrorAfterPatch: false,
        errorDetails: `Structural AST node "${patch.target}" not found in ${patch.filePath}`
      };
    }

    let updatedSource = currentSource;
    let { startIndex, endIndex } = targetNode;

    switch (patch.action) {
      case "REPLACE_FUNCTION":
      case "REPLACE_CLASS":
      case "REPLACE_STRUCT":
      case "REPLACE_NODE": {
        const range = replacementRange(targetNode, patch.content);
        startIndex = range.start;
        endIndex = range.end;
        updatedSource = currentSource.slice(0, startIndex) + patch.content + currentSource.slice(endIndex);
        break;
      }
      case "INSERT_BEFORE": {
        updatedSource = currentSource.slice(0, startIndex) + patch.content + "\n\n" + currentSource.slice(startIndex);
        break;
      }
      case "INSERT_AFTER": {
        updatedSource = currentSource.slice(0, endIndex) + "\n\n" + patch.content + currentSource.slice(endIndex);
        break;
      }
      case "DELETE_NODE": {
        updatedSource = currentSource.slice(0, startIndex) + currentSource.slice(endIndex);
        break;
      }
    }

    const postParse = this.parser.parse(updatedSource, language, patch.filePath);
    return {
      success: !postParse.hasSyntaxError,
      filePath: patch.filePath,
      updatedSource,
      byteOffsetStart: startIndex,
      byteOffsetEnd: endIndex,
      hasSyntaxErrorAfterPatch: postParse.hasSyntaxError,
      errorDetails: postParse.hasSyntaxError
        ? `Tree-sitter detected syntax error after patching "${patch.target}" at line ${
            (postParse.syntaxErrorNodes[0]?.startPosition.row ?? 0) + 1
          }`
        : undefined
    };
  }

  /**
   * Applies patches in order. A patch that fails (syntax error or missing
   * target) leaves its file untouched and is reported in `results`.
   * Non-code files (manifests, configs) only support CREATE_FILE.
   * The returned map is exposed both as `files` and `updatedFiles`.
   */
  public applyBatch(
    virtualFiles: Map<string, string>,
    patches: SimpleSemanticPatch[]
  ): { files: Map<string, string>; updatedFiles: Map<string, string>; results: SimplePatchResult[] };
  public applyBatch(
    virtualFiles: Map<string, string>,
    patches: SemanticPatch[]
  ): { files: Map<string, string>; updatedFiles: Map<string, string>; results: PatchApplicationResult[] };
  public applyBatch(
    virtualFiles: Map<string, string>,
    patches: Array<SemanticPatch | SimpleSemanticPatch>
  ): {
    files: Map<string, string>;
    updatedFiles: Map<string, string>;
    results: Array<PatchApplicationResult | SimplePatchResult>;
  } {
    const updatedFiles = new Map<string, string>(virtualFiles);
    const results: Array<PatchApplicationResult | SimplePatchResult> = [];

    for (const patch of patches) {
      const existing = updatedFiles.get(patch.filePath) ?? "";

      if (isSimplePatch(patch)) {
        const simple = this.applySimplePatch(existing, patch);
        results.push(simple);
        if (simple.success && typeof simple.updatedSource === "string") {
          updatedFiles.set(patch.filePath, simple.updatedSource);
        }
        continue;
      }

      const language = this.parser.languageForPath(patch.filePath);

      if (language === null) {
        const ok = patch.action === "CREATE_FILE";
        results.push({
          success: ok,
          filePath: patch.filePath,
          updatedSource: ok ? patch.content : existing,
          byteOffsetStart: 0,
          byteOffsetEnd: ok ? patch.content.length : 0,
          hasSyntaxErrorAfterPatch: false,
          errorDetails: ok ? undefined : `${patch.action} is not supported for non-code file ${patch.filePath}; use CREATE_FILE`
        });
        if (ok) updatedFiles.set(patch.filePath, patch.content);
        continue;
      }

      const res = this.applyStructuralPatch(existing, language, patch);
      results.push(res);
      if (res.success) {
        updatedFiles.set(patch.filePath, res.updatedSource);
      }
    }

    return { files: updatedFiles, updatedFiles, results };
  }
}
