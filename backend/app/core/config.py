from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    LLM_PROVIDER: str = "openrouter"
    LLM_MODEL: str = "openai/gpt-4o-mini"
    OPENROUTER_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    XAI_API_KEY: str = ""
    GOOGLE_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    CLOUDFLARE_ACCOUNT_ID: str = ""
    CLOUDFLARE_API_KEY: str = ""
    DATABASE_URL: str = "sqlite:///./sara.db"
    TRANSACTION_POLL_SECONDS: int = 15
    TRANSACTION_MONITOR_DEPTH: int = 256
    EVM_CONFIRMATIONS_DEFAULT: int = 12
    EVM_CONFIRMATIONS_POLYGON: int = 64

    # Address risk screening (Stage 5.6) — provider-neutral. No vendor ships
    # configured by default; PROVIDER + API_KEY select the adapter and the
    # generic HTTP adapter additionally requires an HTTPS API_URL. Until configured, every
    # screen returns "unavailable" rather than a fabricated "clear" result.
    RISK_SCREENING_PROVIDER: str = ""
    RISK_SCREENING_API_KEY: str = ""
    RISK_SCREENING_API_URL: str = ""
    RISK_SCREENING_MANDATORY: bool = False
    RISK_SCREENING_TTL_HOURS: int = 24

    model_config = SettingsConfigDict(env_file="../.env.local", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
