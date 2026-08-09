"""Worker pipeline: pending inbound fax -> policy checks -> thread -> brain -> render -> send."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update

from app.config import get_settings
from app.db import get_sessionmaker
from app.models import InboundFax, Thread
from app.worker import brain, delivery, ingest, policy, render

logger = logging.getLogger("faxbot.pipeline")


def _faxbot_date() -> str:
    return datetime.now(UTC).strftime("%d %B %Y").upper()


def _claim_next(session) -> InboundFax | None:
    """Atomically claim the oldest 'received' fax, or None.

    The conditional UPDATE (rowcount-checked) behaves identically on Postgres
    and SQLite, so tests exercise the same claim semantics as production.
    """
    candidate_id = session.scalar(
        select(InboundFax.id)
        .where(InboundFax.status == "received")
        .order_by(InboundFax.id)
        .limit(1)
    )
    if candidate_id is None:
        return None
    claimed = session.execute(
        update(InboundFax)
        .where(InboundFax.id == candidate_id, InboundFax.status == "received")
        .values(
            status="processing",
            claimed_at=datetime.now(UTC),
            attempts=InboundFax.attempts + 1,
        )
    ).rowcount
    session.commit()
    if claimed != 1:
        return None  # another worker won the race
    return session.get(InboundFax, candidate_id)


def requeue_stale(session, settings) -> None:
    """Recover faxes stuck in 'processing' after a crash or a failed attempt.

    The stale window doubles as retry backoff: a deterministically failing fax
    is retried at most every stale_processing_seconds, never in a hot loop.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.stale_processing_seconds)
    stale = session.scalars(
        select(InboundFax).where(InboundFax.status == "processing", InboundFax.claimed_at < cutoff)
    ).all()
    for fax in stale:
        if fax.attempts >= settings.max_process_attempts:
            fax.status = "failed_processing"
            logger.warning("giving up on fax %s after %d attempts", fax.fax_id, fax.attempts)
        else:
            fax.status = "received"
            fax.claimed_at = None
            logger.info("requeueing stale fax %s (attempts so far: %d)", fax.fax_id, fax.attempts)
    if stale:
        session.commit()


def process_pending() -> int:
    """Process all inbound faxes awaiting a reply. Returns number processed."""
    settings = get_settings()
    session_factory = get_sessionmaker()
    processed = 0
    with session_factory() as session:
        requeue_stale(session, settings)
    while True:
        with session_factory() as session:
            reason = policy.breaker_tripped(session, settings)
            if reason:
                logger.warning("circuit breaker: %s — faxes stay queued", reason)
                return processed
            inbound = _claim_next(session)
            if inbound is None:
                return processed
            fax_id, attempt = inbound.fax_id, inbound.attempts
            try:
                _process_one(session, inbound)
                processed += 1
            except Exception:
                logger.exception("pipeline failed for fax %s (attempt %d)", fax_id, attempt)
                session.rollback()
                # Leave the row in 'processing': the stale sweep requeues it
                # (or gives up once attempts reach the cap). Move on so one
                # broken fax can't block the queue.


def _find_thread_by_ani(session, ani: str, window_days: int) -> Thread | None:
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    return session.scalars(
        select(Thread)
        .where(Thread.caller_ani == ani, Thread.last_activity_at >= cutoff)
        .order_by(Thread.last_activity_at.desc())
        .limit(1)
    ).first()


def _find_thread_by_ref(session, ref: str, ani: str) -> Thread | None:
    # Refs are printed on cover sheets and shown on public gallery pages, so
    # they are not secrets. Binding the match to the sender's number keeps a
    # stranger who quotes a ref from joining (or opting out, or publishing
    # under) someone else's thread.
    return session.scalars(
        select(Thread).where(Thread.ref_number == ref, Thread.caller_ani == ani)
    ).first()


def _thread_history(session, thread: Thread, limit: int) -> list[dict]:
    exchanges = session.scalars(
        select(InboundFax)
        .where(
            InboundFax.thread_id == thread.id,
            InboundFax.inbound_summary.is_not(None),
        )
        .order_by(InboundFax.id.desc())
        .limit(limit)
    ).all()
    history = []
    for fax in reversed(exchanges):
        reply = fax.reply_body or "(NO REPLY ON FILE)"
        history.append(
            {
                "date": fax.received_at.strftime("%d %B %Y").upper(),
                "inbound_summary": fax.inbound_summary,
                "reply_excerpt": reply[:600],
            }
        )
    return history


def _ensure_ref(session, thread: Thread) -> None:
    if not thread.ref_number:
        session.flush()
        thread.ref_number = f"FB-{datetime.now(UTC).year}-{thread.id:06d}"


def _apply_removal(session, settings, thread: Thread) -> None:
    """Honor a REMOVE request: withdraw gallery items, purge stored PDFs,
    and stamp the thread so /admin/recent shows the request."""
    from app import gallery as gallery_mod
    from app.models import GalleryItem
    from app.worker import retention

    thread.removal_requested_at = datetime.now(UTC)
    items = session.scalars(
        select(GalleryItem).where(
            GalleryItem.thread_id == thread.id,
            GalleryItem.status.in_(["pending", "approved"]),
        )
    ).all()
    for item in items:
        gallery_mod.unpublish(session, settings, item)
    purged = retention.purge_thread(session, settings, thread.id)
    logger.info(
        "removal request on thread %s: %d gallery item(s) withdrawn, %d file(s) purged",
        thread.ref_number,
        len(items),
        purged,
    )


