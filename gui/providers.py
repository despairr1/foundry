"""Extra AI providers: any OpenAI-compatible endpoint (DeepSeek, OpenRouter, Groq, Mistral, xAI, a local Ollama or
LM Studio, or something else), added in Settings › API keys & providers.

Providers are kept in <foundry>/.foundry/providers.json; their keys live in .env as PROVIDER_<ID>_API_KEY, like the
built-in keys. One provider at a time is the code writer (the synthesizer role): choosing it writes the ASTRA_*
settings the engine reads (ASTRA_BASE_URL, ASTRA_API_KEY, ASTRA_MODEL and the per-million-token prices used for the
spending limit). Choosing OpenAI again clears them, so the engine falls back to the OpenAI key."""
import json
import os
import re
import urllib.error
import urllib.request

SYNTHESIZER_PREF = "FOUNDRY_SYNTHESIZER"  # "openai" (default) or a provider id
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"

# (preset id, name, base URL, model suggestions, key hint, price per million tokens in/out or None)
PRESETS = [
    ("deepseek", "DeepSeek", "https://api.deepseek.com/v1", ["deepseek-chat", "deepseek-reasoner"], "sk-...", (0.27, 1.10)),
    ("openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
     ["openai/gpt-5", "anthropic/claude-sonnet-5-5", "deepseek/deepseek-chat", "qwen/qwen3-coder"], "sk-or-v1-...", None),
    ("groq", "Groq", "https://api.groq.com/openai/v1", ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"], "gsk_...", None),
    ("mistral", "Mistral", "https://api.mistral.ai/v1", ["codestral-latest", "mistral-large-latest"], "", None),
    ("xai", "xAI (Grok)", "https://api.x.ai/v1", ["grok-4", "grok-code-fast-1"], "xai-...", None),
    ("together", "Together AI", "https://api.together.xyz/v1", ["meta-llama/Llama-3.3-70B-Instruct-Turbo"], "", None),
    ("fireworks", "Fireworks", "https://api.fireworks.ai/inference/v1", ["accounts/fireworks/models/llama-v3p3-70b-instruct"], "", None),
    ("ollama", "Ollama (on this computer)", "http://127.0.0.1:11434/v1", ["qwen2.5-coder", "llama3.1", "deepseek-coder-v2"], "not needed", (0.0, 0.0)),
    ("lmstudio", "LM Studio (on this computer)", "http://127.0.0.1:1234/v1", [], "not needed", (0.0, 0.0)),
    ("custom", "Other (OpenAI-compatible)", "", [], "", None),
]
PRESET_BY_ID = {p[0]: p for p in PRESETS}

# Where to add credits or see the bill, per provider (built-in keys and presets). Local servers have none.
BILLING_URLS = {
    "anthropic": "https://console.anthropic.com/settings/billing",
    "gemini": "https://console.cloud.google.com/billing",
    "openai": "https://platform.openai.com/settings/organization/billing/overview",
    "deepseek": "https://platform.deepseek.com/top_up",
    "openrouter": "https://openrouter.ai/settings/credits",
    "groq": "https://console.groq.com/settings/billing",
    "mistral": "https://console.mistral.ai/billing/",
    "xai": "https://console.x.ai/",
    "together": "https://api.together.ai/settings/billing",
    "fireworks": "https://fireworks.ai/account/billing",
}


def billing_url(provider):
    """provider: a built-in name ("anthropic", "gemini", "openai") or an added provider dict; None for local servers."""
    if isinstance(provider, dict):
        return provider.get("billingUrl") or BILLING_URLS.get(provider.get("preset", ""))
    return BILLING_URLS.get(provider)


def providers_path(root):
    return os.path.join(root, ".foundry", "providers.json")


def load_providers(root):
    try:
        with open(providers_path(root), "r", encoding="utf-8") as f:
            items = json.load(f).get("providers", [])
    except Exception:
        return []
    return [p for p in items if isinstance(p, dict) and p.get("id") and p.get("name") and p.get("baseUrl")]


def save_providers(root, providers):
    path = providers_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ignore = os.path.join(os.path.dirname(path), ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("*\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "providers": providers}, f, indent=2)
    os.replace(tmp, path)


def provider_id(name, existing):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "provider"
    taken = {p["id"] for p in existing}
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def key_var(pid):
    return f"PROVIDER_{re.sub(r'[^A-Za-z0-9]+', '_', pid).upper()}_API_KEY"


def add_provider(root, name, base_url, model, preset="custom", price_in=None, price_out=None):
    providers = load_providers(root)
    pid = provider_id(name, providers)
    provider = {"id": pid, "name": name.strip(), "preset": preset, "baseUrl": base_url.strip().rstrip("/"),
                "model": model.strip(), "keyVar": key_var(pid)}
    if price_in is not None:
        provider["priceIn"] = float(price_in)
        provider["priceOut"] = float(price_out if price_out is not None else price_in)
    providers.append(provider)
    save_providers(root, providers)
    return provider


def update_provider(root, pid, **changes):
    providers = load_providers(root)
    for p in providers:
        if p["id"] == pid:
            p.update({k: v for k, v in changes.items() if v is not None})
    save_providers(root, providers)
    return providers


def remove_provider(root, pid):
    providers = [p for p in load_providers(root) if p["id"] != pid]
    save_providers(root, providers)
    return providers


def chat_endpoint(base_url):
    """The chat completions URL the engine expects in ASTRA_BASE_URL (it also accepts a bare base URL)."""
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions" if re.search(r"/v\d+$", base) else f"{base}/v1/chat/completions"


def synthesizer_settings(root, config_manager, choice):
    """The .env values that make `choice` ("openai" or a provider id) the code writer."""
    if choice == "openai":
        return {SYNTHESIZER_PREF: "openai", "ASTRA_API_KEY": "", "ASTRA_MODEL": "", "ASTRA_BASE_URL": OPENAI_ENDPOINT,
                "ASTRA_INPUT_PRICE_PER_M": "", "ASTRA_OUTPUT_PRICE_PER_M": ""}
    provider = next((p for p in load_providers(root) if p["id"] == choice), None)
    if provider is None:
        return None
    key = str(config_manager.get(provider["keyVar"], "") or "").strip() or "local"  # local servers take any token
    return {SYNTHESIZER_PREF: provider["id"], "ASTRA_API_KEY": key, "ASTRA_MODEL": provider.get("model", ""),
            "ASTRA_BASE_URL": chat_endpoint(provider["baseUrl"]),
            "ASTRA_INPUT_PRICE_PER_M": f"{provider['priceIn']:g}" if "priceIn" in provider else "",
            "ASTRA_OUTPUT_PRICE_PER_M": f"{provider['priceOut']:g}" if "priceOut" in provider else ""}


def current_synthesizer(root, config_manager):
    """("openai", None) or (provider id, provider)."""
    choice = str(config_manager.get(SYNTHESIZER_PREF, "openai") or "openai")
    if choice != "openai":
        provider = next((p for p in load_providers(root) if p["id"] == choice), None)
        if provider:
            return choice, provider
    return "openai", None


def test_provider(base_url, key, timeout=8):
    """(ok, message, model ids): GET <base>/models, which every OpenAI-compatible server answers."""
    base = base_url.strip().rstrip("/")
    if not re.search(r"/v\d+$", base):
        base = f"{base}/v1"
    headers = {"User-Agent": "Foundry"}
    if key.strip():
        headers["Authorization"] = f"Bearer {key.strip()}"
    try:
        req = urllib.request.Request(f"{base}/models", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "The key was not accepted (HTTP 401).", []
        return False, f"HTTP {e.code}: {e.reason}", []
    except Exception as e:
        return False, f"Couldn't connect: {e}", []
    models = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
    note = f"{len(models)} models available" if models else "connected"
    return True, f"Connected ({note}).", models
