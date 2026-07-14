from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(
        default="sqlite:///./bname-dev.db",
        validation_alias="BNAME_DATABASE_URL",
    )
    public_base_url: str = Field(default="http://localhost:8000", validation_alias="BNAME_PUBLIC_BASE_URL")
    cors_origins: str = Field(default="*", validation_alias="BNAME_CORS_ORIGINS")
    polygon_rpc_url: str = Field(default="https://polygon-bor-rpc.publicnode.com", validation_alias="BNAME_POLYGON_RPC_URL")
    anchor_signer_address: str = Field(default="", validation_alias="BNAME_ANCHOR_SIGNER_ADDRESS")
    registration_fee_pol: str = Field(default="10", validation_alias="BNAME_REGISTRATION_FEE_POL")
    hash_anchor_fee_pol: str = Field(default="2", validation_alias="BNAME_HASH_ANCHOR_FEE_POL")
    full_anchor_fee_pol: str = Field(default="10", validation_alias="BNAME_FULL_ANCHOR_FEE_POL")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
