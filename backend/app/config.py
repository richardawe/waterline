from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://waterline:waterline@localhost:5432/waterline"
    environment: str = "development"
    api_cors_origins: str = "http://localhost:5500,http://localhost:8000"
    admin_api_username: str | None = None
    admin_api_password: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
