import { SupportedLanguage } from "../ast/parser";

/**
 * Mechanisms Foundry adopted from three papers (Help › "Methods: REA, GEF and TCA"):
 *
 * - GEF, the Greenfield Engineering Framework: the auditor writes a hidden acceptance suite the code writer never
 *   sees, and only the shrunken counterexample of a failure goes back to it (`holdoutTestPath`,
 *   `distillCounterexample`).
 * - REA, Reverse Engineering Agents: for an existing codebase, evidence is gathered before anything is planned, and
 *   the current behavior is the specification unless the task says otherwise (`describeEvidence`).
 * - TCA, the Token and Context Arbiter: logs are folded before they go to a model, large sources become signature
 *   stubs (lazy handles), stable prompt prefixes are cached, and an attempt that keeps producing the same failure is
 *   stopped (`failureSignature`, `patchSignature`, `ThrashDetector`, `signatureStub`).
 *
 * Everything here is deterministic and makes no model call.
 */

// eslint-disable-next-line no-control-regex
const ANSI = /\x1b\[[0-9;]*[A-Za-z]/g;

const NOISE_LINE =
  /node_modules[\\/]|node:internal|^\s+at (?:Module|Object|Function|process|Generator|async Promise)\.|^\s*\^+\s*$|^={5,}|^-{5,}\s*$|^\s*\d+ passing|^npm (?:ERR|WARN)|^\s*$/;

const SIGNAL_LINE =
  /assert|expected|received|actual|error|fail|panick|exception|traceback|mismatch|\bE\s{2,}|^\s*[+-] |^\s*at .*\.(?:ts|js|py|rs|go|cpp)/i;

function fnv1a(text: string): string {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

/**
 * Folds a test or compiler log into the counterexample a model needs: assertion and error lines, the frames that
 * point into the workspace, no repeats and no noise. Falls back to the tail of the log when nothing stands out.
 */
export function distillCounterexample(text: string, maxChars = 3500): string {
  const lines = text.replace(ANSI, "").split(/\r?\n/).map((line) => line.replace(/\s+$/, ""));
  const seen = new Map<string, number>();
  const kept: string[] = [];
  for (const line of lines) {
    if (NOISE_LINE.test(line)) continue;
    const key = line.trim();
    if (!key) continue;
    const count = (seen.get(key) ?? 0) + 1;
    seen.set(key, count);
    if (count > 1) continue;
    kept.push(line.length > 400 ? `${line.slice(0, 400)} …` : line);
  }
  const signal = kept.filter((line) => SIGNAL_LINE.test(line));
  let chosen = signal.length >= 2 ? signal : kept.slice(-20);
  const repeated = Array.from(seen.entries()).filter(([, n]) => n > 2).length;
  if (repeated > 0) chosen = [...chosen, `(${repeated} line${repeated === 1 ? "" : "s"} repeated and folded)`];
  const joined = chosen.join("\n");
  if (joined.length <= maxChars) return joined;
  const head = Math.floor(maxChars * 0.7);
  return `${joined.slice(0, head)}\n… [folded] …\n${joined.slice(-(maxChars - head - 16))}`;
}

/** A short, stable fingerprint of a failure: the same failure with other line numbers, paths or timings matches. */
export function failureSignature(text: string): string {
  const normalized = text
    .replace(ANSI, "")
    .toLowerCase()
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && SIGNAL_LINE.test(line) && !NOISE_LINE.test(line))
    .slice(0, 12)
    .join("\n")
    .replace(/[a-z]:\\[^\s:]+|\/[^\s:]+\/[^\s:]+/g, "<path>")
    .replace(/foundry-[a-z]+-[a-z0-9]+/g, "<tmp>")
    .replace(/0x[0-9a-f]+/g, "<hex>")
    .replace(/\d+(?:\.\d+)?/g, "<n>")
    .replace(/\s+/g, " ");
  return fnv1a(normalized || text.trim().slice(0, 200).toLowerCase());
}

/** The fingerprint of a set of patches; an identical set resent would give an identical result. */
export function patchSignature(patches: Array<{ filePath: string; action: string; target: string; content: string }>): string {
  const normalized = patches
    .map((p) => `${p.filePath}|${p.action}|${p.target.trim()}|${p.content.replace(/\s+/g, " ").trim()}`)
    .sort()
    .join("\n");
  return fnv1a(normalized);
}

export interface ThrashVerdict {
  /** How many times in a row this result (failure or patch) has now come back. */
  repeats: number;
  /** One short of the limit: the writer is told to change its approach. */
  escalate: boolean;
  /** At the limit: the attempt is abandoned. */
  stalled: boolean;
  reason?: string;
}

/**
 * Watches each branch for the TCA thrash pattern: the same failure signature, or the same patch, coming back again
 * and again. `limit` is how many identical results in a row end the attempt (0 turns the detector off); one short of
 * it the writer gets a warning instead.
 */
export class ThrashDetector {
  private readonly limit: number;
  private history = new Map<string, { failures: string[]; patches: string[] }>();

  constructor(limit = 3) {
    this.limit = Math.max(0, Math.floor(limit));
  }

  public reset(): void {
    this.history.clear();
  }

  public record(branchId: string, failureSig: string, patchSig: string): ThrashVerdict {
    const entry = this.history.get(branchId) ?? { failures: [], patches: [] };
    entry.failures.push(failureSig);
    entry.patches.push(patchSig);
    this.history.set(branchId, entry);
    if (this.limit === 0) return { repeats: 1, escalate: false, stalled: false };

    const trailing = (items: string[]): number => {
      const last = items[items.length - 1];
      let n = 0;
      for (let i = items.length - 1; i >= 0 && items[i] === last; i -= 1) n += 1;
      return n;
    };
    const sameFailure = trailing(entry.failures);
    const samePatch = trailing(entry.patches);
    const repeats = Math.max(sameFailure, samePatch);
    const reason =
      samePatch >= sameFailure
        ? `the same patch was submitted ${samePatch} times in a row`
        : `the same failure came back ${sameFailure} times in a row`;
    return {
      repeats,
      escalate: repeats >= 2 && repeats === this.limit - 1,
      stalled: repeats >= this.limit,
      reason: repeats >= 2 ? reason : undefined
    };
  }
}

const TS_EXPORT = /^export\s+(?:default\s+)?(?:abstract\s+)?(?:async\s+)?(?:function\*?|class|interface|type|const|let|var|enum|namespace)\s+[A-Za-z_$][\w$]*/;
const TS_MEMBER = /^ {2}(?:(?:public|protected|static|async|readonly|override|abstract|get|set)\s+)*[A-Za-z_$][\w$]*\s*(?:\(|[:=?])/;

/**
 * The public shape of a source file instead of its text: the declaration lines of exported symbols (and the members
 * of exported classes). A dependency too large to quote in full is handed to the models this way.
 */
export function signatureStub(source: string, language: SupportedLanguage): string {
  const lines = source.split(/\r?\n/);
  const out: string[] = [];
  // The declaration up to its body: everything before the first "{" (an export list keeps its braces), minus a
  // trailing "=" or "=>" that introduced the body.
  const header = (line: string): string => {
    let text = line;
    if (!/^\s*export\s*\{/.test(line)) {
      const brace = line.indexOf("{");
      if (brace >= 0) text = line.slice(0, brace);
    }
    text = text.replace(/\s*(?:=>|=)?\s*$/, "");
    return text.length > 220 ? `${text.slice(0, 220)} …` : text;
  };

  if (language === "typescript") {
    let inExportedClass = false;
    for (const line of lines) {
      if (TS_EXPORT.test(line) || /^export\s*\{/.test(line)) {
        out.push(header(line));
        inExportedClass = /^export\s+(?:default\s+)?(?:abstract\s+)?class\b/.test(line);
        continue;
      }
      if (/^\S/.test(line) && line.trim()) inExportedClass = inExportedClass && !/^}/.test(line);
      if (inExportedClass && TS_MEMBER.test(line) && !/^\s*(?:private)\b/.test(line)) out.push(header(line));
    }
  } else if (language === "python") {
    for (const line of lines) {
      if (/^(?:async\s+def|def|class)\s+[A-Za-z_]/.test(line) && !/^def\s+_/.test(line)) out.push(header(line.replace(/:\s*$/, "")));
      else if (/^ {4}(?:async\s+def|def)\s+[A-Za-z_]/.test(line) && !/^ {4}def\s+_(?!_init__)/.test(line)) out.push(header(line.replace(/:\s*$/, "")));
      else if (/^[A-Z][A-Z0-9_]*\s*(?::[^=]+)?=/.test(line)) out.push(header(line));
    }
  } else if (language === "rust") {
    for (const line of lines) {
      if (/^\s*pub(?:\([^)]*\))?\s+(?:async\s+)?(?:unsafe\s+)?(?:fn|struct|enum|trait|type|const|static|mod|use)\b/.test(line) || /^impl\b/.test(line)) {
        out.push(header(line));
      }
    }
  } else if (language === "go") {
    for (const line of lines) {
      if (/^func\s+(?:\([^)]*\)\s*)?[A-Z]/.test(line) || /^type\s+[A-Z]/.test(line) || /^(?:var|const)\s+[A-Z]/.test(line)) out.push(header(line));
    }
  } else {
    for (const line of lines) {
      if (/^(?:class|struct|namespace|template|enum)\b/.test(line) || /^[A-Za-z_][\w:<>,\s*&]*\s+[A-Za-z_]\w*\s*\([^;{]*\)\s*(?:const)?\s*[{;]?\s*$/.test(line)) {
        out.push(header(line));
      }
    }
  }
  const body = out.length > 0 ? out.join("\n") : "(no exported declarations found)";
  return `// signatures only (${source.length.toLocaleString()} chars of source not shown)\n${body}`;
}

const TEST_PATH = /(?:^|\/)(?:tests?|__tests__|spec)\/|\.(?:test|spec)\.[a-z]+$|(?:^|\/)test_[^/]+\.py$|_test\.(?:go|py|rs)$/i;
const MAX_EVIDENCE_CHARS = 7000;

function manifestSummary(name: string, text: string): string | null {
  if (name === "package.json") {
    try {
      const pkg = JSON.parse(text) as { name?: string; scripts?: Record<string, string>; dependencies?: Record<string, string>; devDependencies?: Record<string, string> };
      const parts = [
        pkg.name ? `name ${pkg.name}` : "",
        pkg.scripts ? `scripts: ${Object.keys(pkg.scripts).join(", ")}` : "",
        pkg.dependencies ? `dependencies: ${Object.keys(pkg.dependencies).join(", ")}` : "",
        pkg.devDependencies ? `devDependencies: ${Object.keys(pkg.devDependencies).join(", ")}` : ""
      ].filter(Boolean);
      return parts.join("; ");
    } catch {
      return "(unparseable)";
    }
  }
  const firstLines = text.split(/\r?\n/).filter((l) => l.trim() && !l.trim().startsWith("#")).slice(0, 12);
  return firstLines.join(" | ").slice(0, 400);
}

function languageForEvidence(filePath: string): SupportedLanguage | null {
  if (/\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/i.test(filePath)) return "typescript";
  if (/\.py$/i.test(filePath)) return "python";
  if (/\.rs$/i.test(filePath)) return "rust";
  if (/\.go$/i.test(filePath)) return "go";
  if (/\.(c|cc|cpp|cxx|h|hh|hpp)$/i.test(filePath)) return "cpp";
  return null;
}

/**
 * REA's recon step for an existing codebase: what the manifests declare, which tests already exist, and what each
 * source file exports, so the architect plans against evidence instead of guessing. Empty for an empty workspace.
 */
export function describeEvidence(files: Map<string, string>): string {
  const manifests: string[] = [];
  const tests: string[] = [];
  const sources: Array<{ path: string; lines: number; stub: string }> = [];
  for (const [filePath, text] of files) {
    const base = filePath.split("/").pop() ?? filePath;
    const language = languageForEvidence(filePath);
    if (!language) {
      const summary = manifestSummary(base, text);
      if (summary) manifests.push(`- ${filePath}: ${summary}`);
      continue;
    }
    const lines = text.split(/\r?\n/).length;
    if (TEST_PATH.test(filePath)) {
      tests.push(`- ${filePath} (${lines} lines)`);
      continue;
    }
    const stub = signatureStub(text, language).split("\n").slice(1).join("\n");
    sources.push({ path: filePath, lines, stub });
  }
  if (manifests.length === 0 && tests.length === 0 && sources.length === 0) return "";

  sources.sort((a, b) => b.lines - a.lines);
  const sections: string[] = [];
  if (manifests.length > 0) sections.push(`Manifests:\n${manifests.slice(0, 8).join("\n")}`);
  sections.push(
    tests.length > 0
      ? `Existing tests (${tests.length} file${tests.length === 1 ? "" : "s"}; they must keep passing):\n${tests.slice(0, 30).join("\n")}${tests.length > 30 ? `\n- … ${tests.length - 30} more` : ""}`
      : "Existing tests: none found."
  );
  const sourceLines: string[] = [];
  let used = sections.join("\n\n").length;
  for (const source of sources) {
    const symbols = source.stub
      .split("\n")
      .filter((l) => !l.startsWith("("))
      .map((l) => l.trim())
      .slice(0, 12);
    const entry = `- ${source.path} (${source.lines} lines)${symbols.length > 0 ? `:\n    ${symbols.join("\n    ")}` : ""}`;
    if (used + entry.length > MAX_EVIDENCE_CHARS) {
      sourceLines.push(`- … ${sources.length - sourceLines.length} more source files`);
      break;
    }
    used += entry.length + 1;
    sourceLines.push(entry);
  }
  sections.push(`Source files and what they declare (largest first):\n${sourceLines.join("\n")}`);
  return sections.join("\n\n");
}

/** The guidance that goes with the evidence (REA): the code is the specification until the task says otherwise. */
export const EVIDENCE_GUIDANCE = [
  `This is an existing codebase. Treat the evidence above as ground truth and the task as a hypothesis about what should change.`,
  `Unless the task says otherwise, current behavior is the specification: keep every existing public symbol, signature and test passing, prefer the smallest change that completes the task, and do not rewrite files the task does not need.`,
  `Where the task leaves something open, choose the reading that preserves existing behavior, and state that assumption in the module's boundaryInvariants.`
].join("\n");

/**
 * Where a module's hidden acceptance suite lives: next to the public test file, named so the harness picks it up.
 * Returns null when the harness cannot run a second file for this module (tests inside the implementation file, or
 * C++ whose test defines main()).
 */
export function holdoutTestPath(testFilePath: string, language: SupportedLanguage, targetFilePath = ""): string | null {
  if (!testFilePath || testFilePath === targetFilePath) return null;
  switch (language) {
    case "typescript": {
      const m = /^(.*?)(\.(?:test|spec))?(\.[cm]?[jt]sx?)$/.exec(testFilePath);
      if (!m) return null;
      return `${m[1]}.holdout${m[2] ?? ""}${m[3]}`;
    }
    case "python": {
      const parts = testFilePath.split("/");
      const base = parts.pop() ?? "";
      let name: string;
      if (/^test_.*\.py$/.test(base)) name = base.replace(/\.py$/, "_holdout.py");
      else if (/_test\.py$/.test(base)) name = base.replace(/_test\.py$/, "_holdout_test.py");
      else name = base.replace(/\.py$/, "_holdout.py");
      return [...parts, name].join("/");
    }
    case "go":
      return /_test\.go$/.test(testFilePath) ? testFilePath.replace(/_test\.go$/, "_holdout_test.go") : null;
    case "rust":
      return /^tests\/[^/]+\.rs$/.test(testFilePath) ? testFilePath.replace(/\.rs$/, "_holdout.rs") : null;
    default:
      return null;
  }
}

/** Older chat messages, folded to one line each so the model keeps the thread without the full text (TCA compaction). */
export function foldMessages(lines: string[], maxEntries = 24, maxCharsEach = 140): string {
  const entries = lines.slice(-maxEntries).map((line) => {
    const flat = line.replace(/\s+/g, " ").trim();
    return `- ${flat.length > maxCharsEach ? `${flat.slice(0, maxCharsEach)}…` : flat}`;
  });
  const skipped = lines.length - entries.length;
  return `${skipped > 0 ? `- (${skipped} earlier messages not shown)\n` : ""}${entries.join("\n")}`;
}
