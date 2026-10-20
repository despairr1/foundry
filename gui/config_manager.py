import os
import re
import json
import urllib.request
import urllib.error

DEFAULT_CONFIG = {
    # The three models (src/config.ts reads these)
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "claude-fable-5-1",
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com/v1/messages",
    "ANTHROPIC_MAX_TOKENS": "65536",
    "ANTHROPIC_REFUSAL_FALLBACKS": "true",

    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "gemini-3.1-pro-preview",
    "GEMINI_EMBEDDING_MODEL": "gemini-embedding-2",
    "GEMINI_BASE_URL": "https://generativelanguage.googleapis.com/v1beta",
    "GEMINI_MAX_OUTPUT_TOKENS": "",

    # The code writer (synthesizer): OpenAI, or any OpenAI-compatible provider through the ASTRA_* settings
    "OPENAI_API_KEY": "",
    "OPENAI_MODEL": "gpt-5",
    "ASTRA_API_KEY": "",
    "ASTRA_MODEL": "",
    "ASTRA_BASE_URL": "https://api.openai.com/v1/chat/completions",
    "ASTRA_MAX_TOKENS": "",
    "ASTRA_INPUT_PRICE_PER_M": "",
    "ASTRA_OUTPUT_PRICE_PER_M": "",
    "FOUNDRY_SYNTHESIZER": "openai",

    # Who holds which role: claude, gemini or openai (openai = the OpenAI key or the custom provider chosen as the code writer)
    "FOUNDRY_ARCHITECT": "claude",
    "FOUNDRY_AUDITOR": "gemini",
    "FOUNDRY_WRITER": "openai",

    # Thinking and requests
    "FOUNDRY_THINKING": "high",
    "REQUEST_TIMEOUT_MS": "600000",
    "REQUEST_MAX_RETRIES": "3",

    # Spending limits
    "MAX_BUDGET_USD": "5.00",
    "MAX_MODULE_BUDGET_USD": "",
    "UI_SELF_IMPROVE_LIMIT": "5.00",
    "FOUNDRY_CHAT_BUDGET_USD": "1.00",

    # Builds
    "MAX_CONVERGENCE_ITERATIONS": "8",
    "MIN_MUTATION_KILL_RATIO": "0.85",
    "AUTO_GIT_COMMIT": "true",
    "FOUNDRY_FORCE_LOCAL_SANDBOX": "false",
    "FOUNDRY_SANDBOX_IMAGE": "foundry-sandbox:latest",
    "FOUNDRY_SANDBOX_TIMEOUT_MS": "120000",
    "FOUNDRY_LSP_TIMEOUT_MS": "1500",
    "DOCKER_MEMORY_MB": "1024",
    "DOCKER_CPUS": "2.0",
    "FOUNDRY_OFFLINE_MODE": "false",
    "FOUNDRY_SQLITE_PATH": ".foundry-memory.sqlite",
    "FOUNDRY_FREEZE_PATH": ".foundry-freeze.json",
    "NODE_BINARY_PATH": "node",

    # Improve and Ouroboros
    "UI_SELF_IMPROVE_ROUNDS": "1",
    "OUROBOROS_AUTO_HEAL": "true",
    "OUROBOROS_MAX_LAG_MS": "180",
    "OUROBOROS_MAX_AST_MS": "350",
    "OUROBOROS_MAX_HEAP_MB": "1024",
    "OUROBOROS_AUTO_RESTART": "false",

    # The window
    "UI_THEME_MODE": "Dark",
    "UI_START_PAGE": "improve",
    "UI_CONFIRM_STOP": "true",
    "UI_OPEN_OVERVIEW": "true",
    "SOUND_NOTIFICATIONS": "true",
    "TERMINAL_SHELL": "auto",
    "TERMINAL_FONT_FAMILY": "",
    "TERMINAL_FONT_SIZE": "12",
    "TERMINAL_SCROLLBACK": "5000",
}

