from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telnyx
    telnyx_api_key: SecretStr = SecretStr("")
    telnyx_public_key: SecretStr = SecretStr("")  # webhook Ed25519 verification key (base64)
    telnyx_connection_id: str = ""  # Fax application id for POST /v2/faxes
    fax_bot_number: str = ""  # public number replies are sent from, E.164
    test_fax_number: str = ""  # loop-guard number used by round-trip tests

    # Public URL of this server (ngrok in dev, prod domain later).
    # Telnyx fetches reply PDFs from {public_base_url}/media/... and posts
    # webhooks to {public_base_url}/webhooks/telnyx.
    public_base_url: str = "http://localhost:8000"

    # Set false only for local experiments; never in anything Telnyx can reach.
    webhook_verify: bool = True

    # Anthropic (Phase 1)
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5"

    # Worker
    page_cap: int = 20
    worker_poll_seconds: float = 2.0
    # Give up on an inbound fax after this many processing attempts.
    max_process_attempts: int = 3
    # A fax claimed longer than this is presumed crashed and gets requeued.
    stale_processing_seconds: int = 900

    # Abuse / cost controls (Phase 2)
    per_ani_daily_cap: int = 5
    daily_outbound_fax_cap: int = 200
    daily_llm_budget_usd: float = 20.0
    # claude-opus-5 list prices per MTok; used for the budget breaker only
    llm_input_price_per_mtok: float = 5.0
    llm_output_price_per_mtok: float = 25.0
    thread_context_exchanges: int = 5
    ani_thread_window_days: int = 30

    # Delivery retries: backoff seconds per retry, then give up
    retry_backoff_seconds: tuple[int, ...] = (300, 1800, 7200)

    # Retention: purge inbound/reply PDFs after this many days (transcripts stay)
    retention_days: int = 30

    # Phase 3: gallery + prod hardening
    # All /admin/* routes require the X-Admin-Token header; while this is
    # empty every admin route returns 401.
    admin_token: SecretStr = SecretStr("")
    sentry_dsn: SecretStr = SecretStr("")
    gallery_raster_dpi: int = 120
    # Publish the two built-in, clearly labeled house samples on startup.
    # The operation is idempotent and never republishes a removed sample.
    gallery_seed_samples: bool = False

    @property
    def gallery_dir(self) -> Path:
        return self.data_dir / "gallery"

    # Infrastructure
    # repr=False: the prod DSN embeds the DB password — keep it out of
    # settings reprs just like the SecretStr fields.
    database_url: str = Field(
        default="postgresql+psycopg://faxbot:faxbot@localhost:5432/faxbot", repr=False
    )
    data_dir: Path = Path("data")

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def inbound_dir(self) -> Path:
        return self.data_dir / "inbound"


@lru_cache
def get_settings() -> Settings:
    return Settings()
