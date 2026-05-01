from typing import AsyncGenerator
from app.core.config import settings
from app.llm.prompts import SARA_SYSTEM_PROMPT
import litellm

litellm.drop_params = True

class SaraLLM:
    def _build_kwargs(self) -> dict:
        provider = settings.LLM_PROVIDER.lower()
        model = settings.LLM_MODEL

        if provider == "openai":
            return {"model": model, "api_key": settings.OPENAI_API_KEY}
        elif provider == "anthropic":
            return {"model": f"anthropic/{model}", "api_key": settings.ANTHROPIC_API_KEY}
        elif provider == "groq":
            return {"model": f"groq/{model}", "api_key": settings.GROQ_API_KEY}
        elif provider == "ollama":
            return {"model": f"ollama/{model}", "api_base": settings.OLLAMA_BASE_URL}
        elif provider == "cloudflare":
            return {"model": f"cloudflare/{model}",
                    "api_key": settings.CLOUDFLARE_API_KEY,
                    "api_base": f"https://api.cloudflare.com/client/v4/accounts/{settings.CLOUDFLARE_ACCOUNT_ID}/ai/v1"}
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
