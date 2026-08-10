"""Outbound delivery: sending, failure handling, and retries with backoff."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app import telnyx_client
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.models import OutboundAttempt, OutboundFax, Thread
from app.worker.policy import breaker_tripped, classify_failure

logger = logging.getLogger("faxbot.delivery")


def send_reply(
    session,
    settings: Settings,
    *,
    to: str,
    media_url: str,
    thread_id: int | None,
    reply_to_fax_id: str,
) -> OutboundFax:
    # Bounded at-least-once, not exactly-once: a reprocessed pipeline reuses
    # the existing row, and a recorded prior send is never repeated — but a
    # crash between Telnyx accepting and the commit below can still yield one
    # duplicate (capped by max_process_attempts). Telnyx's fax-create API has
    # no idempotency key to close that window; a duplicate letter beats a
    # lost one.
    outbound = session.scalar(
        select(OutboundFax).where(OutboundFax.reply_to_fax_id == reply_to_fax_id)
    )
    if outbound is not None and outbound.fax_id is not None:
        return outbound
    if outbound is None:
        outbound = OutboundFax(
            fax_id=None,
            status="pending_send",
            attempts=0,
            thread_id=thread_id,
            to_number=to,
            reply_to_fax_id=reply_to_fax_id,
            media_url=media_url,
        )
        session.add(outbound)
    # Intent goes to the DB before the API call: a crash right after the send
    # leaves a visible pending_send row instead of an untracked fax.
    session.commit()
    fax_id = telnyx_client.send_fax(settings, to=to, media_url=media_url)
    outbound.fax_id = fax_id
    outbound.status = "queued"
    outbound.attempts += 1
    session.add(
        OutboundAttempt(outbound_id=outbound.id, telnyx_fax_id=fax_id, attempt_no=outbound.attempts)
    )
    session.commit()
    return outbound


def handle_failure(session, outbound: OutboundFax, failure_reason: str | None) -> None:
    """Called from the webhook handler on an outbound fax.failed event."""
    settings = get_settings()
    outbound.failure_reason = failure_reason
    kind = classify_failure(failure_reason)
    backoffs = settings.retry_backoff_seconds
    retries_used = max(outbound.attempts - 1, 0)

    if kind == "permanent":
        outbound.status = "failed_permanent"
        if outbound.thread_id:
            thread = session.get(Thread, outbound.thread_id)
            if thread and thread.status == "active":
                thread.status = "undeliverable"
                logger.warning(
                    "thread %s marked undeliverable (%s): %s",
                    thread.ref_number,
                    outbound.to_number,
                    failure_reason,
                )
    elif retries_used >= len(backoffs):
        outbound.status = "failed_permanent"
        logger.warning(
            "giving up on fax to %s after %d attempts (%s)",
            outbound.to_number,
            outbound.attempts,
            failure_reason,
        )
    else:
        delay = backoffs[retries_used]
        outbound.status = "retry_scheduled"
        outbound.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        logger.info(
            "fax to %s failed (%s); retry %d/%d in %ds",
            outbound.to_number,
            failure_reason,
            retries_used + 1,
            len(backoffs),
            delay,
        )
    session.commit()


def _recover_stale_retrying(session, settings: Settings) -> None:
    """A crash between claiming a retry and recording its result leaves the
    row in 'retrying'. Requeue it once the stale window passes — the attempt
    was already counted at claim time, so a crash-after-send costs at most
    one duplicate and still marches toward the attempt cap."""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.stale_processing_seconds)
    stale = session.scalars(
        select(OutboundFax).where(OutboundFax.status == "retrying", OutboundFax.updated_at < cutoff)
    ).all()
    for outbound in stale:
        exhausted = outbound.attempts > len(settings.retry_backoff_seconds)
        outbound.status = "failed_permanent" if exhausted else "retry_scheduled"
        outbound.next_retry_at = None if exhausted else datetime.now(UTC)
        logger.warning(
            "stale retrying row %d (attempts=%d) -> %s",
            outbound.id,
            outbound.attempts,
            outbound.status,
        )
    if stale:
        session.commit()


def process_retries() -> int:
    """Re-send any outbound faxes whose backoff has elapsed."""
    settings = get_settings()
    session_factory = get_sessionmaker()
    resent = 0
    with session_factory() as session:
        _recover_stale_retrying(session, settings)
        due_ids = session.scalars(
            select(OutboundFax.id).where(
                OutboundFax.status == "retry_scheduled",
                OutboundFax.next_retry_at <= datetime.now(UTC),
            )
        ).all()
        for outbound_id in due_ids:
            # Retries are real sends: each one re-checks the breaker and the
            # daily cap (counted via outbound_attempts) before dialing.
            reason = breaker_tripped(session, settings)
            if reason:
                logger.warning("circuit breaker: %s — retries stay scheduled", reason)
                break
            # Atomic claim: the attempt is spent at claim time, so neither a
            # concurrent worker nor a crash can turn one retry into many.
            claimed = session.execute(
                update(OutboundFax)
                .where(OutboundFax.id == outbound_id, OutboundFax.status == "retry_scheduled")
                .values(status="retrying", attempts=OutboundFax.attempts + 1)
            ).rowcount
            session.commit()
            if claimed != 1:
                continue
            outbound = session.get(OutboundFax, outbound_id)
            try:
                new_fax_id = telnyx_client.send_fax(
                    settings, to=outbound.to_number, media_url=outbound.media_url
                )
            except Exception:
                logger.exception("retry send failed for %s", outbound.to_number)
                exhausted = outbound.attempts > len(settings.retry_backoff_seconds)
                outbound.status = "failed_permanent" if exhausted else "retry_scheduled"
                outbound.next_retry_at = (
                    None
                    if exhausted
                    else datetime.now(UTC) + timedelta(seconds=settings.retry_backoff_seconds[0])
                )
                session.commit()
                continue
            logger.info(
                "retry attempt %d for %s: fax %s (was %s)",
                outbound.attempts,
                outbound.to_number,
                new_fax_id,
                outbound.fax_id,
            )
            outbound.fax_id = new_fax_id
            outbound.status = "queued"
            outbound.next_retry_at = None
            session.add(
                OutboundAttempt(
                    outbound_id=outbound.id,
                    telnyx_fax_id=new_fax_id,
                    attempt_no=outbound.attempts,
                )
            )
            session.commit()
            resent += 1
    return resent
