from importlib.metadata import version
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

USER_AGENT = f"PodPocket/{version('podpocket')} (+https://github.com/Garulf/podpocket)"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PODPOCKET_")

    database_url: str = "sqlite+aiosqlite:////data/podpocket.db"


class Settings(DatabaseSettings):
    public_url: str
    secret_key: SecretStr = Field(min_length=32)
    access_token_minutes: int = 60
    cache_dir: Path = Path("/cache")
    region: str = "us"
    refresh_interval_minutes: int = 30
    run_scheduler: bool = True
    allow_private_feeds: bool = False
    max_feed_bytes: int = 50 * 1024 * 1024
    max_image_bytes: int = 20 * 1024 * 1024

    @field_validator("public_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")
