import { describe, it, expect, afterEach } from "vitest";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { DEFAULT_ENABLED, enabledSkillNames, loadSkills, parseSkillFile, skillsPrompt } from "../src/skills";

function makeRoot(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "foundry-skills-"));
  const write = (name: string, text: string) => {
    fs.mkdirSync(path.join(root, "skills", name), { recursive: true });
    fs.writeFileSync(path.join(root, "skills", name, "SKILL.md"), text);
  };
  write("clean-code", "---\nname: clean-code\ndescription: Readable code.\n---\n# Clean code\nName things well.\n");
  write("test-first", "---\nname: test-first\ndescription: Tests prove the contract.\n---\nList edge cases first.\n");
  write("no-header", "Just instructions, no header.\nSecond line.\n");
  fs.mkdirSync(path.join(root, "skills", "empty-folder"));
  return root;
}

describe("skills", () => {
  const previous = process.env.FOUNDRY_SKILLS_DIR;
  afterEach(() => {
    if (previous === undefined) delete process.env.FOUNDRY_SKILLS_DIR;
    else process.env.FOUNDRY_SKILLS_DIR = previous;
  });

  it("parses the header and falls back to the folder name and first line", () => {
    expect(parseSkillFile("---\nname: My Skill!\ndescription: \"Does things\"\n---\nBody here\n", "x")).toEqual({
      name: "my-skill-",
      description: "Does things",
      body: "Body here"
    });
    expect(parseSkillFile("Only a body\nmore", "folder-name")).toEqual({ name: "folder-name", description: "Only a body", body: "Only a body\nmore" });
  });

  it("loads every folder with a SKILL.md, sorted, and enables the defaults until the user chooses", () => {
    const root = makeRoot();
    try {
      delete process.env.FOUNDRY_SKILLS_DIR;
      const skills = loadSkills(root);
      expect(skills.map((s) => s.name)).toEqual(["clean-code", "no-header", "test-first"]);
      expect(enabledSkillNames(root, skills)).toEqual(DEFAULT_ENABLED.filter((n) => ["clean-code", "test-first"].includes(n)));
      fs.mkdirSync(path.join(root, ".foundry"), { recursive: true });
      fs.writeFileSync(path.join(root, ".foundry", "skills.json"), JSON.stringify({ enabled: ["no-header", "missing", 3] }));
      expect(enabledSkillNames(root, skills)).toEqual(["no-header"]);
      const prompt = skillsPrompt(root);
      expect(prompt).toContain("Skills the user enabled");
      expect(prompt).toContain("## Skill: no-header");
      expect(prompt).not.toContain("## Skill: clean-code");
      fs.writeFileSync(path.join(root, ".foundry", "skills.json"), JSON.stringify({ enabled: [] }));
      expect(skillsPrompt(root)).toBe("");
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  it("honours FOUNDRY_SKILLS_DIR and survives a missing folder", () => {
    const root = makeRoot();
    try {
      process.env.FOUNDRY_SKILLS_DIR = path.join(root, "skills");
      expect(loadSkills(path.join(root, "elsewhere")).length).toBe(3);
      delete process.env.FOUNDRY_SKILLS_DIR;
      expect(loadSkills(path.join(root, "elsewhere"))).toEqual([]);
      expect(skillsPrompt(path.join(root, "elsewhere"))).toBe("");
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });
});
