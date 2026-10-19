import Parser from "tree-sitter";
import TypeScriptLang from "tree-sitter-typescript";
import RustLang from "tree-sitter-rust";
import PythonLang from "tree-sitter-python";
import GoLang from "tree-sitter-go";
import CppLang from "tree-sitter-cpp";
import * as path from "path";
import { performance } from "perf_hooks";

export type SupportedLanguage = "typescript" | "rust" | "python" | "go" | "cpp";

export type DeclarationWrapperKind = "export" | "decorator" | "attribute";

export interface ASTNodeInfo {
  name: string;
  kind: string;
  /** Range including any wrapper (export statement, decorators, Rust attributes). */
  startIndex: number;
  endIndex: number;
  startPosition: { row: number; column: number };
  endPosition: { row: number; column: number };
  text: string;
  /** Range of the bare declaration, without the wrapper. Equal to startIndex/endIndex when unwrapped. */
  declStartIndex: number;
  declEndIndex: number;
  wrapperKind: DeclarationWrapperKind | null;
}

export interface ParsedDocument {
  language: SupportedLanguage;
  tree: Parser.Tree;
  source: string;
  hasSyntaxError: boolean;
  syntaxErrorNodes: ASTNodeInfo[];
  parseDurationMs: number;
}

const TARGETABLE_NODE_TYPES: Record<SupportedLanguage, Set<string>> = {
  typescript: new Set([
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
    "class_declaration",
    "abstract_class_declaration",
    "interface_declaration",
    "type_alias_declaration",
    "enum_declaration",
    "lexical_declaration",
    "variable_declaration"
  ]),
  rust: new Set(["function_item", "struct_item", "enum_item", "impl_item", "trait_item", "const_item", "static_item", "type_item", "mod_item"]),
  python: new Set(["function_definition", "class_definition"]),
  go: new Set(["function_declaration", "method_declaration", "type_declaration", "type_spec"]),
  cpp: new Set(["function_definition", "struct_specifier", "class_specifier", "declaration"])
};

// Variable-style declarations are only targetable outside function bodies, so a local
// `const result` never shadows a module-level declaration of the same name.
const LOCAL_SCOPED_TYPES = new Set(["lexical_declaration", "variable_declaration", "declaration", "const_item", "static_item"]);
const FUNCTION_BODY_TYPES = new Set(["statement_block", "compound_statement", "block"]);

const CODE_EXTENSIONS: Record<string, SupportedLanguage> = {
  ".ts": "typescript",
  ".tsx": "typescript",
  ".mts": "typescript",
  ".cts": "typescript",
  ".js": "typescript",
  ".jsx": "typescript",
  ".mjs": "typescript",
  ".cjs": "typescript",
  ".rs": "rust",
  ".py": "python",
  ".go": "go",
  ".c": "cpp",
  ".cc": "cpp",
  ".cpp": "cpp",
  ".cxx": "cpp",
  ".h": "cpp",
  ".hh": "cpp",
  ".hpp": "cpp"
};

export class ASTParser {
  private parsers: Map<SupportedLanguage, Parser> = new Map();
  private tsxParser: Parser;
  private lastParseDurationMs = 0;

  constructor() {
    const tsParser = new Parser();
    tsParser.setLanguage(TypeScriptLang.typescript);
    this.parsers.set("typescript", tsParser);

    this.tsxParser = new Parser();
    this.tsxParser.setLanguage(TypeScriptLang.tsx);

    const rustParser = new Parser();
    rustParser.setLanguage(RustLang);
    this.parsers.set("rust", rustParser);

    const pyParser = new Parser();
    pyParser.setLanguage(PythonLang);
    this.parsers.set("python", pyParser);

    const goParser = new Parser();
    goParser.setLanguage(GoLang);
    this.parsers.set("go", goParser);

    const cppParser = new Parser();
    cppParser.setLanguage(CppLang);
    this.parsers.set("cpp", cppParser);
  }

  /** Returns the language of a source file, or null for non-code files (manifests, docs, config). */
  public languageForPath(filePath: string): SupportedLanguage | null {
    return CODE_EXTENSIONS[path.extname(filePath).toLowerCase()] ?? null;
  }

  public detectLanguage(filePath: string): SupportedLanguage {
    return this.languageForPath(filePath) ?? "typescript";
  }

  public parse(source: string, language: SupportedLanguage, filePath?: string): ParsedDocument {
    const start = performance.now();
    const isJsx = filePath !== undefined && /\.(tsx|jsx)$/i.test(filePath);
    const parser = language === "typescript" && isJsx ? this.tsxParser : this.parsers.get(language);
    if (!parser) {
      throw new Error(`Unsupported language for Tree-sitter: ${language}`);
    }

    // node-tree-sitter's default 32 KiB input buffer rejects larger sources with "Invalid argument".
    const tree = parser.parse(source, undefined, { bufferSize: Math.max(32 * 1024, source.length * 2 + 1) });
    const syntaxErrorNodes: ASTNodeInfo[] = [];
    this.collectErrors(tree.rootNode, source, syntaxErrorNodes);

    const duration = performance.now() - start;
    this.lastParseDurationMs = duration;

    return {
      language,
      tree,
      source,
      hasSyntaxError: tree.rootNode.hasError,
      syntaxErrorNodes,
      parseDurationMs: duration
    };
  }

