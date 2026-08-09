"""Abuse caps, the daily spend circuit breaker, and failure classification."""

import logging
from datetime import UTC, datetime, time

from sqlalchemy import func, select

from app.config import Settings
from app.models import InboundFax, OutboundAttempt, Thread

logger = logging.getLogger("faxbot.policy")

# failure_reason substrings that mean "this destination will never receive a fax"
PERMANENT_FAILURE_MARKERS = (
    "voice",  # voice line / human answered
    "not_fax",
    "invalid",  # invalid/unallocated destination
    "unallocated",
    "disconnected",
    "rejected",
)


def classify_failure(failure_reason: str | None) -> str:
    """'permanent' (never retry) or 'transient' (busy, no answer, timeout...)."""
    reason = (failure_reason or "").lower()
    if any(marker in reason for marker in PERMANENT_FAILURE_MARKERS):
        return "permanent"
    return "transient"


def _start_of_today() -> datetime:
    return datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)


def ani_exchanges_today(session, ani: str) -> int:
    return (
        session.scalar(
            select(func.count(InboundFax.id)).where(
                InboundFax.from_number == ani,
                InboundFax.received_at >= _start_of_today(),
                InboundFax.status.notin_(["failed_download", "failed_processing"]),
            )
        )
        or 0
    )


def ani_opted_out(session, ani: str) -> bool:
    return bool(
        session.scalar(
            select(Thread.id).where(Thread.caller_ani == ani, Thread.status == "opted_out").limit(1)
        )
    )


def llm_spend_today_usd(session, settings: Settings) -> float:
    row = session.execute(
        select(
            func.coalesce(func.sum(InboundFax.input_tokens), 0),
            func.coalesce(func.sum(InboundFax.output_tokens), 0),
        ).where(InboundFax.received_at >= _start_of_today())
    ).one()
    return (
        row[0] * settings.llm_input_price_per_mtok + row[1] * settings.llm_output_price_per_mtok
    ) / 1_000_000


def outbound_faxes_today(session) -> int:
    # Counts actual Telnyx sends — first attempts and retries alike — so
    # retrying cannot slip past the daily cap.
    return (
        session.scalar(
            select(func.count(OutboundAttempt.id)).where(
                OutboundAttempt.created_at >= _start_of_today()
            )
        )
        or 0
    )


def breaker_tripped(session, settings: Settings) -> str | None:
    """Returns a reason string if processing should pause, else None.

    Faxes stay queued (status='received') and are processed when the day rolls
    over or the caps are raised. Slowness is the brand; insolvency is not.
    """
    spend = llm_spend_today_usd(session, settings)
    if spend >= settings.daily_llm_budget_usd:
        return f"daily LLM budget reached (${spend:.2f} >= ${settings.daily_llm_budget_usd:.2f})"
    outbound = outbound_faxes_today(session)
    if outbound >= settings.daily_outbound_fax_cap:
        return f"daily outbound fax cap reached ({outbound} >= {settings.daily_outbound_fax_cap})"
    return None


def cap_letter(date: str, cap: int) -> str:
    return (
        f"RE: YOUR FACSIMILE OF {date}\n\n"
        "DEAR ENTHUSIASTIC CORRESPONDENT,\n\n"
        "IT IS WITH GENUINE REGRET THAT THE DEPARTMENT MUST INFORM YOU\n"
        f"THAT YOU HAVE EXCEEDED YOUR ALLOTTED WHIMSY FOR TODAY\n"
        f"({cap} EXCHANGES).\n\n"
        "YOUR CORRESPONDENCE HAS BEEN AMONG THE FINEST THE DEPARTMENT\n"
        "HAS PROCESSED THIS BUSINESS DAY. THAT IS PRECISELY WHY IT MUST\n"
        "STOP. MODERATION IS THE CORNERSTONE OF SUSTAINABLE WHIMSY.\n\n"
        "YOUR ALLOTMENT RENEWS AT MIDNIGHT, COORDINATED UNIVERSAL TIME.\n"
        "THE DEPARTMENT DOES NOT MAKE THE RULES. THE DEPARTMENT IS THE\n"
        "RULES.\n\n"
        "FURTHER TRANSMISSIONS TODAY WILL BE FILED, CHERISHED, AND NOT\n"
        "ANSWERED.\n\n"
        "RESPECTFULLY TRANSMITTED AT 9,600 BAUD,\n\n"
        "FAX-BOT\n"
        "AUTOMATED CORRESPONDENCE DIVISION"
    )
