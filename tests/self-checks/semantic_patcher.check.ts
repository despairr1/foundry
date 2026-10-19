import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { SemanticPatcher, importInsertionOffset } from "../../src/ast/semantic_patcher";

async function main() {
  // (a) Read src/ast/semantic_patcher.ts via node:fs and assert the regex does not match
  const sourcePath = path.resolve(__dirname, "../../src/ast/semantic_patcher.ts");
  const sourceCode = fs.readFileSync(sourcePath, "utf-8");
  assert.equal(
    /\b(?:const|let|var)\s+tempNode\b/.test(sourceCode),
    false,
    "The unused tempNode declaration must be deleted"
  );

  // (b) Call new SemanticPatcher().applyPatch with a REPLACE-style action
  const patcher = new SemanticPatcher();
  const tsSnippet = `export function hello() {\n  return 1;\n}`;
  const replacePatch = {
    filePath: "test.ts",
    action: "REPLACE" as any,
    targetSymbol: "hello",
    newCode: "export function hello() {\n  return 2;\n}"
  };
  const res1 = patcher.applyPatch(tsSnippet, replacePatch, "typescript" as any);
  
  assert.equal(res1.success, true);
  assert.equal(typeof res1.updatedSource, "string");
  assert.ok(res1.updatedSource!.includes("return 2;"));
  
  // Guard against MUT_GUARD_1 (Wrapper prefix guard deletion)
  // If the guard is deleted, the patcher might incorrectly use startIndex instead of declStartIndex,
  // failing to replace the `export` wrapper and causing it to be duplicated in the output.
  assert.equal(
    res1.updatedSource!.includes("export export"),
    false,
    "MUT_GUARD_1: replacement should include the export wrapper properly, not duplicate it"
  );

  const tsSnippetDefault = `export default function main() { return 1; }`;
  const resDefault = patcher.applyPatch(tsSnippetDefault, {
    filePath: "test.ts",
    action: "REPLACE" as any,
    targetSymbol: "main",
    newCode: "export default function main() {\n  return 2;\n}"
  }, "typescript" as any);
  
  assert.equal(resDefault.success, true);
  assert.equal(
    resDefault.updatedSource!.includes("export default export default"),
    false,
    "MUT_GUARD_1: export default wrapper should not be duplicated"
  );

  // (c) Call applyPatch targeting a symbol absent from the snippet
  const resMissing = patcher.applyPatch(tsSnippet, {
    filePath: "test.ts",
    action: "REPLACE" as any,
    targetSymbol: "absentSymbol",
    newCode: "function absentSymbol() {}"
  }, "typescript" as any);
  
  assert.equal(resMissing.success, false);
  assert.equal(typeof resMissing.error, "string");
  assert.ok(resMissing.error!.length > 0);

  // (d) Assert typeof SemanticPatcher.prototype methods
  assert.equal(typeof SemanticPatcher.prototype.applyPatch, "function");
  assert.equal(typeof SemanticPatcher.prototype.applyBatch, "function");

  // (e) Run applyBatch over a one-file Map and assert results
  const filesMap = new Map([["test.ts", tsSnippet]]);
  const batchRes = patcher.applyBatch(filesMap, [replacePatch]);
  assert.equal(batchRes.results.length, 1);
  assert.equal(batchRes.results[0].success, true);
  assert.ok(batchRes.files.get("test.ts")!.includes("return 2;"));

  // Additional boundary checks to specifically target surviving mutants in importInsertionOffset
  if (typeof importInsertionOffset === "function") {
    const goCode = "package myawesomepackage\n\nimport \"fmt\"\n";
    // length of "package myawesomepackage\n" is 25
    const goOffset = importInsertionOffset(goCode, "go" as any);
    
    // Kills MUT_GUARD_4 (deleted go logic guard) and MUT_CONST_3 (pkg[0] -> pkg[1])
    assert.equal(
      goOffset,
      25,
      "MUT_GUARD_4 / MUT_CONST_3: Go package offset should be exactly 25"
    );

    const tsCodeWithFakePackage = "package myawesomepackage\n\nimport { x } from 'y';\n";
    const tsOffset = importInsertionOffset(tsCodeWithFakePackage, "typescript" as any);
    
    // Kills MUT_OP_5 (language === 'go' inverted to language !== 'go')
    assert.notEqual(
      tsOffset,
      25,
      "MUT_OP_5: TypeScript should not use Go package offset logic even if source matches"
    );
  }
}

main().catch(err => {
  console.error(err);
  process.exitCode = 1;
});