  public getLastParseDurationMs(): number {
    return this.lastParseDurationMs;
  }

  public listNamedDeclarations(
    source: string,
    language: SupportedLanguage,
    parsed?: ParsedDocument
  ): ASTNodeInfo[] {
    const doc = parsed ?? this.parse(source, language);
    const results: ASTNodeInfo[] = [];
    const allowedSet = TARGETABLE_NODE_TYPES[language];

    const visit = (node: Parser.SyntaxNode): void => {
      if (allowedSet.has(node.type) && !(LOCAL_SCOPED_TYPES.has(node.type) && this.isInsideBody(node))) {
        const extractedName = this.extractNodeName(node, source);
        if (extractedName) {
          const { outer, wrapperKind } = this.resolveWrapper(node, language);
          results.push({
            name: extractedName,
            kind: node.type,
            startIndex: outer.startIndex,
            endIndex: outer.endIndex,
            startPosition: outer.startPosition,
            endPosition: outer.endPosition,
            text: source.slice(outer.startIndex, outer.endIndex),
            declStartIndex: node.startIndex,
            declEndIndex: node.endIndex,
            wrapperKind
          });
        }
      }
      for (let i = 0; i < node.namedChildCount; i += 1) {
        const child = node.namedChild(i);
        if (child) visit(child);
      }
    };

    visit(doc.tree.rootNode);
    return results;
  }

  public findTargetNode(source: string, language: SupportedLanguage, targetName: string): ASTNodeInfo | null {
    const wanted = targetName.trim();
    const declarations = this.listNamedDeclarations(source, language);
    return (
      declarations.find((d) => d.name === wanted) ??
      // Accept qualified targets such as "Pool.initialize" or "Pool::initialize" for methods.
      declarations.find((d) => wanted.length > 0 && d.name === wanted.split(/::|\./).pop()) ??
      null
    );
  }

  private isInsideBody(node: Parser.SyntaxNode): boolean {
    for (let cur = node.parent; cur; cur = cur.parent) {
      if (FUNCTION_BODY_TYPES.has(cur.type)) return true;
    }
    return false;
  }

  private resolveWrapper(
    node: Parser.SyntaxNode,
    language: SupportedLanguage
  ): {
    outer: { startIndex: number; endIndex: number; startPosition: Parser.Point; endPosition: Parser.Point };
    wrapperKind: DeclarationWrapperKind | null;
  } {
    const parent = node.parent;
    if (language === "typescript" && parent && parent.type === "export_statement") {
      return { outer: parent, wrapperKind: "export" };
    }
    if (language === "python" && parent && parent.type === "decorated_definition") {
      return { outer: parent, wrapperKind: "decorator" };
    }
    if (language === "rust") {
      let first: Parser.SyntaxNode = node;
      for (let prev = node.previousNamedSibling; prev && prev.type === "attribute_item"; prev = prev.previousNamedSibling) {
        first = prev;
      }
      if (first !== node) {
        return {
          outer: {
            startIndex: first.startIndex,
            endIndex: node.endIndex,
            startPosition: first.startPosition,
            endPosition: node.endPosition
          },
          wrapperKind: "attribute"
        };
      }
    }
    return { outer: node, wrapperKind: null };
  }

  private extractNodeName(node: Parser.SyntaxNode, source: string): string | null {
    const nameField = node.childForFieldName("name");
    if (nameField) {
      return source.slice(nameField.startIndex, nameField.endIndex).trim();
    }

    const declarator = node.childForFieldName("declarator");
    if (declarator) {
      const innerName = declarator.childForFieldName("declarator") ?? declarator.childForFieldName("name");
      if (innerName) {
        return source.slice(innerName.startIndex, innerName.endIndex).trim();
      }
      for (let i = 0; i < declarator.namedChildCount; i += 1) {
        const child = declarator.namedChild(i);
        if (child && (child.type === "identifier" || child.type === "field_identifier")) {
          return source.slice(child.startIndex, child.endIndex).trim();
        }
      }
    }

    for (let i = 0; i < node.namedChildCount; i += 1) {
      const child = node.namedChild(i);
      if (!child) continue;
      if (
        child.type === "identifier" ||
        child.type === "type_identifier" ||
        child.type === "property_identifier" ||
        child.type === "field_identifier"
      ) {
        return source.slice(child.startIndex, child.endIndex).trim();
      }
      if (child.type === "variable_declarator" || child.type === "type_spec") {
        const subName = this.extractNodeName(child, source);
        if (subName) return subName;
      }
    }

    return null;
  }

  private collectErrors(node: Parser.SyntaxNode, source: string, acc: ASTNodeInfo[]): void {
    if (node.type === "ERROR" || node.isMissing) {
      acc.push({
        name: node.type,
        kind: "SYNTAX_ERROR",
        startIndex: node.startIndex,
        endIndex: node.endIndex,
        startPosition: node.startPosition,
        endPosition: node.endPosition,
        text: source.slice(node.startIndex, Math.min(source.length, node.endIndex + 40)),
        declStartIndex: node.startIndex,
        declEndIndex: node.endIndex,
        wrapperKind: null
      });
    }
    for (let i = 0; i < node.childCount; i += 1) {
      const child = node.child(i);
      if (child && (child.hasError || child.isMissing || child.type === "ERROR")) {
        this.collectErrors(child, source, acc);
      }
    }
  }
}
