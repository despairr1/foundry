import * as fs from "fs";
import * as path from "path";

/**
 * Skills: instruction files the models follow when they apply. Each is `<foundry>/skills/<name>/SKILL.md`, a short
 * YAML header (name, description) and a markdown body. Which ones are enabled is kept in `.foundry/skills.json`
 * (Settings › Skills writes it). The enabled skills are added to every model call in chats, the prompt studio and
 * Builds; self-improvement rounds keep their own rules and do not use them.
 */
export interface Skill {
  name: string;
  description: string;
  body: string;
  path: string;
}

export const DEFAULT_ENABLED = ["clean-code", "test-first", "error-handling"];
const MAX_SKILL_CHARS = 6000;
const MAX_TOTAL_CHARS = 24000;

export function skillsDir(foundryRoot: string): string {
  return process.env.FOUNDRY_SKILLS_DIR?.trim() || path.join(foundryRoot, "skills");
}

export function skillsStatePath(foundryRoot: string): string {
  return path.join(foundryRoot, ".foundry", "skills.json");
}

/** `---\nname: x\ndescription: y\n---\nbody` -> the parts; a file without a header takes the folder's name. */
export function parseSkillFile(text: string, fallbackName: string): Omit<Skill, "path"> {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/.exec(text.replace(/^﻿/, ""));
  const header: Record<string, string> = {};
  let body = text;
  if (match) {
    for (const line of match[1].split(/\r?\n/)) {
      const kv = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(line);
      if (kv) header[kv[1].toLowerCase()] = kv[2].trim().replace(/^["']|["']$/g, "");
    }
    body = match[2];
  }
  return {
    name: (header.name || fallbackName).trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-") || fallbackName,
    description: (header.description || body.trim().split(/\r?\n/)[0] || "").slice(0, 300),
    body: body.trim()
  };
}

export function loadSkills(foundryRoot: string): Skill[] {
  const dir = skillsDir(foundryRoot);
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return [];
  }
  const skills: Skill[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const file = path.join(dir, entry.name, "SKILL.md");
    let text: string;
    try {
      text = fs.readFileSync(file, "utf8");
    } catch {
      continue;
    }
    skills.push({ ...parseSkillFile(text, entry.name), path: file });
  }
  return skills.sort((a, b) => a.name.localeCompare(b.name));
}

export function enabledSkillNames(foundryRoot: string, skills: Skill[]): string[] {
  const names = new Set(skills.map((s) => s.name));
  try {
    const state = JSON.parse(fs.readFileSync(skillsStatePath(foundryRoot), "utf8")) as { enabled?: unknown };
    if (Array.isArray(state.enabled)) {
      return state.enabled.filter((n): n is string => typeof n === "string" && names.has(n));
    }
  } catch {
    // No state yet: the defaults.
  }
  return DEFAULT_ENABLED.filter((n) => names.has(n));
}

/** The enabled skills as a prompt section, or "" when none. Bounded per skill and overall. */
export function skillsPrompt(foundryRoot: string): string {
  const skills = loadSkills(foundryRoot);
  const enabled = new Set(enabledSkillNames(foundryRoot, skills));
  const chosen = skills.filter((s) => enabled.has(s.name));
  if (chosen.length === 0) return "";
  const parts: string[] = [];
  let total = 0;
  for (const skill of chosen) {
    const body = skill.body.length > MAX_SKILL_CHARS ? `${skill.body.slice(0, MAX_SKILL_CHARS)}\n[cut: the skill is longer]` : skill.body;
    const section = `## Skill: ${skill.name}\n${skill.description}\n\n${body}`;
    if (total + section.length > MAX_TOTAL_CHARS) {
      parts.push(`## Skill: ${skill.name}\n${skill.description}\n\n[left out: the enabled skills are too long together]`);
      continue;
    }
    parts.push(section);
    total += section.length;
  }
  return `Skills the user enabled (follow each one whenever it applies to the work; the task's own instructions win on a conflict):\n\n${parts.join("\n\n")}`;
}
