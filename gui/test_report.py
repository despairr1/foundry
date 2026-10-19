"""The Tests page's plain-Python side: which command runs a folder's tests, and what its output said.

detect_test_command() looks at the folder the way a developer would (package.json's test script, Cargo.toml, go.mod,
pytest or unittest files). parse_test_output() reads the summary lines the common runners print (vitest, Jest,
pytest, unittest, cargo test, go test) into pass/fail counts; when it recognizes none, it says so instead of guessing.
docker_state() asks the docker command line what is really there, because the engine's sandbox only isolates tests
when the sandbox image exists."""
import json
import os
import re
import shutil
import subprocess

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r")
NPM_PLACEHOLDER = "no test specified"


def detect_test_command(folder):
    """{"command", "source", "framework"} for the folder, or None when nothing recognizable is there."""
    if not folder or not os.path.isdir(folder):
        return None
    package = os.path.join(folder, "package.json")
    if os.path.isfile(package):
        try:
            with open(package, "r", encoding="utf-8") as f:
                scripts = json.load(f).get("scripts") or {}
            script = str(scripts.get("test") or "")
            if script and NPM_PLACEHOLDER not in script:
                framework = next((name for name in ("vitest", "jest", "mocha", "ava", "tap") if name in script), None)
                if framework is None and "node --test" in script:
                    framework = "node:test"
                return {"command": "npm test", "source": "package.json", "framework": framework or "npm"}
        except (OSError, ValueError):
            pass
    if os.path.isfile(os.path.join(folder, "Cargo.toml")):
        return {"command": "cargo test", "source": "Cargo.toml", "framework": "cargo"}
    if os.path.isfile(os.path.join(folder, "go.mod")):
        return {"command": "go test ./...", "source": "go.mod", "framework": "go"}
    python_tests = _python_test_files(folder)
    if python_tests:
        if _pytest_configured(folder) or any(framework == "pytest" for _path, framework in python_tests):
            return {"command": "python -m pytest", "source": "pytest files", "framework": "pytest"}
        tests_dir = os.path.join(folder, "tests")
        start = " -s tests" if os.path.isdir(tests_dir) and any(p.startswith(tests_dir) for p, _f in python_tests) else ""
        return {"command": f"python -m unittest discover{start}", "source": "unittest files", "framework": "unittest"}
    return None


_PY_TEST_IMPORT = re.compile(r"^\s*(?:import|from)\s+(unittest|pytest)\b", re.MULTILINE)


def _python_test_files(folder, limit=200):
    """[(path, "pytest" | "unittest")] for test_*.py / *_test.py files that import a test framework."""
    found = []
    for current, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "venv", "__pycache__", "dist", "build", "site-packages")]
        for name in files:
            if not name.endswith(".py") or not (name.startswith("test_") or name.endswith("_test.py")):
                continue
            path = os.path.join(current, name)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    m = _PY_TEST_IMPORT.search(f.read(20000))
            except OSError:
                continue
            if m:
                found.append((path, m.group(1)))
                if len(found) >= limit:
                    return found
        if current.count(os.sep) - folder.count(os.sep) >= 3:
            dirs[:] = []
    return found


def _pytest_configured(folder):
    for name in ("pytest.ini", "conftest.py"):
        if os.path.isfile(os.path.join(folder, name)):
            return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8", errors="replace") as f:
                if "[tool.pytest" in f.read() or "[pytest]" in f.read() or "[tool:pytest]" in f.read():
                    return True
        except OSError:
            pass
    return False


def _counts_from_parts(text):
    """"1 failed | 86 passed" or "6 passed, 1 skipped, 7 total" -> {"passed": 86, "failed": 1, ...}."""
    counts = {}
    for number, word in re.findall(r"(\d+)\s+(passed|failed|skipped|todo|errors?|error|xfailed|xpassed|deselected|pending|total)", text):
        key = {"errors": "errors", "error": "errors"}.get(word, word)
        counts[key] = counts.get(key, 0) + int(number)
    return counts


def _empty(framework=None):
    return {"framework": framework, "passed": None, "failed": None, "skipped": None, "total": None, "files_passed": None,
            "files_failed": None, "duration": None, "failures": [], "summary": None}


