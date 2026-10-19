"""Unit tests for the GUI's plain-Python helpers: access rules, launcher detection, the prompt store and providers."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import access_rules  # noqa: E402
import launcher  # noqa: E402
import prompt_store  # noqa: E402
import providers  # noqa: E402
import skills  # noqa: E402


class AccessRulesTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="foundry-rules-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.home, "private", "shared"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_most_specific_rule_wins_and_default_is_allow(self):
        access_rules.set_rule(self.root, os.path.join(self.home, "private"), "block")
        access_rules.set_rule(self.root, os.path.join(self.home, "private", "shared"), "allow")
        rules = access_rules.load_rules(self.root)
        self.assertEqual(access_rules.access_for(os.path.join(self.home, "anything"), rules), "allow")
        self.assertEqual(access_rules.access_for(os.path.join(self.home, "private", "diary"), rules), "block")
        self.assertEqual(access_rules.access_for(os.path.join(self.home, "private", "shared", "x"), rules), "allow")
        self.assertEqual(access_rules.access_for(os.path.join(self.home, "privateer"), rules), "allow")
        self.assertTrue(access_rules.is_blocked(self.root, os.path.join(self.home, "private")))
        with open(access_rules.rules_path(self.root), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["rules"]), 2)

    def test_clearing_a_rule(self):
        access_rules.set_rule(self.root, self.home, "block")
        access_rules.set_rule(self.root, self.home, None)
        self.assertEqual(access_rules.load_rules(self.root), [])


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="foundry-launch-")

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_nothing_to_launch(self):
        self.assertIsNone(launcher.find_launcher(self.folder))
        self.assertIn("launcher.json", launcher.launcher_request(self.folder))

    def test_conventions(self):
        open(os.path.join(self.folder, "main.py"), "w").close()
        self.assertEqual(launcher.find_launcher(self.folder)["command"], "python main.py")
        with open(os.path.join(self.folder, "package.json"), "w", encoding="utf-8") as f:
            json.dump({"scripts": {"start": "node index.js"}}, f)
        self.assertEqual(launcher.find_launcher(self.folder)["command"], "npm start")
        script = "launch.bat" if sys.platform == "win32" else "launch.sh"
        open(os.path.join(self.folder, script), "w").close()
        self.assertEqual(launcher.find_launcher(self.folder)["source"], "script")

    def test_launcher_json_wins(self):
        os.makedirs(os.path.join(self.folder, ".foundry"))
        with open(os.path.join(self.folder, ".foundry", "launcher.json"), "w", encoding="utf-8") as f:
            json.dump({"command": "python app.py", "cwd": ".", "icon": "assets/icon.ico", "name": "Weather"}, f)
        found = launcher.find_launcher(self.folder)
        self.assertEqual((found["command"], found["source"], found["name"]), ("python app.py", "launcher.json", "Weather"))
        self.assertIn("launcher.json", launcher.describe_launcher(found))


class PromptStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="foundry-prompts-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_titles(self):
        self.assertEqual(prompt_store.title_for("write me a prompt for summarizing meeting notes"), "Summarizing meeting notes")
        self.assertEqual(prompt_store.title_for("A system prompt for a code reviewer"), "Code reviewer")
        self.assertEqual(prompt_store.title_for("A prompt for Claude that summarizes meeting notes"), "Claude summarizes meeting notes")
        self.assertEqual(prompt_store.title_for("I need a prompt to rename photos by date"), "Rename photos by date")
        self.assertEqual(prompt_store.title_for(""), "New prompt")

    def test_job_plan_parsing(self):
        raw = json.dumps({"job": "Weather app", "project": "existing", "language": "Python 3", "notes": "run in order",
                          "steps": [{"title": "Core", "thinking": "Extra high", "limit": "$3", "build": "Core logic" + chr(10) + "parse and compute"},
                                    {"title": "", "build": "Command line" + chr(10) + "cli.py", "thinking": "silly", "limit": -1, "optional": True},
                                    {"title": "No build text"}]})
        plan = prompt_store.parse_job_plan(raw)
        self.assertEqual((plan["job"], plan["project"], plan["language"]), ("Weather app", "existing", "Python 3"))
        self.assertEqual([s["title"] for s in plan["steps"]], ["Core", "Command line"])
        self.assertEqual((plan["steps"][0]["thinking"], plan["steps"][0]["limit"]), ("xhigh", 3.0))
        self.assertEqual((plan["steps"][1]["thinking"], plan["steps"][1]["limit"], plan["steps"][1]["optional"]), ("", 0.0, True))
        self.assertIsNone(prompt_store.parse_job_plan("not json"))
        self.assertIsNone(prompt_store.parse_job_plan('{"steps": []}'))

    def test_list_and_rename(self):
        folder = prompt_store.new_prompt_folder(self.root)
        self.assertEqual(prompt_store.list_prompts(self.root), [])
        prompt_store.touch(folder, title="First")
        folder2 = prompt_store.new_prompt_folder(self.root)
        prompt_store.touch(folder2, title="Second")
        titles = [p["title"] for p in prompt_store.list_prompts(self.root)]
        self.assertEqual(sorted(titles), ["First", "Second"])
        prompt_store.set_title(folder, "Renamed")
        self.assertIn("Renamed", [p["title"] for p in prompt_store.list_prompts(self.root)])
        prompt_store.delete_prompt(folder)
        self.assertEqual([p["title"] for p in prompt_store.list_prompts(self.root)], ["Second"])


class ProvidersTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="foundry-providers-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_add_and_endpoint(self):
        p = providers.add_provider(self.root, "DeepSeek", "https://api.deepseek.com/v1/", "deepseek-chat", preset="deepseek", price_in=0.27)
        self.assertEqual(p["keyVar"], "PROVIDER_DEEPSEEK_API_KEY")
        self.assertEqual(providers.chat_endpoint(p["baseUrl"]), "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(providers.chat_endpoint("http://127.0.0.1:11434"), "http://127.0.0.1:11434/v1/chat/completions")
        second = providers.add_provider(self.root, "DeepSeek", "https://x", "m")
        self.assertEqual(second["id"], "deepseek-2")
        providers.remove_provider(self.root, second["id"])
        self.assertEqual([x["id"] for x in providers.load_providers(self.root)], ["deepseek"])


class SkillsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="foundry-skills-")
        os.environ.pop("FOUNDRY_SKILLS_DIR", None)
        for name, text in (("clean-code", "---\nname: clean-code\ndescription: Readable code.\n---\nName things well.\n"),
                           ("plain", "Just rules.\nMore.\n")):
            os.makedirs(os.path.join(self.root, "skills", name))
            with open(os.path.join(self.root, "skills", name, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(text)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_load_enable_and_size(self):
        found = skills.load_skills(self.root)
        self.assertEqual([s["name"] for s in found], ["clean-code", "plain"])
        self.assertEqual(found[1]["description"], "Just rules.")
        self.assertTrue(found[0]["builtin"] and not found[1]["builtin"])
        self.assertEqual(skills.enabled_names(self.root), ["clean-code"])  # the defaults that exist
        skills.set_enabled(self.root, "plain", True)
        skills.set_enabled(self.root, "clean-code", False)
        self.assertEqual(skills.enabled_names(self.root), ["plain"])
        count, chars = skills.prompt_size(self.root)
        self.assertEqual(count, 1)
        self.assertGreater(chars, 10)

    def test_new_import_remove(self):
        path = skills.new_skill(self.root, "API Design")
        self.assertTrue(path.endswith(os.path.join("api-design", "SKILL.md")))
        self.assertIn("name: api-design", open(path, encoding="utf-8").read())
        single = os.path.join(self.root, "notes.md")
        with open(single, "w", encoding="utf-8") as f:
            f.write("Keep functions short.\n")
        imported = skills.import_skill(self.root, single)
        self.assertEqual(skills.parse_skill_file(open(imported, encoding="utf-8").read(), "x")["name"], "notes")
        with self.assertRaises(ValueError):
            skills.import_skill(self.root, single)  # already there
        skills.remove_skill(self.root, "notes")
        self.assertNotIn("notes", [s["name"] for s in skills.load_skills(self.root)])


if __name__ == "__main__":
    unittest.main()
