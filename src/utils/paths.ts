import * as path from "path";

/**
 * Workspace-relative paths are stored POSIX-style ("src/module.ts") so that
 * keys produced by the filesystem walker on Windows match paths emitted by
 * the models.
 */
export function toPosixRelative(rootDir: string, absPath: string): string {
  return path.relative(rootDir, absPath).split(path.sep).join("/");
}

/**
 * Validates and normalizes a model-supplied workspace path. Rejects absolute
 * paths, traversal outside the workspace, `.git` internals, and characters
 * that would need shell quoting (paths are interpolated into test commands).
 */
export function normalizeWorkspacePath(rawPath: string): string {
  const cleaned = rawPath.trim().replace(/\\/g, "/").replace(/^(\.\/)+/, "");
  if (!cleaned || cleaned.includes("\0")) {
    throw new Error(`Empty or invalid workspace path "${rawPath}"`);
  }
  if (path.posix.isAbsolute(cleaned) || /^[A-Za-z]:/.test(cleaned)) {
    throw new Error(`Absolute paths are not allowed: "${rawPath}"`);
  }
  const normalized = path.posix.normalize(cleaned);
  if (normalized === "." || normalized === ".." || normalized.startsWith("../")) {
    throw new Error(`Path escapes the workspace: "${rawPath}"`);
  }
  if (normalized.split("/").some((segment) => segment === ".git")) {
    throw new Error(`Writing into .git is not allowed: "${rawPath}"`);
  }
  if (!/^[A-Za-z0-9._\-/@+]+$/.test(normalized)) {
    throw new Error(`Path contains unsupported characters: "${rawPath}"`);
  }
  return normalized;
}

/** Resolves a workspace-relative path to an absolute path that is guaranteed to stay inside rootDir. */
export function resolveInsideRoot(rootDir: string, relPath: string): string {
  const root = path.resolve(rootDir);
  const abs = path.resolve(root, normalizeWorkspacePath(relPath));
  if (abs !== root && !abs.startsWith(root + path.sep)) {
    throw new Error(`Path "${relPath}" resolves outside ${root}`);
  }
  return abs;
}
