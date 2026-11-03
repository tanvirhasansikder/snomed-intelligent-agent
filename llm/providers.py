"""
Presets for OpenAI-compatible providers. Endpoints, model names, free-tier
limits and prices change: confirm them in each provider's documentation
before a run, and record the exact model identifier used.
"""

from llm.client import MockLLM, OpenAICompatibleClient

PROVIDERS = {
    # Local, free, reproducible weights. Install Ollama and `ollama pull <model>` first.
    "ollama": {"base_url": "http://localhost:11434/v1", "api_key_env": None},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key_env": "GEMINI_API_KEY"},
    "mock": {},
}


def build_client(spec):
    """Create a client from a model entry of an experiment configuration."""

    provider = spec.get("provider")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}; choose one of {sorted(PROVIDERS)}")
    if provider == "mock":
        return MockLLM(name=spec.get("name", "mock"), model=spec.get("model", "mock-1"), **spec.get("mock", {}))

    preset = PROVIDERS[provider]
    return OpenAICompatibleClient(
        name=spec.get("name", provider),
        base_url=spec.get("base_url", preset["base_url"]),
        model=spec["model"],
        api_key_env=spec.get("api_key_env", preset["api_key_env"]),
        temperature=spec.get("temperature", 0.0),
        max_tokens=spec.get("max_tokens", 800),
        timeout=spec.get("timeout", 120),
        rpm=spec.get("rpm"),
        json_mode=spec.get("json_mode", False),
        seed=spec.get("seed"),
        extra_body=spec.get("extra_body"),
        max_retries=spec.get("max_retries", 4),
    )
