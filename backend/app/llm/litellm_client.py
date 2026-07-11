import os
from typing import AsyncGenerator
from app.core.config import settings
from app.llm.prompts import SARA_SYSTEM_PROMPT
import litellm

litellm.drop_params = True

PROVIDER_DEFAULTS = {
    "openrouter": "openai/gpt-4o-mini",
    "groq":      "llama3-8b-8192",
    "openai":    "gpt-4o",
    "anthropic": "claude-opus-4-5-20251101",
    "xai":       "grok-beta",
    "gemini":    "gemini-1.5-pro",
    "ollama":    "llama3",
}

class SaraLLM:
    def _build_kwargs(self) -> dict:
        provider = os.environ.get("LLM_PROVIDER", settings.LLM_PROVIDER).lower()
        model = os.environ.get("LLM_MODEL", settings.LLM_MODEL) or PROVIDER_DEFAULTS.get(provider, "")

        if provider == "openrouter":
            return {"model": f"openrouter/{model}", "api_key": os.environ.get("OPENROUTER_API_KEY", settings.OPENROUTER_API_KEY)}
        elif provider == "openai":
            return {"model": model, "api_key": os.environ.get("OPENAI_API_KEY", settings.OPENAI_API_KEY)}
        elif provider == "anthropic":
            return {"model": f"anthropic/{model}", "api_key": os.environ.get("ANTHROPIC_API_KEY", settings.ANTHROPIC_API_KEY)}
        elif provider == "groq":
            return {"model": f"groq/{model}", "api_key": os.environ.get("GROQ_API_KEY", settings.GROQ_API_KEY)}
        elif provider == "xai":
            return {"model": f"xai/{model}", "api_key": os.environ.get("XAI_API_KEY", settings.XAI_API_KEY)}
        elif provider == "gemini":
            return {"model": f"gemini/{model}", "api_key": os.environ.get("GOOGLE_API_KEY", settings.GOOGLE_API_KEY)}
        elif provider == "ollama":
            return {"model": f"ollama/{model}", "api_base": os.environ.get("OLLAMA_BASE_URL", settings.OLLAMA_BASE_URL)}
        elif provider == "cloudflare":
            return {"model": f"cloudflare/{model}",
                    "api_key": os.environ.get("CLOUDFLARE_API_KEY", settings.CLOUDFLARE_API_KEY),
                    "api_base": f"https://api.cloudflare.com/client/v4/accounts/{os.environ.get('CLOUDFLARE_ACCOUNT_ID', settings.CLOUDFLARE_ACCOUNT_ID)}/ai/v1"}
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {provider}")

    async def stream_chat(self, messages: list[dict]) -> AsyncGenerator[str, None]:
        kwargs = self._build_kwargs()
        response = await litellm.acompletion(
            messages=messages,
            stream=True,
            **kwargs
        )
        async for chunk in response:
            token = chunk.choices[0].delta.content
            if token:
                yield token

sara_llm = SaraLLM()
