import Parser from "tree-sitter";
import { ASTParser, SupportedLanguage } from "./parser";

export interface CallGraphNode {
  symbol: string;
  filePath: string;
  language: SupportedLanguage;
  kind: string;
  callees: string[];
  callers: string[];
}

export class CallGraphBuilder {
  private parser: ASTParser;

  constructor(parser: ASTParser) {
    this.parser = parser;
  }

  public buildWorkspaceGraph(files: Map<string, string>): Map<string, CallGraphNode> {
    const graph = new Map<string, CallGraphNode>();

    for (const [filePath, source] of files.entries()) {
      const language = this.parser.languageForPath(filePath);
      if (language === null) continue;
      const parsed = this.parser.parse(source, language, filePath);
      const declarations = this.parser.listNamedDeclarations(source, language, parsed);

      for (const decl of declarations) {
        const callees = this.extractCalleesInRange(
          parsed.tree.rootNode,
          source,
          decl.startIndex,
          decl.endIndex
        );
        graph.set(decl.name, {
          symbol: decl.name,
          filePath,
          language,
          kind: decl.kind,
          callees,
          callers: []
        });
      }
    }

    for (const [callerName, node] of graph.entries()) {
      for (const calleeName of node.callees) {
        const target = graph.get(calleeName);
        if (target && !target.callers.includes(callerName)) {
          target.callers.push(callerName);
        }
      }
    }

    return graph;
  }

  public summarizeGraphForContext(files: Map<string, string>, maxEntries = 300): string {
    const graph = this.buildWorkspaceGraph(files);
    if (graph.size === 0) {
      return "Empty workspace call graph.";
    }
    const lines: string[] = [];
    for (const node of Array.from(graph.values()).slice(0, maxEntries)) {
      const calleesStr = node.callees.length > 0 ? `calls -> [${node.callees.join(", ")}]` : "leaf";
      const callersStr = node.callers.length > 0 ? `called by <- [${node.callers.join(", ")}]` : "root";
      lines.push(`${node.filePath}::${node.symbol} (${node.kind}) | ${calleesStr} | ${callersStr}`);
    }
    if (graph.size > maxEntries) {
      lines.push(`... ${graph.size - maxEntries} more symbols omitted`);
    }
    return lines.join("\n");
  }

  private extractCalleesInRange(
    root: Parser.SyntaxNode,
    source: string,
    startByte: number,
    endByte: number
  ): string[] {
    const callees = new Set<string>();

    const walk = (node: Parser.SyntaxNode): void => {
      if (node.endIndex < startByte || node.startIndex > endByte) {
        return;
      }

      if (
        node.type === "call_expression" ||
        node.type === "call" ||
        node.type === "invocation_expression"
      ) {
        const fnNode =
          node.childForFieldName("function") ??
          node.namedChild(0);
        if (fnNode) {
          const raw = source.slice(fnNode.startIndex, fnNode.endIndex).trim();
          const parts = raw.split(/[.:]/);
          const terminalName = parts[parts.length - 1];
          if (terminalName && /^[a-zA-Z_][a-zA-Z0-9_]*$/.test(terminalName)) {
            callees.add(terminalName);
          }
        }
      }

      for (let i = 0; i < node.namedChildCount; i += 1) {
        const child = node.namedChild(i);
        if (child) walk(child);
      }
    };

    walk(root);
    return Array.from(callees);
  }
}