PROFILES = {
    "Deep Reasoning (Claude Fable / 3.7 + Gemini 2.0 + GPT-4o)": {
        "ANTHROPIC_MODEL": "claude-fable-5-1",
        "ANTHROPIC_EXTENDED_THINKING": "true",
        "ANTHROPIC_THINKING_BUDGET": "8192",
        "GEMINI_MODEL": "gemini-2.0-flash-thinking-exp",
        "ASTRA_MODEL": "o3-mini",
        "MAX_BUDGET_USD": "15.00",
        "MAX_CONVERGENCE_ITERATIONS": "16",
        "MIN_MUTATION_KILL_RATIO": "0.95",
        "MCTS_BRANCH_COUNT": "5",
        "TRI_MODEL_DEBATE_ROUNDS": "3",
        "FOUNDRY_OFFLINE_MODE": "false"
    },
    "Ultra-Fast Dev (Gemini 2.0 Flash + Claude Haiku + GPT-4o-mini)": {
        "ANTHROPIC_MODEL": "claude-haiku-5-5",
        "GEMINI_MODEL": "gemini-2.0-flash",
        "ASTRA_MODEL": "gpt-4o-mini",
        "MAX_BUDGET_USD": "2.50",
        "MAX_CONVERGENCE_ITERATIONS": "4",
        "MIN_MUTATION_KILL_RATIO": "0.80",
        "MCTS_BRANCH_COUNT": "2",
        "TRI_MODEL_DEBATE_ROUNDS": "1",
        "FOUNDRY_OFFLINE_MODE": "false"
    },
    "Offline / Air-Gapped (Deterministic Local Ollama)": {
        "FOUNDRY_OFFLINE_MODE": "true",
        "MAX_BUDGET_USD": "0.00",
        "MAX_CONVERGENCE_ITERATIONS": "8",
        "MIN_MUTATION_KILL_RATIO": "0.85",
        "MCTS_BRANCH_COUNT": "3",
        "OLLAMA_ENDPOINT": "http://127.0.0.1:11434"
    },
    "Cost-Saver Production ($1.00 Ceiling)": {
        "ANTHROPIC_MODEL": "claude-haiku-5-5",
        "GEMINI_MODEL": "gemini-1.5-flash",
        "ASTRA_MODEL": "gpt-4o-mini",
        "MAX_BUDGET_USD": "1.00",
        "MAX_CONVERGENCE_ITERATIONS": "4",
        "MIN_MUTATION_KILL_RATIO": "0.85",
        "MCTS_BRANCH_COUNT": "2",
        "CIRCUIT_BREAKER_POLICY": "FREEZE_AND_DUMP"
    }
}

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([\w.-]+)\s*=(.*)$")
_QUOTED_VALUE = re.compile(r"""^('(?:\\'|[^'])*'|"(?:\\"|[^"])*"|`(?:\\`|[^`])*`)\s*(?:#.*)?$""")


def _parse_env_value(raw):
    """Parse a .env value the same way the engine's dotenv (v16) does."""
    v = raw.strip()
    m = _QUOTED_VALUE.match(v)
    if m:
        quote, inner = m.group(1)[0], m.group(1)[1:-1]
        if quote == '"':
            inner = inner.replace("\\n", "\n").replace("\\r", "\r")
        return inner
    # Unquoted: dotenv stops at the first '#'
    return v.split("#", 1)[0].strip()


def _format_env_value(value):
    """Quote a value only when dotenv would otherwise truncate or alter it."""
    v = "" if value is None else str(value)
    if not (set(v) & set("#'\"`\r\n")) and v == v.strip():
        return v
    if not (set(v) & set("'\r\n")):
        return f"'{v}'"
    if '"' not in v and "\\n" not in v and "\\r" not in v:
        return '"' + v.replace("\r", "\\r").replace("\n", "\\n") + '"'
    if not (set(v) & set("`\r\n")):
        return f"`{v}`"
    return v.replace("\r", " ").replace("\n", " ")


