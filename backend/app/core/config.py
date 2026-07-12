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

    SARA_NAME_REGISTRAR_ADDRESS: str = ""
    SARA_NAME_LOG_ADDRESS: str = ""
    SARA_NAME_REGISTRATION_FEE: float = 10.0
    SARA_NAME_SERVICE_URL: str = ""
    POLYGONSCAN_API_KEY: str = ""

    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
