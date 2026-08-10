from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Thread(Base):
    """One correspondence thread. Owns the ref number printed on every reply."""

    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ref_number: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    caller_ani: Mapped[str] = mapped_column(String, index=True)
    # active | opted_out (faxed STOP) | undeliverable (their line failed permanently)
    status: Mapped[str] = mapped_column(String, default="active")
    content_flagged: Mapped[bool] = mapped_column(Boolean, default=False)  # gallery gate
    # Set when the correspondent faxed REMOVE; the pipeline has already
    # withdrawn gallery items and purged PDFs — this is the audit stamp.
    removal_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEvent(Base):
    """Raw log of every webhook delivery (deduped by Telnyx event id)."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    direction: Mapped[str | None] = mapped_column(String, nullable=True)
    fax_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboundFax(Base):
    __tablename__ = "inbound_faxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fax_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id"), nullable=True)
    from_number: Mapped[str] = mapped_column(String, index=True)
    to_number: Mapped[str] = mapped_column(String)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String, nullable=True)  # nulled by retention purge
    # received -> processing -> replied | capped (cap letter sent)
    # failed_download | failed_processing | ignored_cap | ignored_optout
    status: Mapped[str] = mapped_column(String, default="received")
    # Processing attempts so far (incremented at claim time, so a crash mid-run
    # still counts); the stale sweep gives up after max_process_attempts.
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The filed record: what arrived and what we said. Survives PDF purge;
    # feeds thread context for follow-up faxes.
    inbound_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GalleryItem(Base):
    """One published (or pending) exchange: an inbound fax and fax-bot's reply.

    Created only when the correspondent explicitly opts in on the fax itself.
    Nothing publishes without human approval.
    """

    __tablename__ = "gallery_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inbound_fax_id: Mapped[int] = mapped_column(
        ForeignKey("inbound_faxes.id"), unique=True, index=True
    )
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id"), nullable=True)
    slug: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    # pending -> approved | rejected; approved -> removed (REMOVE request
    # or admin unpublish)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    in_pages: Mapped[int] = mapped_column(Integer, default=0)
    tx_pages: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboundFax(Base):
    __tablename__ = "outbound_faxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Latest attempt's Telnyx id; NULL between "row committed" and "send
    # succeeded" (see delivery.send_reply). All ids live in outbound_attempts.
    fax_id: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(ForeignKey("threads.id"), nullable=True)
    to_number: Mapped[str] = mapped_column(String, index=True)
    # Unique: one reply row per inbound fax, enforced by the DB so a racing
    # duplicate insert fails loudly instead of double-sending (send_reply).
    reply_to_fax_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    media_url: Mapped[str] = mapped_column(String)
    # pending_send -> queued -> sending -> delivered
    #   | retry_scheduled -> retrying -> queued -> ... | failed_permanent
    status: Mapped[str] = mapped_column(String, default="queued")
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)  # Telnyx sends performed
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class OutboundAttempt(Base):
    """One actual Telnyx send. The parent row carries only the latest attempt's
    fax id; this table remembers them all, so a late webhook for a superseded
    attempt stays attributable — and the daily cap counts real sends."""

    __tablename__ = "outbound_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outbound_id: Mapped[int] = mapped_column(ForeignKey("outbound_faxes.id"), index=True)
    telnyx_fax_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