def parse_test_output(text):
    """What a test runner's output says. Counts are None when the output has no recognizable summary."""
    clean = ANSI_RE.sub("", text or "")
    lines = clean.splitlines()
    for parser in (_parse_vitest, _parse_jest, _parse_pytest, _parse_unittest, _parse_cargo, _parse_go):
        result = parser(lines)
        if result is not None:
            result["summary"] = summarize(result)
            return result
    return _empty()


def _parse_vitest(lines):
    tests = next((m for m in (re.match(r"^\s*Tests\s+(.+?)\s*\((\d+)\)\s*$", l) for l in lines) if m), None)
    if tests is None:
        return None
    result = _empty("vitest")
    counts = _counts_from_parts(tests.group(1))
    result.update(passed=counts.get("passed", 0), failed=counts.get("failed", 0), skipped=counts.get("skipped", 0) + counts.get("todo", 0),
                  total=int(tests.group(2)))
    files = next((m for m in (re.match(r"^\s*Test Files\s+(.+?)\s*\((\d+)\)\s*$", l) for l in lines) if m), None)
    if files:
        file_counts = _counts_from_parts(files.group(1))
        result.update(files_passed=file_counts.get("passed", 0), files_failed=file_counts.get("failed", 0))
    duration = next((m for m in (re.match(r"^\s*Duration\s+(\S+)", l) for l in lines) if m), None)
    if duration:
        result["duration"] = duration.group(1)
    failures = []
    for line in lines:
        m = re.match(r"^\s*(?:×|✕|✗|FAIL)\s+(.+?)(?:\s+\d+(?:\.\d+)?ms)?\s*$", line)
        if m and m.group(1) not in failures and not m.group(1).startswith("Test Files"):
            failures.append(m.group(1))
    result["failures"] = failures[:50]
    return result


def _parse_jest(lines):
    tests = next((m for m in (re.match(r"^\s*Tests:\s+(.+)$", l) for l in lines) if m), None)
    if tests is None:
        return None
    result = _empty("jest")
    counts = _counts_from_parts(tests.group(1))
    result.update(passed=counts.get("passed", 0), failed=counts.get("failed", 0), skipped=counts.get("skipped", 0) + counts.get("todo", 0),
                  total=counts.get("total"))
    suites = next((m for m in (re.match(r"^\s*Test Suites:\s+(.+)$", l) for l in lines) if m), None)
    if suites:
        suite_counts = _counts_from_parts(suites.group(1))
        result.update(files_passed=suite_counts.get("passed", 0), files_failed=suite_counts.get("failed", 0))
    duration = next((m for m in (re.match(r"^\s*Time:\s+(\S+(?:\s*s)?)", l) for l in lines) if m), None)
    if duration:
        result["duration"] = duration.group(1).replace(" ", "")
    result["failures"] = [m.group(1).strip() for m in (re.match(r"^\s*●\s+(.+)$", l) for l in lines)
                          if m and not m.group(1).startswith("Console")][:50]
    return result


def _parse_pytest(lines):
    summary = None
    for line in lines:
        m = re.match(r"^=+\s+(.+?)\s+in\s+([\d.]+)s.*=+\s*$", line)
        if m:
            summary = m
    if summary is None:
        return None
    result = _empty("pytest")
    counts = _counts_from_parts(summary.group(1))
    result.update(passed=counts.get("passed", 0), failed=counts.get("failed", 0) + counts.get("errors", 0),
                  skipped=counts.get("skipped", 0) + counts.get("deselected", 0) + counts.get("xfailed", 0), duration=f"{summary.group(2)}s")
    result["total"] = result["passed"] + result["failed"] + result["skipped"]
    result["failures"] = [m.group(1) for m in (re.match(r"^(?:FAILED|ERROR)\s+(\S+)", l) for l in lines) if m][:50]
    return result


