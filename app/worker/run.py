"""Worker entry point: poll the DB for pending inbound faxes and process them.

Usage: uv run python -m app.worker.run
"""

import logging
import time

from app.config import get_settings
from app.db import init_db
from app.worker.delivery import process_retries
from app.worker.pipeline import process_pending
from app.worker.retention import purge_expired

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("faxbot.worker")

PURGE_INTERVAL_SECONDS = 3600


def main() -> None:
    settings = get_settings()
    init_db()
    # Fail fast if the persona file didn't make it into this environment,
    # rather than on the first model-backed fax.
    from app.worker.brain import _persona

    _persona()
    logger.info(
        "worker up: model=%s page_cap=%d poll=%.1fs ani_cap=%d/day budget=$%.0f/day",
        settings.anthropic_model,
        settings.page_cap,
        settings.worker_poll_seconds,
        settings.per_ani_daily_cap,
        settings.daily_llm_budget_usd,
    )
    last_purge = 0.0
    while True:
        try:
            n = process_pending()
            n += process_retries()
            if n:
                logger.info("processed %d item(s)", n)
            if time.monotonic() - last_purge > PURGE_INTERVAL_SECONDS:
                purge_expired()
                last_purge = time.monotonic()
        except Exception:
            logger.exception("poll iteration failed")
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
