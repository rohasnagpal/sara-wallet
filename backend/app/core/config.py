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

    # Public, credential-free BlockchainProof checkout API. Deployments may
    # point this at a compatible self-hosted instance without changing code.
    BLOCKCHAINPROOF_API_URL: str = "https://api.blockchainproof.org"
    BLOCKCHAINPROOF_CHAIN_ID: int = 137
    BLOCKCHAINPROOF_USDC_CONTRACT: str = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
    BLOCKCHAINPROOF_RECEIVER_ADDRESS: str = ""

    # Sara Names (Stage 6) — the deployed SaraNamesRegistry contract on
    # Polygon Amoy testnet. SARA_NAME_SERVICE_URL, if set, is an *external*
    # off-chain record-hosting service Sara publishes/fetches signed
    # records to/from — never treated as authoritative; every fetched
    # record is re-verified against live on-chain state before use.
    SARA_NAME_REGISTRAR_ADDRESS: str = ""
    # Comma-separated list — redundant RPC providers (Stage 7 reliability),
    # both verified reachable and returning chain_id 80002 before use here.
    SARA_NAME_AMOY_RPC_URL: str = "https://polygon-amoy-bor-rpc.publicnode.com,https://polygon-amoy.drpc.org"
    SARA_NAME_AMOY_USDC_ADDRESS: str = "0x41E94Eb019C0762f9Bfcf9Fb1E58725BfB0e7582"
    SARA_NAME_AMOY_CONFIRMATIONS: int = 12
    SARA_NAME_SERVICE_URL: str = ""
    POLYGONSCAN_API_KEY: str = ""

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