def _process_one(session, inbound: InboundFax) -> None:
    settings = get_settings()
    date = _faxbot_date()
    ani = inbound.from_number

    # --- policy gates, cheapest first ---
    if policy.ani_opted_out(session, ani):
        inbound.status = "ignored_optout"
        session.commit()
        logger.info("fax %s from opted-out %s: filed, not answered", inbound.fax_id, ani)
        return

    exchanges_today = policy.ani_exchanges_today(session, ani)  # includes this fax
    if exchanges_today > settings.per_ani_daily_cap + 1:
        inbound.status = "ignored_cap"
        session.commit()
        logger.info(
            "fax %s from %s beyond cap: filed, cherished, not answered", inbound.fax_id, ani
        )
        return

    # --- provisional thread by ANI (may be corrected by an extracted ref) ---
    thread = _find_thread_by_ani(session, ani, settings.ani_thread_window_days)

    over_cap = exchanges_today == settings.per_ani_daily_cap + 1
    pdf_path = Path(inbound.pdf_path) if inbound.pdf_path else None

    if over_cap:
        result = brain.BrainResult(
            kind="capped", body=policy.cap_letter(date, settings.per_ani_daily_cap)
        )
    elif pdf_path is None or not pdf_path.exists():
        result = brain.BrainResult(
            kind="denied",
            body=brain.denial_letter(date, "PAGES LOST IN TRANSIT (FILED UNDER MYSTERIES)"),
        )
    else:
        pages = ingest.page_count(pdf_path)
        inbound.page_count = pages
        if pages > settings.page_cap:
            result = brain.BrainResult(kind="oversize", body=brain.oversize_letter(date, pages))
        else:
            history = (
                _thread_history(session, thread, settings.thread_context_exchanges)
                if thread
                else []
            )
            images = ingest.rasterize(pdf_path, settings.page_cap)
            result = brain.generate_reply(settings, images, date, history=history)

    # Commit token usage the moment it exists: a render/send failure later in
    # the pipeline must not erase spend that already happened — the budget
    # breaker sums these columns. += so retried attempts accumulate honestly.
    if result.input_tokens or result.output_tokens:
        inbound.input_tokens += result.input_tokens
        inbound.output_tokens += result.output_tokens
        session.commit()

    # --- thread correction/creation ---
    if result.ref_number:
        by_ref = _find_thread_by_ref(session, result.ref_number, ani)
        if by_ref is not None:
            thread = by_ref
    if thread is None:
        thread = Thread(caller_ani=ani)
        session.add(thread)
    _ensure_ref(session, thread)

    inbound.thread_id = thread.id
    inbound.inbound_summary = result.inbound_summary
    inbound.reply_body = result.body
    thread.message_count += 1
    thread.last_activity_at = datetime.now(UTC)
    if result.kind == "denied":
        thread.content_flagged = True
    if result.stop_request:
        thread.status = "opted_out"
        logger.info("thread %s opted out via STOP; sending farewell", thread.ref_number)

    logger.info(
        "brain result for %s: kind=%s thread=%s ref_in=%s tokens=%d/%d",
        inbound.fax_id,
        result.kind,
        thread.ref_number,
        result.ref_number,
        result.input_tokens,
        result.output_tokens,
    )

    reply_name = f"reply_{inbound.fax_id}.pdf"
    render.render_reply_pdf(
        settings,
        ref_number=thread.ref_number,
        body=result.body,
        date=date,
        out_path=settings.media_dir / reply_name,
    )

    media_url = f"{settings.public_base_url}/media/{reply_name}"
    outbound = delivery.send_reply(
        session,
        settings,
        to=ani,
        media_url=media_url,
        thread_id=thread.id,
        reply_to_fax_id=inbound.fax_id,
    )
    inbound.status = "capped" if result.kind == "capped" else "replied"

    # REMOVE: the acknowledgment letter is already on its way; now actually
    # honor the promise — withdraw published items and purge stored PDFs.
    # Thread resolution is ANI-bound, so this only ever touches the sender's
    # own correspondence.
    if result.removal_request:
        _apply_removal(session, settings, thread)

    # Gallery: explicit opt-in only, clean replies only, and even then a human
    # must approve before anything is published. (Never alongside a REMOVE.)
    if (
        result.gallery_opt_in
        and result.kind == "reply"
        and not thread.content_flagged
        and not result.removal_request
    ):
        from app.models import GalleryItem

        session.add(GalleryItem(inbound_fax_id=inbound.id, thread_id=thread.id))
        logger.info("gallery opt-in detected on fax %s; queued for moderation", inbound.fax_id)

    session.commit()
    logger.info("reply %s (%s) queued to %s", outbound.fax_id, result.kind, ani)