def _parse_unittest(lines):
    ran = None
    for line in lines:
        m = re.match(r"^Ran (\d+) tests? in ([\d.]+)s", line)
        if m:
            ran = m
    if ran is None:
        return None
    result = _empty("unittest")
    total, duration = int(ran.group(1)), f"{ran.group(2)}s"
    failed = errors = skipped = 0
    for line in lines:
        m = re.match(r"^(?:OK|FAILED)\s*\((.*)\)\s*$", line)
        if m:
            for key, value in re.findall(r"(failures|errors|skipped|expected failures|unexpected successes)=(\d+)", m.group(1)):
                if key == "failures":
                    failed = int(value)
                elif key == "errors":
                    errors = int(value)
                elif key == "skipped":
                    skipped = int(value)
    result.update(total=total, failed=failed + errors, skipped=skipped, passed=max(0, total - failed - errors - skipped), duration=duration)
    result["failures"] = [m.group(1).strip() for m in (re.match(r"^(?:FAIL|ERROR):\s+(.+)$", l) for l in lines) if m][:50]
    return result


def _parse_cargo(lines):
    results = [re.match(r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored(?:.*?finished in ([\d.]+)s)?", l) for l in lines]
    results = [m for m in results if m]
    if not results:
        return None
    result = _empty("cargo")
    result.update(passed=sum(int(m.group(2)) for m in results), failed=sum(int(m.group(3)) for m in results),
                  skipped=sum(int(m.group(4)) for m in results))
    result["total"] = result["passed"] + result["failed"] + result["skipped"]
    seconds = [float(m.group(5)) for m in results if m.group(5)]
    if seconds:
        result["duration"] = f"{sum(seconds):.2f}s"
    result["failures"] = [m.group(1) for m in (re.match(r"^test (\S+) \.\.\. FAILED", l) for l in lines) if m][:50]
    return result


def _parse_go(lines):
    ok_packages = [m.group(1) for m in (re.match(r"^ok\s+(\S+)", l) for l in lines) if m]
    failed_packages = [m.group(1) for m in (re.match(r"^FAIL\s+(\S+)", l) for l in lines) if m]
    fails = [m.group(1) for m in (re.match(r"^\s*--- FAIL: (\S+)", l) for l in lines) if m]
    passes = [m.group(1) for m in (re.match(r"^\s*--- PASS: (\S+)", l) for l in lines) if m]
    if not ok_packages and not failed_packages and not fails and not passes:
        return None
    result = _empty("go")
    if passes:  # only `go test -v` names passing tests; without it the count of passes is unknown
        result.update(passed=len(passes), total=len(passes) + len(fails))
    if fails:
        result["failed"] = len(fails)
    result.update(files_passed=len(ok_packages), files_failed=len(failed_packages), failures=fails[:50])
    return result


def summarize(result):
    """"86 passed, 1 failed" (or "12 packages passed" for go without -v); None when nothing was recognized."""
    if result["framework"] is None:
        return None
    parts = []
    if result["passed"] is not None:
        parts.append(f"{result['passed']} passed")
    if result["failed"]:
        parts.append(f"{result['failed']} failed")
    if result["skipped"]:
        parts.append(f"{result['skipped']} skipped")
    if result["passed"] is None and result["files_passed"] is not None:
        parts.append(f"{result['files_passed']} package{'s' if result['files_passed'] != 1 else ''} passed")
        if result["files_failed"]:
            parts.append(f"{result['files_failed']} package{'s' if result['files_failed'] != 1 else ''} failed")
    return ", ".join(parts) or "no counts in the output"


def docker_state(image, timeout=8):
    """What the docker command says: {"installed", "running", "version", "os", "image_present", "detail"}."""
    state = {"installed": bool(shutil.which("docker")), "running": False, "version": "", "os": "", "image_present": None, "detail": ""}
    if not state["installed"]:
        return state
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        info = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}|{{.OperatingSystem}}"], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        state["detail"] = f"docker info did not answer: {e}"
        return state
    output = (info.stdout or "").strip()
    if info.returncode != 0 or "|" not in output or output.startswith("|"):
        first_error = next((l.strip() for l in (info.stderr or info.stdout or "").splitlines() if l.strip()), "")
        state["detail"] = first_error[:200] or "the Docker daemon is not running"
        return state
    state["running"] = True
    state["version"], state["os"] = (output.split("|", 1) + [""])[:2]
    try:
        inspect = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **flags)
        state["image_present"] = inspect.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        state["image_present"] = None
    return state