class ConfigManager:
    """Manages reading, writing, and syncing Foundry settings with .env, JSON backups, and process environment."""

    def __init__(self, workspace_root=None):
        if workspace_root is None:
            self.workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        else:
            self.workspace_root = workspace_root

        self.env_path = os.path.join(self.workspace_root, ".env")
        self.config = dict(DEFAULT_CONFIG)
        self._env_pushed = {}  # values this GUI wrote into os.environ itself
        self.load()

    def _sync_environ(self):
        """Mirror config into os.environ. Engine subprocesses inherit it and dotenv never overrides
        variables that are already set, so a stale value here would shadow the .env file."""
        for k, v in list(self._env_pushed.items()):
            if k not in self.config and os.environ.get(k) == v:
                os.environ.pop(k, None)
        self._env_pushed = {}
        for k, v in self.config.items():
            os.environ[k] = str(v)
            self._env_pushed[k] = str(v)

    def load(self):
        """Load settings from .env and environment variables."""
        self.config = dict(DEFAULT_CONFIG)

        if os.path.exists(self.env_path):
            try:
                with open(self.env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        m = _ENV_LINE.match(line)
                        if m:
                            self.config[m.group(1)] = _parse_env_value(m.group(2))
            except Exception as e:
                print(f"[ConfigManager] Error reading .env: {e}")

        # Real environment variables still win (same precedence as dotenv), but values this GUI
        # pushed into os.environ itself must not override fresh edits to the .env file.
        for key in self.config:
            env_val = os.environ.get(key)
            if env_val and env_val != self._env_pushed.get(key):
                self.config[key] = env_val

        self._sync_environ()
        return self.config

    def save(self, updates=None):
        """Save settings to .env file and update current process environment."""
        if updates:
            self.config.update(updates)

        self._sync_environ()

        try:
            categories = {
                "AI models and keys": [
                    "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL", "ANTHROPIC_MAX_TOKENS", "ANTHROPIC_REFUSAL_FALLBACKS",
                    "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_EMBEDDING_MODEL", "GEMINI_BASE_URL", "GEMINI_MAX_OUTPUT_TOKENS",
                    "OPENAI_API_KEY", "OPENAI_MODEL", "ASTRA_API_KEY", "ASTRA_MODEL", "ASTRA_BASE_URL", "ASTRA_MAX_TOKENS",
                    "ASTRA_INPUT_PRICE_PER_M", "ASTRA_OUTPUT_PRICE_PER_M", "FOUNDRY_SYNTHESIZER",
                    "FOUNDRY_ARCHITECT", "FOUNDRY_AUDITOR", "FOUNDRY_WRITER",
                    "FOUNDRY_THINKING", "REQUEST_TIMEOUT_MS", "REQUEST_MAX_RETRIES"
                ],
                "Spending limits": ["MAX_BUDGET_USD", "MAX_MODULE_BUDGET_USD", "UI_SELF_IMPROVE_LIMIT", "FOUNDRY_CHAT_BUDGET_USD"],
                "Builds": [
                    "MAX_CONVERGENCE_ITERATIONS", "MIN_MUTATION_KILL_RATIO", "AUTO_GIT_COMMIT", "FOUNDRY_FORCE_LOCAL_SANDBOX",
                    "FOUNDRY_SANDBOX_IMAGE", "FOUNDRY_SANDBOX_TIMEOUT_MS", "FOUNDRY_LSP_TIMEOUT_MS", "DOCKER_MEMORY_MB", "DOCKER_CPUS",
                    "FOUNDRY_OFFLINE_MODE", "FOUNDRY_SQLITE_PATH", "FOUNDRY_FREEZE_PATH", "NODE_BINARY_PATH"
                ],
                "Improve and Ouroboros": [
                    "UI_SELF_IMPROVE_ROUNDS", "OUROBOROS_AUTO_HEAL", "OUROBOROS_MAX_LAG_MS", "OUROBOROS_MAX_AST_MS",
                    "OUROBOROS_MAX_HEAP_MB", "OUROBOROS_AUTO_RESTART"
                ],
                "The window": [
                    "UI_THEME_MODE", "UI_START_PAGE", "UI_CONFIRM_STOP", "UI_OPEN_OVERVIEW", "SOUND_NOTIFICATIONS",
                    "TERMINAL_SHELL", "TERMINAL_FONT_FAMILY", "TERMINAL_FONT_SIZE", "TERMINAL_SCROLLBACK"
                ]
            }

            existing = []
            if os.path.exists(self.env_path):
                with open(self.env_path, "r", encoding="utf-8") as f:
                    existing = f.read().splitlines()

            written_keys = set()
            if existing:
                # Rewrite the existing file in place so comments, ordering and unknown lines survive.
                key_category = {k: cat for cat, keys in categories.items() for k in keys}
                lines, last_line_of_cat = [], {}
                for line in existing:
                    m = _ENV_LINE.match(line)
                    if m and m.group(1) in self.config:
                        k = m.group(1)
                        line = f"{k}={_format_env_value(self.config[k])}"
                        written_keys.add(k)
                        if k in key_category:
                            last_line_of_cat[key_category[k]] = len(lines)
                    lines.append(line)

                inserts, tail = [], []
                for cat_name, keys in categories.items():
                    missing = [f"{k}={_format_env_value(self.config.get(k, ''))}" for k in keys if k not in written_keys]
                    written_keys.update(k for k in keys)
                    if not missing:
                        continue
                    if cat_name in last_line_of_cat:
                        inserts.append((last_line_of_cat[cat_name] + 1, missing))
                    else:
                        tail += ["", f"# --- {cat_name} ---"] + missing
                for idx, new_lines in sorted(inserts, reverse=True):
                    lines[idx:idx] = new_lines
                lines += tail
            else:
                lines = [
                    "# ====================================================================",
                    "# Foundry Autonomous Software Engineering Engine — Environment Config",
                    "# ====================================================================",
                    ""
                ]
                for cat_name, keys in categories.items():
                    lines.append(f"# --- {cat_name} ---")
                    for k in keys:
                        lines.append(f"{k}={_format_env_value(self.config.get(k, ''))}")
                        written_keys.add(k)
                    lines.append("")

            remaining = [k for k in self.config if k not in written_keys]
            if remaining:
                lines += ["", "# --- Other Parameters ---"]
                for k in remaining:
                    lines.append(f"{k}={_format_env_value(self.config[k])}")

            with open(self.env_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).rstrip("\n") + "\n")
            return True, "Settings saved to .env successfully"
        except Exception as e:
            return False, f"Failed to save .env: {str(e)}"

    def get(self, key, default=""):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = str(value)
        os.environ[key] = str(value)
        self._env_pushed[key] = str(value)

    def apply_profile(self, profile_name):
        """Apply predefined profile configuration preset."""
        if profile_name in PROFILES:
            updates = PROFILES[profile_name]
            self.config.update(updates)
            for k, v in updates.items():
                os.environ[k] = str(v)
            self.save()
            return True, f"Applied '{profile_name}' preset"
        return False, f"Profile '{profile_name}' not found"

    def export_json(self, export_path):
        """Export full configuration to JSON file."""
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
            return True, f"Exported configuration to {export_path}"
        except Exception as e:
            return False, f"Export error: {e}"

    def import_json(self, import_path):
        """Import full configuration from JSON file."""
        try:
            with open(import_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # .env values are strings; normalise JSON numbers/bools/null so the UI comparisons still work
                self.config.update({
                    str(k): ("" if v is None else ("true" if v is True else "false" if v is False else str(v)))
                    for k, v in data.items()
                })
                self.save()
                return True, f"Successfully imported {len(data)} settings"
            return False, "Invalid JSON format: expected key-value mapping"
        except Exception as e:
            return False, f"Import error: {e}"

    def reset_defaults(self):
        """Reset configuration back to DEFAULT_CONFIG."""
        self.config = dict(DEFAULT_CONFIG)
        self.save()
        return True, "Reset all settings to recommended defaults"

    @staticmethod
    def _openai_can_spend(key):
        body = json.dumps({"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}).encode("utf-8")
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "Foundry"})
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True, "The key works and the account can spend: Foundry will use it to write code."
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code == 429 and "insufficient_quota" in detail:
                return False, ("The key is valid, but OpenAI says this key's account has no credits. Credits belong to an "
                               "organization: make sure the key was created in the same organization (and project) where you added "
                               "the $, and give OpenAI a few minutes after paying.")
            if e.code in (403, 404):
                return True, "The key is valid (the small test model isn't enabled for it, so spending couldn't be checked)."
            return False, f"OpenAI HTTP {e.code}: {detail[:200]}"
        except Exception as e:
            return True, f"The key is valid (spending couldn't be checked: {e})."

    def test_connection(self, provider, custom_key=None, custom_url=None):
        """
        Test API connectivity for a given provider.
        Accepts custom_key passed directly from on-screen input widget so users don't fail when testing unsaved keys!
        """
        provider = provider.lower()
        try:
            # 1. Google Gemini
            if provider == "gemini":
                key = (custom_key if custom_key is not None else self.get("GEMINI_API_KEY")).strip()
                if not key or key == "gemini-default-key":
                    return False, "No Gemini API key entered. Please enter your Gemini key and click Save & Test."
                self.set("GEMINI_API_KEY", key)
                base_url = (custom_url or self.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")).rstrip("/")
                url = f"{base_url}/models"
                # Send the key as a header rather than in the URL so it can't leak via URLs in error text / proxy logs
                req = urllib.request.Request(url, headers={"x-goog-api-key": key, "User-Agent": "Foundry-Dashboard"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        return True, "Connected successfully! Google Gemini API key is valid and responsive."
                    return False, f"HTTP {resp.status}"

            # 2. Anthropic Claude (Uses official GET /v1/models to validate key and retrieve account models)
            elif provider == "anthropic":
                key = (custom_key if custom_key is not None else self.get("ANTHROPIC_API_KEY")).strip()
                if not key or key == "sk-ant-default":
                    return False, "No Anthropic API key entered. Please enter your Claude key and click Save & Test."
                self.set("ANTHROPIC_API_KEY", key)

                models_url = "https://api.anthropic.com/v1/models"
                headers = {
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "User-Agent": "Foundry-Dashboard"
                }
                try:
                    req = urllib.request.Request(models_url, headers=headers)
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        if resp.status == 200:
                            data = json.loads(resp.read().decode("utf-8"))
                            models_list = [m.get("id") for m in data.get("data", [])]
                            preview = ", ".join(models_list[:4]) if models_list else "Active"
                            return True, f"Connected successfully! Claude key is valid. ({len(models_list)} models available: {preview}...)"
                        return False, f"HTTP {resp.status}"
                except urllib.error.HTTPError as e:
                    if e.code == 401:
                        return False, "Anthropic Authentication Failed (HTTP 401): Invalid x-api-key."
                    return False, f"Anthropic HTTP {e.code}: {e.reason}"

            # 3. Astra (OpenAI / GPT Code Synthesizer)
            elif provider == "astra":
                key = (custom_key or self.get("ASTRA_API_KEY") or self.get("OPENAI_API_KEY")).strip()
                if not key or key == "astra-default-key":
                    return False, "No OpenAI / Astra API key entered. Please enter your key and click Save & Test."
                self.set("ASTRA_API_KEY", key)
                url = "https://api.openai.com/v1/models"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "Foundry-Dashboard"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        return True, "Connected successfully! Astra (OpenAI / GPT) endpoint is active and verified."
                    return False, f"HTTP {resp.status}"

            # 4. OpenAI
            elif provider == "openai":
                key = (custom_key if custom_key is not None else self.get("OPENAI_API_KEY")).strip()
                if not key:
                    return False, "No OpenAI API key entered. Please enter your key and click Save & Test."
                self.set("OPENAI_API_KEY", key)
                url = "https://api.openai.com/v1/models"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "Foundry-Dashboard"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status != 200:
                        return False, f"HTTP {resp.status}"
                # Listing models works even with no credits; a 1-token request (a tiny fraction of a cent) shows whether it can spend.
                return self._openai_can_spend(key)

            # 5. DeepSeek
            elif provider == "deepseek":
                key = (custom_key if custom_key is not None else self.get("DEEPSEEK_API_KEY")).strip()
                if not key:
                    return False, "No DeepSeek API key entered. Please enter your key and click Save & Test."
                self.set("DEEPSEEK_API_KEY", key)
                base_url = (custom_url or self.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
                url = f"{base_url}/models"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "Foundry-Dashboard"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        return True, "Connected successfully! DeepSeek API key is valid and models are active."
                    return False, f"HTTP {resp.status}"

            # 6. OpenRouter
            elif provider == "openrouter":
                key = (custom_key if custom_key is not None else self.get("OPENROUTER_API_KEY")).strip()
                if not key:
                    return False, "No OpenRouter API key entered. Please enter your key and click Save & Test."
                self.set("OPENROUTER_API_KEY", key)
                base_url = (custom_url or self.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip("/")
                url = f"{base_url}/models"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "Foundry-Dashboard"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        return True, "Connected successfully! OpenRouter universal model router is active."
                    return False, f"HTTP {resp.status}"

            # 7. Groq
            elif provider == "groq":
                key = (custom_key if custom_key is not None else self.get("GROQ_API_KEY")).strip()
                if not key:
                    return False, "No Groq API key entered. Please enter your key and click Save & Test."
                self.set("GROQ_API_KEY", key)
                url = "https://api.groq.com/openai/v1/models"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "Foundry-Dashboard"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        return True, "Connected successfully! Groq LPU inference endpoint is active."
                    return False, f"HTTP {resp.status}"

            # Any OpenAI-compatible endpoint (extra providers): GET <base>/models
            elif provider == "openai-compatible":
                from providers import test_provider
                ok, msg, _models = test_provider(custom_url or "", custom_key or "")
                return ok, msg

            # 8. Local Ollama
            elif provider == "ollama":
                endpoint = (custom_url or self.get("OLLAMA_ENDPOINT", "http://127.0.0.1:11434")).rstrip("/") + "/api/tags"
                req = urllib.request.Request(endpoint, headers={"User-Agent": "Foundry-Dashboard"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    if resp.status == 200:
                        return True, "Local Ollama server is running and responsive."
                    return False, f"HTTP {resp.status}"

            return False, f"Unknown provider: {provider}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: {e.reason}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
