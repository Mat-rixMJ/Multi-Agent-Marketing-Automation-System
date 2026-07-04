"""LLM client. Supports OpenRouter, NVIDIA build, and local Ollama.
All expose OpenAI-compatible /chat/completions endpoints, so one thin wrapper
covers all — just swap LLM_PROVIDER in .env.
"""
import os

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

_PROVIDERS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
    },
    "nvidia": {
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_env": "NVIDIA_API_KEY",
        "model_env": "NVIDIA_MODEL",
    },
    "ollama": {
        "url": "http://localhost:11434/v1/chat/completions",
        "key_env": None,
        "model_env": "OLLAMA_MODEL",
    },
}


# Free models to rotate through when hitting rate limits on OpenRouter
_FREE_MODELS = [
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3-coder:free",
]
_model_rotation_idx = 0


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=3, max=30))
def chat(messages: list[dict], temperature: float = 0.7, max_tokens: int = 1200) -> str:
    global _model_rotation_idx
    provider = os.getenv("LLM_PROVIDER", "openrouter")
    cfg = _PROVIDERS[provider]
    api_key = os.getenv(cfg["key_env"]) if cfg["key_env"] else "ollama"
    model = os.getenv(cfg["model_env"])
    if cfg["key_env"] and not api_key:
        raise RuntimeError(f"{cfg['key_env']} not set in .env")

    headers = {"Content-Type": "application/json"}
    if api_key != "ollama":
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.post(
        cfg["url"],
        headers=headers,
        json={"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
        timeout=300,
    )

    # On rate limit (429), rotate to next free model and retry
    if resp.status_code == 429 and provider == "openrouter":
        _model_rotation_idx = (_model_rotation_idx + 1) % len(_FREE_MODELS)
        new_model = _FREE_MODELS[_model_rotation_idx]
        os.environ[cfg["model_env"]] = new_model
        print(f"  [LLM] Rate limited, rotating to: {new_model}")
        resp.raise_for_status()  # triggers tenacity retry

    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def ask(system_prompt: str, user_prompt: str, **kwargs) -> str:
    return chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        **kwargs,
    )
