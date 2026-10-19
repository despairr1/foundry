"""Unit tests for the plain-Python side of the right-pane tool pages: the engine-log reader (Search tree, Ouroboros),
the test-output reader (Tests), the symbol scan (Code map) and the memory reader (Memory). No Tk, no model calls."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import code_map  # noqa: E402
import engine_log  # noqa: E402
import memory_store  # noqa: E402
import test_report  # noqa: E402


SAMPLE_LOG = """[22:29:51.033] [INFO] [CLI] Improving the existing project in C:\\work\\demo (spending limit $20.00, 0 earlier run(s) remembered).
[22:30:10.000] [INFO] [Convergence] Module plan: 1. storage (src/storage.ts) | 2. parser (src/parser.ts, imports storage)
[22:30:11.000] [INFO] [Convergence] Module 1/2: storage -> src/storage.ts
[22:31:00.000] [INFO] [Economics] CLAUDE (claude-fable-5-1): +100in/200out ($0.1000) | Total: $0.1000/$20.00
[22:31:05.000] [WARN] [Backoff] AstraSynthesizer[Beta] failed (attempt 1/4): AstraSynthesizer[Beta] HTTP 429: no credits. Retrying in 450ms.
[22:31:06.000] [WARN] [AstraSynthesizer] Astra failed on Branch Beta (no credits); falling back to Claude.
[22:31:30.000] [INFO] [TreeSearch] Branch Alpha step 1: fitness 0.0, 1 LSP error, mutation kill not measured, tests failed
[22:32:00.000] [WARN] [TreeSearch] Pruned Branch Beta: synthesis failed: response truncated at max_tokens=16000
[22:33:00.000] [WARN] [Convergence] Branch Gamma tests failed mutation lethality (60%). Regenerating hardened test suite.
[22:33:30.000] [INFO] [TreeSearch] Branch Alpha step 2: fitness 75.5, 0 LSP errors, mutation kill 80%, tests passed
[22:33:40.000] [INFO] [TreeSearch] Branch Gamma step 2: fitness 11.1, 0 LSP errors, mutation kill 60%, tests passed
[22:34:00.000] [INFO] [TreeSearch] Pruned Branch Gamma (fitness 11.1 vs leader 75.5)
[22:35:00.000] [INFO] [Convergence] Module 1/2 storage converged on Branch Alpha after 2 iteration(s).
[22:35:01.000] [INFO] [Git] Committed abcdef12: feat(storage): converged
[22:35:02.000] [INFO] [Memory] Indexed winning semantic patch episode #7 for storage
[22:35:10.000] [INFO] [Convergence] Module 2/2: parser -> src/parser.ts
[22:36:00.000] [INFO] [Economics] CLAUDE (claude-fable-5-1): +100in/200out ($0.5000) | Total: $0.6000/$20.00
[22:37:00.000] [WARN] [Convergence] Module 2/2 parser did not converge: tests kept failing
[22:37:21.000] [ERROR] [CLI] Module 2/2 parser did not converge after 3 iteration(s). Cost: $0.5000
[22:37:21.739] [ERROR] [CLI] Not finished: 1/2 part(s) done. Total cost: $0.6000. Problem: parser
[23:00:00.000] [INFO] [CLI] Chat reply from claude (claude-fable-5-1, 120 chars, $0.0100).
[23:10:00.000] [ERROR] [Fatal][
  {
    "message": "ANTHROPIC_API_KEY is required",
    "path": ["anthropicApiKey"]
  }
]
[23:20:00.000] [INFO] [SelfImprove] Health: type-check passing, tests passing.
[23:20:01.000] [INFO] [CLI] Type-check and tests pass; 3 recent warning(s)/error(s) in the log.
[23:30:00.000] [INFO] [Ouroboros] Self-modification disabled (OUROBOROS_AUTO_HEAL=false); continuing.
"""


class EngineLogTest(unittest.TestCase):
    def test_parse_lines_handles_both_spacings_and_continuations(self):
        entries = engine_log.parse_lines("[01:02:03.004] [INFO] [Git]Committed 1234abcd:feat\n[01:02:04.000] [ERROR] [Fatal][\n  {\"a\": 1}\n]\n")
        self.assertEqual(entries[0], {"time": "01:02:03.004", "level": "INFO", "component": "Git", "message": "Committed 1234abcd:feat"})
        self.assertEqual(entries[1]["component"], "Fatal")
        self.assertIn('{"a": 1}', entries[1]["message"])

    def test_parse_runs_reads_modules_branches_and_outcome(self):
        runs = engine_log.parse_runs(engine_log.parse_lines(SAMPLE_LOG))
        self.assertEqual([r["kind"] for r in runs], ["improve", "fatal", "check"])
        run = runs[0]
        self.assertEqual(run["target"], "C:\\work\\demo")
        self.assertEqual(run["limit_usd"], 20.0)
        self.assertEqual(run["spent_usd"], 0.6)
        self.assertFalse(run["outcome_ok"])
        self.assertTrue(run["outcome"].startswith("Not finished: 1/2"))
        storage, parser = run["modules"]
        self.assertEqual((storage["index"], storage["status"], storage["winner"], storage["iterations"]), ("1/2", "converged", "Alpha", 2))
        self.assertEqual(parser["imports"], ["storage"])
        self.assertEqual((parser["status"], parser["iterations"], parser["cost_usd"]), ("failed", 3, 0.5))
        self.assertEqual(storage["branches"]["Alpha"]["status"], "converged")
        self.assertEqual(storage["branches"]["Beta"]["status"], "pruned")
        self.assertEqual(storage["branches"]["Beta"]["retries"], 1)
        self.assertIn("Pruned: synthesis failed: response truncated at max_tokens=16000", storage["branches"]["Beta"]["notes"])
        self.assertIn("Astra failed to write the code; Claude wrote it instead", storage["branches"]["Beta"]["notes"])
        self.assertEqual(storage["branches"]["Gamma"]["status"], "pruned")
        self.assertEqual(storage["branches"]["Gamma"]["notes"][0], "Tests killed only 60% of mutants; a hardened test suite was generated")
        self.assertEqual(run["commits"], [{"hash": "abcdef12", "message": "feat(storage): converged"}])
        self.assertEqual(run["episodes"], [{"id": 7, "module": "storage"}])
        self.assertEqual(runs[1]["fatal"], ["ANTHROPIC_API_KEY is required"])
        self.assertEqual(runs[2]["health"], "type-check passing, tests passing.")
        self.assertEqual(len(engine_log.search_runs(runs)), 1)
        self.assertEqual(engine_log.describe_run(run), "Improve a project · demo · started 22:29:51")

    def test_branch_step_lines_give_fitness_and_iterations(self):
        runs = engine_log.parse_runs(engine_log.parse_lines(SAMPLE_LOG))
        storage, parser = runs[0]["modules"]
        alpha, beta, gamma = (storage["branches"][b] for b in engine_log.BRANCHES)
        self.assertEqual((alpha["steps"], alpha["step"], alpha["fitness"], alpha["lsp_errors"], alpha["kill_ratio"], alpha["tests_passed"]),
                         (2, 2, 75.5, 0, 80, True))
        self.assertEqual(alpha["status"], "converged")
        self.assertEqual((gamma["steps"], gamma["step"], gamma["fitness"], gamma["kill_ratio"], gamma["status"]), (1, 2, 11.1, 60, "pruned"))
        self.assertEqual((beta["steps"], beta["step"], beta["fitness"], beta["kill_ratio"], beta["tests_passed"]), (0, None, None, None, None))
        self.assertEqual(alpha["notes"], [])  # step lines are numbers on the row, not notes
        for branch in parser["branches"].values():
            self.assertEqual(branch["steps"], 0)
        # A failed step alone: the kill ratio was not measured and the branch counts as working.
        failed = engine_log.parse_runs(engine_log.parse_lines(
            "[01:00:00.000] [INFO] [TreeSearch] Branch Beta step 1: fitness 12.5, 3 LSP errors, mutation kill not measured, tests failed\n"))
        branch = failed[0]["modules"][0]["branches"]["Beta"]
        self.assertEqual((branch["steps"], branch["fitness"], branch["lsp_errors"], branch["kill_ratio"], branch["tests_passed"], branch["status"]),
                         (1, 12.5, 3, None, False, "active"))

    def test_old_log_without_start_line_becomes_a_run(self):
        text = ("[23:29:25.113] [INFO] [Economics]CLAUDE (claude-3-opus-20240229): +25in/240out ($0.0184) | Total: $0.0184/$5.00\n"
                "[23:29:25.117] [INFO] [ASTPatcher]Target node not found; appending.\n"
                "[23:29:25.738] [INFO] [Git]Committed 99a1b187:feat(core_module): autonomous convergence via BranchAlpha\n"
                "[23:29:25.742] [INFO] [CLI]Build converged on Branch Alpha. Modified files: src/module.ts ert{} Total Cost: $0.0617\n")
        runs = engine_log.parse_runs(engine_log.parse_lines(text))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["kind"], "run")
        self.assertEqual(runs[0]["spent_usd"], 0.0617)
        self.assertEqual(runs[0]["modules"][0]["winner"], "Alpha")
        self.assertTrue(runs[0]["outcome_ok"])

    def test_ouroboros_entries_and_collapsed_notes(self):
        entries = engine_log.parse_lines(SAMPLE_LOG)
        ouroboros = engine_log.entries_for(entries, "Ouroboros")
        self.assertEqual(len(ouroboros), 1)
        self.assertIn("OUROBOROS_AUTO_HEAL=false", ouroboros[0]["message"])
        self.assertEqual(engine_log.collapse_notes(["a", "a", "b", "a"]), ["a (×2)", "b", "a"])

    def test_read_log_missing_file(self):
        self.assertEqual(engine_log.read_log(os.path.join(tempfile.gettempdir(), "no-such-foundry.log")), [])


class TestReportTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="foundry-tests-")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def test_detects_npm_cargo_go_and_python(self):
        self.assertIsNone(test_report.detect_test_command(self.folder))
        self._write("package.json", json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}))
        self.assertIsNone(test_report.detect_test_command(self.folder))
        self._write("package.json", json.dumps({"scripts": {"test": "vitest run"}}))
        self.assertEqual(test_report.detect_test_command(self.folder), {"command": "npm test", "source": "package.json", "framework": "vitest"})
        os.remove(os.path.join(self.folder, "package.json"))
        self._write("Cargo.toml", "[package]\nname = \"x\"\n")
        self.assertEqual(test_report.detect_test_command(self.folder)["command"], "cargo test")
        os.remove(os.path.join(self.folder, "Cargo.toml"))
        self._write("tests/test_a.py", "import unittest\n\nclass T(unittest.TestCase):\n    pass\n")
        self.assertEqual(test_report.detect_test_command(self.folder)["command"], "python -m unittest discover -s tests")
        self._write("tests/test_b.py", "import pytest\n")
        self.assertEqual(test_report.detect_test_command(self.folder)["framework"], "pytest")

    def test_a_file_named_test_something_is_not_a_test_without_a_framework_import(self):
        self._write("test_report_copy.py", "print('import pytest' in 'nothing')\n")
        self.assertIsNone(test_report.detect_test_command(self.folder))

    def test_parses_vitest(self):
        text = ("\x1b[32m ✓\x1b[0m tests/a.test.ts (5 tests) 12ms\n ❯ tests/x.test.ts (3 tests | 1 failed) 20ms\n   × suite > name 5ms\n"
                " Test Files  1 failed | 14 passed (15)\n      Tests  1 failed | 86 passed (87)\n   Duration  3.21s (transform 1.2s)\n")
        result = test_report.parse_test_output(text)
        self.assertEqual((result["framework"], result["passed"], result["failed"], result["total"]), ("vitest", 86, 1, 87))
        self.assertEqual((result["files_passed"], result["files_failed"], result["duration"]), (14, 1, "3.21s"))
        self.assertEqual(result["failures"], ["suite > name"])
        self.assertEqual(result["summary"], "86 passed, 1 failed")

    def test_parses_jest_pytest_unittest_cargo_go(self):
        jest = test_report.parse_test_output("  ● Suite › fails\nTest Suites: 1 passed, 1 total\nTests:       1 failed, 6 passed, 7 total\nTime:        1.428 s\n")
        self.assertEqual((jest["framework"], jest["passed"], jest["failed"], jest["failures"]), ("jest", 6, 1, ["Suite › fails"]))
        pytest_result = test_report.parse_test_output("FAILED tests/test_a.py::test_b - assert 1 == 2\n===== 1 failed, 5 passed, 2 skipped in 0.12s =====\n")
        self.assertEqual((pytest_result["framework"], pytest_result["passed"], pytest_result["failed"], pytest_result["skipped"]), ("pytest", 5, 1, 2))
        ok = test_report.parse_test_output("....\nRan 12 tests in 0.034s\n\nOK\n")
        self.assertEqual((ok["framework"], ok["passed"], ok["failed"]), ("unittest", 12, 0))
        failed = test_report.parse_test_output("FAIL: test_x (t.T.test_x)\nRan 12 tests in 0.034s\n\nFAILED (failures=1, errors=2, skipped=1)\n")
        self.assertEqual((failed["passed"], failed["failed"], failed["skipped"], failed["failures"]), (8, 3, 1, ["test_x (t.T.test_x)"]))
        cargo = test_report.parse_test_output("test foo ... FAILED\ntest result: FAILED. 5 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.30s\n")
        self.assertEqual((cargo["framework"], cargo["passed"], cargo["failed"], cargo["failures"]), ("cargo", 5, 1, ["foo"]))
        go = test_report.parse_test_output("--- FAIL: TestX (0.00s)\nFAIL\tpkg/a\t0.01s\nok  \tpkg/b\t0.02s\n")
        self.assertEqual((go["framework"], go["passed"], go["failed"], go["files_passed"], go["files_failed"]), ("go", None, 1, 1, 1))
        self.assertEqual(go["summary"], "1 failed, 1 package passed, 1 package failed")

    def test_unrecognized_output_has_no_counts(self):
        result = test_report.parse_test_output("Compiling...\nDone.\n")
        self.assertIsNone(result["framework"])
        self.assertIsNone(result["passed"])
        self.assertIsNone(result["summary"])

    def test_docker_state_without_docker(self):
        original = test_report.shutil.which
        test_report.shutil.which = lambda name: None
        try:
            state = test_report.docker_state("foundry-sandbox:latest")
        finally:
            test_report.shutil.which = original
        self.assertEqual((state["installed"], state["running"], state["image_present"]), (False, False, None))


class CodeMapTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="foundry-map-")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_symbols_per_language(self):
        ts = "import x from 'y';\nexport function run() {}\nexport default class App {}\nexport const VALUE = 1;\nexport { a as b, c };\nfunction hidden() {}\n"
        self.assertEqual([(s["kind"], s["name"], s["line"]) for s in code_map.symbols_in(ts, "typescript")],
                         [("function", "run", 2), ("class", "App", 3), ("const", "VALUE", 4), ("export", "b", 5), ("export", "c", 5)])
        js = "exports.helper = () => 1;\nmodule.exports.other = 2;\n"
        self.assertEqual([s["name"] for s in code_map.symbols_in(js, "javascript")], ["helper", "other"])
        py = "import os\n\ndef public():\n    pass\n\ndef _private():\n    pass\n\nclass Thing:\n    def method(self):\n        pass\n"
        self.assertEqual([(s["kind"], s["name"]) for s in code_map.symbols_in(py, "python")], [("def", "public"), ("class", "Thing")])
        go = "package x\n\nfunc Exported() {}\nfunc hidden() {}\ntype Shape struct{}\nfunc (s Shape) Area() int { return 0 }\n"
        self.assertEqual([s["name"] for s in code_map.symbols_in(go, "go")], ["Exported", "Shape", "Area"])
        rust = "pub fn run() {}\nfn hidden() {}\npub struct S;\npub(crate) enum E {}\n"
        self.assertEqual([s["name"] for s in code_map.symbols_in(rust, "rust")], ["run", "S", "E"])

    def test_scan_skips_dependency_folders_and_filters(self):
        os.makedirs(os.path.join(self.folder, "src"))
        os.makedirs(os.path.join(self.folder, "node_modules", "dep"))
        with open(os.path.join(self.folder, "src", "a.ts"), "w", encoding="utf-8") as f:
            f.write("export function alpha() {}\nexport class Beta {}\n")
        with open(os.path.join(self.folder, "src", "plain.ts"), "w", encoding="utf-8") as f:
            f.write("const local = 1;\n")
        with open(os.path.join(self.folder, "node_modules", "dep", "index.js"), "w", encoding="utf-8") as f:
            f.write("exports.ignored = 1;\n")
        scan = code_map.scan_folder(self.folder)
        self.assertEqual([f["path"] for f in scan["files"]], ["src/a.ts", "src/plain.ts"])
        self.assertEqual((scan["file_count"], scan["symbol_count"], scan["truncated"]), (2, 2, False))
        self.assertEqual([f["path"] for f in code_map.filter_files(scan["files"], "beta")], ["src/a.ts"])
        self.assertEqual([s["name"] for s in code_map.filter_files(scan["files"], "beta")[0]["symbols"]], ["Beta"])
        self.assertEqual(code_map.scan_folder(os.path.join(self.folder, "missing"))["files"], [])


class MemoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="foundry-memory-")
        self.path = os.path.join(self.folder, "memory.sqlite")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def _create(self, with_model_column=True):
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE episodic_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, language TEXT NOT NULL, module_name TEXT NOT NULL, "
                     "failure_pattern TEXT NOT NULL, stack_trace TEXT NOT NULL, winning_patches_json TEXT NOT NULL, embedding_blob BLOB NOT NULL, "
                     "created_at TEXT NOT NULL" + (", embedding_model TEXT NOT NULL DEFAULT 'legacy'" if with_model_column else "") + ")")
        patches = json.dumps([{"filePath": "src/a.ts", "action": "REPLACE_FUNCTION", "target": "run", "content": "export function run() { return 2; }"}])
        columns = "language, module_name, failure_pattern, stack_trace, winning_patches_json, embedding_blob, created_at" + \
            (", embedding_model" if with_model_column else "")
        values = ["typescript", "mod", "Tests failed: boundary", "at x (a.ts:1:1)", patches, bytes(16), "2026-10-17T03:51:10.061Z"]
        if with_model_column:
            values.append("gemini:gemini-embedding-2:4")
        conn.execute(f"INSERT INTO episodic_memory ({columns}) VALUES ({', '.join('?' * len(values))})", values)
        conn.commit()
        conn.close()

    def test_missing_file(self):
        result = memory_store.read_episodes(self.path)
        self.assertEqual((result["exists"], result["episodes"], result["error"]), (False, [], ""))
        self.assertFalse(os.path.exists(self.path))  # reading must not create it

    def test_reads_episodes_and_patches(self):
        self._create()
        result = memory_store.read_episodes(self.path)
        self.assertTrue(result["exists"])
        self.assertEqual(result["error"], "")
        episode = result["episodes"][0]
        self.assertEqual((episode["id"], episode["module"], episode["language"], episode["dims"], episode["embedding_model"]),
                         (1, "mod", "typescript", 4, "gemini:gemini-embedding-2:4"))
        self.assertEqual(episode["patches"][0]["action"], "REPLACE_FUNCTION")
        text = memory_store.describe_patch(episode["patches"][0])
        self.assertIn("REPLACE_FUNCTION · src/a.ts", text)
        self.assertIn("target: run", text)
        self.assertIn("return 2", text)
        self.assertEqual(memory_store.filter_episodes(result["episodes"], "boundary"), result["episodes"])
        self.assertEqual(memory_store.filter_episodes(result["episodes"], "nothing-like-this"), [])
        self.assertEqual(memory_store.delete_all(self.path), 1)
        self.assertEqual(memory_store.read_episodes(self.path)["episodes"], [])

    def test_legacy_schema_without_model_column(self):
        self._create(with_model_column=False)
        episode = memory_store.read_episodes(self.path)["episodes"][0]
        self.assertEqual(episode["embedding_model"], "legacy")

    def test_memory_path_defaults_and_relative_setting(self):
        class Config:
            def __init__(self, value):
                self.value = value

            def get(self, key, default=""):
                return self.value if key == "FOUNDRY_SQLITE_PATH" else default

        root = os.path.abspath(self.folder)
        self.assertEqual(memory_store.memory_path(root, Config("")), os.path.join(root, ".foundry-memory.sqlite"))
        self.assertEqual(memory_store.memory_path(root, Config("data/mem.sqlite")), os.path.join(root, "data", "mem.sqlite"))
        self.assertEqual(memory_store.memory_path(root, Config(self.path)), self.path)


if __name__ == "__main__":
    unittest.main()
