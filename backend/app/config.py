from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://waterline:waterline@localhost:5432/waterline"
    environment: str = "development"
    api_cors_origins: str = "http://localhost:5500,http://localhost:8000"
    admin_api_username: str | None = None
    admin_api_password: str | None = None

    # OpenRouter — free OSS model. gpt-oss-20b is the one confirmed working;
    # other free-tier models were tried and didn't. Same model id for both
    # the writer and QA roles by design (env-configurable independently in
    # case that ever needs to change) — free-tier availability rotates, so
    # these are config-driven defaults, not hardcoded in the pipeline.
    openrouter_api_key: str | None = None
    openrouter_writer_model: str = "openai/gpt-oss-20b:free"
    openrouter_qa_model: str = "openai/gpt-oss-20b:free"

    # Blog pipeline
    blog_site_base_url: str = "https://waterline.ng"
    blog_news_feed_urls: str = "https://nairametrics.com/feed/,https://techcabal.com/feed/"
    blog_news_max_age_days: int = 14

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def blog_news_feeds(self) -> list[str]:
        return [u.strip() for u in self.blog_news_feed_urls.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
