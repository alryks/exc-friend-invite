from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    channel_id: str | None = Field(
        default=None,
        alias="CHANNEL_ID",
    )
    
    log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    webhook_public_url: str = Field(
        default="http://127.0.0.1",
        alias="WEBHOOK_PUBLIC_URL",
    )
    webhook_public_port: int = Field(
        default=8579,
        alias="WEBHOOK_PUBLIC_PORT",
    )


def get_settings() -> Settings:
    return Settings()
