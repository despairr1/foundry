import * as fs from "fs";
import * as path from "path";

/**
 * AI access rules: which folders Foundry's models may read, change or run things in.
 *
 * The GUI's Files pane writes them (right-click a folder: Allow / Block AI access) to
 * `<foundry>/.foundry/access.json` as `{"rules": [{"path": "C:\\...", "access": "block"}]}`.
 * The engine refuses to work on a blocked folder and leaves blocked subfolders of a project
 * unread. Everything is allowed unless a rule says otherwise; the most specific rule (the
 * longest matching path) wins, so a blocked folder can hold an allowed subfolder and vice versa.
 */
export type AccessKind = "allow" | "block";

export interface AccessRule {
  path: string;
  access: AccessKind;
}

export class AccessBlockedError extends Error {
  public readonly folder: string;
  public readonly rule: string;

  constructor(folder: string, rule: string) {
    super(
      `AI access to ${folder} is blocked (rule: ${rule}). Allow it in Foundry's Files pane (right-click the folder) ` +
        `or in Settings › AI access.`
    );
    this.name = "AccessBlockedError";
    this.folder = folder;
    this.rule = rule;
  }
}

export function accessRulesPath(foundryRoot: string): string {
  return process.env.FOUNDRY_ACCESS_RULES?.trim() || path.join(foundryRoot, ".foundry", "access.json");
}

export function loadAccessRules(file: string): AccessRule[] {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, "utf8")) as { rules?: unknown };
    if (!Array.isArray(parsed.rules)) return [];
    return parsed.rules
      .filter((r): r is Record<string, unknown> => typeof r === "object" && r !== null)
      .filter((r) => typeof r.path === "string" && r.path.trim() !== "" && (r.access === "allow" || r.access === "block"))
      .map((r) => ({ path: (r.path as string).trim(), access: r.access as AccessKind }));
  } catch {
    return [];
  }
}

function canonical(p: string): string {
  const resolved = path.resolve(p).replace(/[\\/]+$/, "");
  return process.platform === "win32" ? resolved.toLowerCase() : resolved;
}

function covers(rulePath: string, target: string): boolean {
  const rule = canonical(rulePath);
  const folder = canonical(target);
  return folder === rule || folder.startsWith(rule + path.sep);
}

/** The rule that decides a folder's access (the most specific one), or undefined when no rule covers it. */
export function rulingFor(target: string, rules: AccessRule[]): AccessRule | undefined {
  let best: AccessRule | undefined;
  for (const rule of rules) {
    if (covers(rule.path, target) && (!best || canonical(rule.path).length > canonical(best.path).length)) best = rule;
  }
  return best;
}

export function accessFor(target: string, rules: AccessRule[]): AccessKind {
  return rulingFor(target, rules)?.access ?? "allow";
}

/** Throws AccessBlockedError when Foundry's models may not work in `target`. */
export function assertAiAccess(target: string, foundryRoot: string, rules?: AccessRule[]): void {
  const active = rules ?? loadAccessRules(accessRulesPath(foundryRoot));
  const rule = rulingFor(target, active);
  if (rule?.access === "block") throw new AccessBlockedError(path.resolve(target), rule.path);
}
