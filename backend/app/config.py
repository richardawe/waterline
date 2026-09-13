from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _model_chain(value: str) -> list[str]:
    """A single model id and a comma-separated fallback chain are both valid
    config — an existing deployment with one id set keeps working unchanged."""
    return [m.strip() for m in value.split(",") if m.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://waterline:waterline@localhost:5432/waterline"
    environment: str = "development"
    api_cors_origins: str = "http://localhost:5500,http://localhost:8000"
    admin_api_username: str | None = None
    admin_api_password: str | None = None

    # OpenRouter — free OSS models, as a comma-separated *fallback chain*
    # tried left to right, not a single id. Free-tier availability rotates
    # fast and a pulled model is indistinguishable from a typo from the
    # outside: three separate slugs have now died under this pipeline
    # (openai/gpt-oss-20b:free, deepseek/deepseek-chat-v3.1:free, then
    # minimax/minimax-m3:free on 2026-09-08), each time taking daily
    # generation down until someone noticed. A chain means one slug going
    # away costs a fallback hop, not an outage.
    #
    # `openrouter/free` is deliberately last: it's OpenRouter's own router
    # across whatever is currently free, so it stays resolvable even when
    # every named slug above it has been pulled. Same chain for the writer
    # and QA roles by design, independently env-configurable in case that
    # ever needs to change.
    openrouter_api_key: str | None = None
    openrouter_writer_model: str = "google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free,openrouter/free"
    openrouter_qa_model: str = "google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free,openrouter/free"

    # Blog pipeline
    blog_site_base_url: str = "https://waterline.ng"
    blog_news_feed_urls: str = "https://nairametrics.com/feed/,https://techcabal.com/feed/"
    blog_news_max_age_days: int = 14

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def openrouter_writer_models(self) -> list[str]:
        return _model_chain(self.openrouter_writer_model)

    @property
    def openrouter_qa_models(self) -> list[str]:
        return _model_chain(self.openrouter_qa_model)

    @property
    def blog_news_feeds(self) -> list[str]:
        return [u.strip() for u in self.blog_news_feed_urls.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
