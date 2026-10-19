import Parser from "tree-sitter";
import { ASTParser, SupportedLanguage } from "../ast/parser";
import { SandboxRunner } from "../sandbox/runner";
import { logger } from "../utils/logger";

export interface MutantDescriptor {
  id: string;
  kind: "OPERATOR_INVERSION" | "GUARD_DELETION" | "CONSTANT_PERTURBATION";
  originalSnippet: string;
  mutatedSnippet: string;
  mutatedSource: string;
  killed: boolean;
}

export interface MutationEvaluationReport {
  totalMutants: number;
  killedMutants: number;
  survivedMutants: number;
  killRatio: number;
  survivalRatio: number;
  passedLethalityThreshold: boolean;
  survivorDescriptions: string[];
}

const OPERATOR_INVERSIONS: Record<string, string> = {
  "===": "!==",
  "!==": "===",
  "==": "!=",
  "!=": "==",
  "<": ">=",
  "<=": ">",
  ">": "<=",
  ">=": "<",
  "+": "-",
  "-": "+",
  "*": "/",
  "/": "*",
  "&&": "||",
  "||": "&&",
  and: "or",
  or: "and"
};

const BINARY_NODE_TYPES = new Set(["binary_expression", "binary_operator", "comparison_operator", "boolean_operator"]);
const GUARD_NODE_TYPES = new Set(["if_statement", "if_expression"]);
const INTEGER_NODE_TYPES = new Set(["number", "integer", "integer_literal", "int_literal", "number_literal"]);

type Candidate = Omit<MutantDescriptor, "id" | "killed">;

export class MutationEngine {
  private parser: ASTParser;
  private sandbox: SandboxRunner;
  private minKillRatio: number;

  constructor(parser: ASTParser, sandbox: SandboxRunner, minKillRatio = 0.85) {
    this.parser = parser;
    this.sandbox = sandbox;
    this.minKillRatio = minKillRatio;
  }

  public generateMutants(source: string, language: SupportedLanguage, maxMutants = 5): MutantDescriptor[] {
    const parsed = this.parser.parse(source, language);
    const byKind = new Map<MutantDescriptor["kind"], Candidate[]>();
    const add = (candidate: Candidate): void => {
      const list = byKind.get(candidate.kind) ?? [];
      list.push(candidate);
      byKind.set(candidate.kind, list);
    };
    const splice = (start: number, end: number, replacement: string): string =>
      source.slice(0, start) + replacement + source.slice(end);

    const visit = (node: Parser.SyntaxNode): void => {
      if (BINARY_NODE_TYPES.has(node.type)) {
        for (let i = 0; i < node.childCount; i += 1) {
          const child = node.child(i);
          if (!child || child.isNamed) continue;
          const inverted = OPERATOR_INVERSIONS[source.slice(child.startIndex, child.endIndex)];
          if (inverted) {
            add({
              kind: "OPERATOR_INVERSION",
              originalSnippet: source.slice(node.startIndex, node.endIndex),
              mutatedSnippet:
                source.slice(node.startIndex, child.startIndex) + inverted + source.slice(child.endIndex, node.endIndex),
              mutatedSource: splice(child.startIndex, child.endIndex, inverted)
            });
            break;
          }
        }
      }

      if (GUARD_NODE_TYPES.has(node.type)) {
        const nodeText = source.slice(node.startIndex, node.endIndex);
        if (/\bthrow\b|Err\(|\braise\b|panic!|\breturn\b/.test(nodeText)) {
          // Python blocks cannot be empty, so a deleted guard becomes `pass`.
          const replacement = language === "python" ? "pass" : "";
          add({
            kind: "GUARD_DELETION",
            originalSnippet: nodeText,
            mutatedSnippet: replacement || "/* guard deleted */",
            mutatedSource: splice(node.startIndex, node.endIndex, replacement)
          });
        }
      }

      if (INTEGER_NODE_TYPES.has(node.type)) {
        const literal = source.slice(node.startIndex, node.endIndex);
        if (/^\d+$/.test(literal)) {
          const perturbed = String(BigInt(literal) + 1n);
          add({
            kind: "CONSTANT_PERTURBATION",
            originalSnippet: literal,
            mutatedSnippet: perturbed,
            mutatedSource: splice(node.startIndex, node.endIndex, perturbed)
          });
        }
      }

      for (let i = 0; i < node.namedChildCount; i += 1) {
        const child = node.namedChild(i);
        if (child) visit(child);
      }
    };

    visit(parsed.tree.rootNode);

    // Round-robin across mutant kinds so a file full of arithmetic does not crowd out guard deletions,
    // and skip mutants that no longer parse: a syntax error is "killed" by any test suite and proves nothing.
    const queues = Array.from(byKind.values()).map((list) => ({ list, next: 0 }));
    const mutants: MutantDescriptor[] = [];
    while (mutants.length < maxMutants && queues.some((q) => q.next < q.list.length)) {
      for (const queue of queues) {
        if (mutants.length >= maxMutants) break;
        while (queue.next < queue.list.length) {
          const candidate = queue.list[queue.next];
          queue.next += 1;
          if (this.parser.parse(candidate.mutatedSource, language).hasSyntaxError) continue;
          const prefix = candidate.kind === "OPERATOR_INVERSION" ? "OP" : candidate.kind === "GUARD_DELETION" ? "GUARD" : "CONST";
          mutants.push({ ...candidate, id: `MUT_${prefix}_${mutants.length + 1}`, killed: false });
          break;
        }
      }
    }
    return mutants;
  }

  public async evaluateTestLethality(params: {
    workspaceFiles: Map<string, string>;
    targetFilePath: string;
    testFilePath: string;
    language: SupportedLanguage;
  }): Promise<MutationEvaluationReport> {
    const originalSource = params.workspaceFiles.get(params.targetFilePath) ?? "";
    const mutants = this.generateMutants(originalSource, params.language, 5);

    if (mutants.length === 0) {
      logger.warn("Mutation", `No viable mutants in ${params.targetFilePath}; lethality cannot be measured.`);
      return {
        totalMutants: 0,
        killedMutants: 0,
        survivedMutants: 0,
        killRatio: 1.0,
        survivalRatio: 0.0,
        passedLethalityThreshold: true,
        survivorDescriptions: []
      };
    }

    let killedCount = 0;
    const survivorDescriptions: string[] = [];

    for (const mutant of mutants) {
      const mutatedWorkspace = new Map<string, string>(params.workspaceFiles);
      mutatedWorkspace.set(params.targetFilePath, mutant.mutatedSource);

      const execRes = await this.sandbox.executeWorkspaceTests(mutatedWorkspace, params.language, params.testFilePath);

      if (!execRes.passed) {
        mutant.killed = true;
        killedCount += 1;
      } else {
        survivorDescriptions.push(
          `Mutant ${mutant.id} (${mutant.kind}) survived: "${mutant.originalSnippet}" -> "${mutant.mutatedSnippet}"`
        );
      }
    }

    const totalMutants = mutants.length;
    const survivedMutants = totalMutants - killedCount;
    const killRatio = killedCount / totalMutants;
    const survivalRatio = survivedMutants / totalMutants;

    logger.updateState({ mutationSurvivalRatio: survivalRatio });

    return {
      totalMutants,
      killedMutants: killedCount,
      survivedMutants,
      killRatio,
      survivalRatio,
      passedLethalityThreshold: killRatio >= this.minKillRatio,
      survivorDescriptions
    };
  }
}
