from pydantic import ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(extra="ignore")

    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    webhook_public_url: str = Field(
        default="http://127.0.0.1",
        alias="WEBHOOK_PUBLIC_URL",
    )
    webhook_public_port: int = Field(
        default=8579,
        alias="WEBHOOK_PUBLIC_PORT",
    )
    webhook_host_port: int = Field(default=8579, alias="WEBHOOK_HOST_PORT")

    friend_api_base_url: str = Field(
        default="http://snp-back:8000",
        alias="FRIEND_API_BASE_URL",
    )
    friend_api_key: str | None = Field(default=None, alias="FRIEND_API_KEY")
    friend_api_timeout_seconds: float = Field(
        default=10,
        alias="FRIEND_API_TIMEOUT_SECONDS",
    )
    friend_api_mock_mode: bool = Field(default=False, alias="FRIEND_API_MOCK_MODE")

    flow_ttl_hours: int = Field(default=24, alias="FLOW_TTL_HOURS")
    max_document_bytes: int = Field(default=10 * 1024 * 1024, alias="MAX_DOCUMENT_BYTES")
    enable_access_check: bool = Field(default=False, alias="ENABLE_ACCESS_CHECK")
    access_check_debug_override: bool = Field(default=False, alias="ACCESS_CHECK_DEBUG_OVERRIDE")
    access_check_debug_full_name: str = Field(default="", alias="ACCESS_CHECK_DEBUG_FULL_NAME")

    @model_validator(mode="after")
    def validate_friend_api_key(self) -> "Settings":
        if not self.friend_api_key and not self.friend_api_mock_mode:
            raise ValueError(
                "FRIEND_API_KEY is required unless FRIEND_API_MOCK_MODE=true"
            )
        return self

    @property
    def webhook_origin(self) -> str:
        origin = self.webhook_public_url.rstrip("/")
        if self.webhook_public_port in (80, 443):
            return origin
        if _origin_has_port(origin):
            return origin
        return f"{origin}:{self.webhook_public_port}"

    def webhook_url(self, hook: str) -> str:
        return f"{self.webhook_origin}/hooks/{hook.lstrip('/')}"


def _origin_has_port(origin: str) -> bool:
    host = origin.split("://", 1)[-1].split("/", 1)[0]
    if host.startswith("["):
        return "]:" in host
    return ":" in host


def get_settings() -> Settings:
    return Settings()
