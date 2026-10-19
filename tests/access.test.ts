import { describe, it, expect, afterEach } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { accessFor, accessRulesPath, assertAiAccess, loadAccessRules, rulingFor } from "../src/utils/access";

const home = path.resolve(os.tmpdir(), "foundry-access-home");
const rules = [
  { path: path.join(home, "private"), access: "block" as const },
  { path: path.join(home, "private", "shared"), access: "allow" as const },
  { path: path.join(home, "projects"), access: "allow" as const }
];

describe("AI access rules", () => {
  afterEach(() => {
    delete process.env.FOUNDRY_ACCESS_RULES;
  });

  it("allows everything unless a rule says otherwise, and lets the most specific rule win", () => {
    expect(accessFor(path.join(home, "anything"), rules)).toBe("allow");
    expect(accessFor(path.join(home, "private"), rules)).toBe("block");
    expect(accessFor(path.join(home, "private", "notes", "2026"), rules)).toBe("block");
    expect(accessFor(path.join(home, "private", "shared", "app"), rules)).toBe("allow");
    expect(accessFor(path.join(home, "privateer"), rules)).toBe("allow"); // a prefix of the name is not the folder
    expect(rulingFor(path.join(home, "private", "x"), rules)?.path).toBe(path.join(home, "private"));
  });

  it("refuses a blocked folder with a message that says how to allow it", () => {
    expect(() => assertAiAccess(path.join(home, "private", "diary"), home, rules)).toThrow(/AI access to .* is blocked .*Files pane/);
    expect(() => assertAiAccess(path.join(home, "projects", "app"), home, rules)).not.toThrow();
  });

  it("reads the rules file the GUI writes and ignores malformed entries", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-access-"));
    try {
      const file = path.join(dir, "access.json");
      fs.writeFileSync(file, JSON.stringify({ rules: [{ path: "C:\\\\x", access: "block" }, { path: "", access: "block" }, { path: "D:\\\\y", access: "maybe" }, 7] }));
      expect(loadAccessRules(file)).toEqual([{ path: "C:\\\\x", access: "block" }]);
      expect(loadAccessRules(path.join(dir, "missing.json"))).toEqual([]);
      process.env.FOUNDRY_ACCESS_RULES = file;
      expect(accessRulesPath(dir)).toBe(file);
      delete process.env.FOUNDRY_ACCESS_RULES;
      expect(accessRulesPath(dir)).toBe(path.join(dir, ".foundry", "access.json"));
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});
