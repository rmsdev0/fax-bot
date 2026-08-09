"""Retention: purge fax PDFs after N days. Transcripts and metadata stay."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import InboundFax, OutboundFax

logger = logging.getLogger("faxbot.retention")


def purge_expired() -> int:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
    session_factory = get_sessionmaker()
    purged = 0
    with session_factory() as session:
        old_inbound = session.scalars(
            select(InboundFax).where(
                InboundFax.received_at < cutoff, InboundFax.pdf_path.is_not(None)
            )
        ).all()
        for fax in old_inbound:
            path = Path(fax.pdf_path)
            if path.exists():
                path.unlink()
            fax.pdf_path = None
            purged += 1

        # Reply PDFs: the letter text survives in InboundFax.reply_body.
        old_outbound = session.scalars(
            select(OutboundFax).where(
                OutboundFax.created_at < cutoff,
                OutboundFax.status.in_(["delivered", "failed_permanent"]),
            )
        ).all()
        for fax in old_outbound:
            name = fax.media_url.rsplit("/", 1)[-1]
            if name.startswith("reply_"):
                path = settings.media_dir / name
                if path.exists():
                    path.unlink()
                    purged += 1
        session.commit()
    if purged:
        logger.info("retention purge removed %d file(s)", purged)
    return purged


def purge_thread(session, settings, thread_id: int) -> int:
    """Early purge for a REMOVE request: delete one thread's stored fax PDFs.

    Outbound reply PDFs still awaiting delivery are left for the normal
    retention cycle — Telnyx may not have fetched them yet. No commit here;
    the caller owns the transaction.
    """
    purged = 0
    inbound = session.scalars(
        select(InboundFax).where(
            InboundFax.thread_id == thread_id, InboundFax.pdf_path.is_not(None)
        )
    ).all()
    for fax in inbound:
        path = Path(fax.pdf_path)
        if path.exists():
            path.unlink()
        fax.pdf_path = None
        purged += 1

    outbound = session.scalars(
        select(OutboundFax).where(
            OutboundFax.thread_id == thread_id,
            OutboundFax.status.in_(["delivered", "failed_permanent"]),
        )
    ).all()
    for fax in outbound:
        name = fax.media_url.rsplit("/", 1)[-1]
        if name.startswith("reply_"):
            path = settings.media_dir / name
            if path.exists():
                path.unlink()
                purged += 1
    return purged
