import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import telnyx_client
from app.config import get_settings
from app.db import get_sessionmaker, init_db
from app.models import GalleryItem, InboundFax, OutboundAttempt, OutboundFax, Thread, WebhookEvent

logger = logging.getLogger("faxbot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.pdf$")
SLUG_RE = re.compile(r"^[a-z0-9-]+$")
GALLERY_IMG_RE = re.compile(r"^(in|tx)_\d{1,3}\.png$")
# Telnyx fax ids are UUIDs; anything else never reaches a filesystem path.
FAX_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_templates = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def _pretty_fax_number(e164: str) -> str:
    digits = e164.lstrip("+")
    if len(digits) == 11 and digits.startswith("1"):
        return f"1-{digits[1:4]}-{digits[4:7]}-{digits[7:]}"
    return e164


def _exchange_no(session, inbound: InboundFax) -> int:
    from sqlalchemy import func

    return (
        session.scalar(
            select(func.count(InboundFax.id)).where(
                InboundFax.thread_id == inbound.thread_id, InboundFax.id <= inbound.id
            )
        )
        or 1
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.pdf import ensure_media_files

    settings = get_settings()
    if settings.sentry_dsn.get_secret_value():
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn.get_secret_value(), traces_sample_rate=0)
    settings.inbound_dir.mkdir(parents=True, exist_ok=True)
    settings.gallery_dir.mkdir(parents=True, exist_ok=True)
    ensure_media_files(settings.media_dir)
    init_db()
    if settings.gallery_seed_samples:
        from app.gallery_seeds import seed_gallery_samples

        seed_gallery_samples(settings)
    if settings.webhook_verify and not settings.telnyx_public_key.get_secret_value():
        logger.warning(
            "TELNYX_PUBLIC_KEY is empty — all webhooks will be rejected. "
            "Copy it from portal Keys & Credentials, or set WEBHOOK_VERIFY=false for local-only runs."
        )
    if not settings.admin_token.get_secret_value():
        logger.warning("ADMIN_TOKEN is empty — all /admin/* routes are disabled until it is set.")
    yield


app = FastAPI(title="fax-bot", lifespan=lifespan)


def require_admin(request: Request) -> None:
    # Fail closed: an unset ADMIN_TOKEN disables /admin/* entirely.
    token = get_settings().admin_token.get_secret_value()
    if not token or request.headers.get("x-admin-token") != token:
        raise HTTPException(status_code=401, detail="admin token required")


@app.get("/")
def root():
    """fax-bot.com lands on the gallery."""
    return RedirectResponse("/gallery", status_code=302)


@app.get("/health")
def health():
    return {"status": "ok", "motto": "serving you eventually since 2026"}


@app.get("/media/{name}")
def serve_media(name: str):
    """Public media endpoint — Telnyx fetches outbound fax PDFs from here."""
    settings = get_settings()
    if not MEDIA_NAME_RE.match(name):
        raise HTTPException(status_code=404)
    path = (settings.media_dir / name).resolve()
    if not path.is_file() or settings.media_dir.resolve() not in path.parents:
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="application/pdf")


PRIVACY_REVISED = "10 AUGUST 2026"  # bump when the policy text changes


@app.get("/privacy", response_class=HTMLResponse)
def privacy():
    settings = get_settings()
    html = _templates.get_template("privacy.html").render(
        revised=PRIVACY_REVISED,
        fax_number_pretty=_pretty_fax_number(settings.fax_bot_number),
    )
    return HTMLResponse(html)


@app.get("/gallery", response_class=HTMLResponse)
def gallery_index():
    settings = get_settings()
    session_factory = get_sessionmaker()
    with session_factory() as session:
        rows = session.execute(
            select(GalleryItem, InboundFax, Thread)
            .join(InboundFax, GalleryItem.inbound_fax_id == InboundFax.id)
            .outerjoin(Thread, GalleryItem.thread_id == Thread.id)
            .where(GalleryItem.status == "approved")
            .order_by(GalleryItem.published_at.desc())
        ).all()
        items = []
        for item, inbound, thread in rows:
            items.append(
                {
                    "slug": item.slug,
                    "ref": thread.ref_number if thread else "UNFILED",
                    "exchange_no": _exchange_no(session, inbound),
                    "teaser": (inbound.inbound_summary or "CONTENTS ON FILE.")[:160],
                    "is_sample": item.is_sample,
                    "published": item.published_at.strftime("%d %B %Y").upper()
                    if item.published_at
                    else "",
                }
            )
    html = _templates.get_template("gallery_index.html").render(
        items=items, fax_number_pretty=_pretty_fax_number(settings.fax_bot_number)
    )
    return HTMLResponse(html)


@app.get("/gallery/{slug}", response_class=HTMLResponse)
def gallery_item(slug: str):
    settings = get_settings()
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=404)
    session_factory = get_sessionmaker()
    with session_factory() as session:
        item = session.scalar(
            select(GalleryItem).where(GalleryItem.slug == slug, GalleryItem.status == "approved")
        )
        if item is None:
            raise HTTPException(status_code=404)
        inbound = session.get(InboundFax, item.inbound_fax_id)
        thread = session.get(Thread, item.thread_id) if item.thread_id else None
        exchange_no = _exchange_no(session, inbound)
    html = _templates.get_template("gallery_item.html").render(
        slug=slug,
        ref=thread.ref_number if thread else "UNFILED",
        exchange_no=exchange_no,
        in_pages=item.in_pages,
        tx_pages=item.tx_pages,
        is_sample=item.is_sample,
        base_url=settings.public_base_url,
        fax_number_pretty=_pretty_fax_number(settings.fax_bot_number),
    )
    return HTMLResponse(html)


@app.get("/gallery-media/{slug}/{name}")
def gallery_media(slug: str, name: str):
    settings = get_settings()
    if not SLUG_RE.match(slug) or not GALLERY_IMG_RE.match(name):
        raise HTTPException(status_code=404)
    # The DB status is the source of truth: an unpublished/removed item's
    # images stop being served even if files linger on disk.
    session_factory = get_sessionmaker()
    with session_factory() as session:
        item = session.scalar(
            select(GalleryItem).where(GalleryItem.slug == slug, GalleryItem.status == "approved")
        )
    if item is None:
        raise HTTPException(status_code=404)
    path = (settings.gallery_dir / slug / name).resolve()
    if not path.is_file() or settings.gallery_dir.resolve() not in path.parents:
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/png")


@app.get("/admin/gallery", dependencies=[Depends(require_admin)])
def admin_gallery():
    """Moderation queue: pending opt-ins with the material to review."""
    session_factory = get_sessionmaker()
    with session_factory() as session:
        rows = session.execute(
            select(GalleryItem, InboundFax, Thread)
            .join(InboundFax, GalleryItem.inbound_fax_id == InboundFax.id)
            .outerjoin(Thread, GalleryItem.thread_id == Thread.id)
            .where(GalleryItem.status == "pending")
            .order_by(GalleryItem.id)
        ).all()
        return {
            "pending": [
                {
                    "item_id": item.id,
                    "ref": thread.ref_number if thread else None,
                    "from": inbound.from_number,
                    "summary": inbound.inbound_summary,
                    "reply_body": inbound.reply_body,
                    # Review the actual pages before approving:
                    "inbound_pdf": f"GET /admin/gallery/{item.id}/inbound.pdf",
                    "reply_pdf": f"GET /admin/gallery/{item.id}/reply.pdf",
                    "approve": f"POST /admin/gallery/{item.id}/approve",
                    "reject": f"POST /admin/gallery/{item.id}/reject",
                }
                for item, inbound, thread in rows
            ]
        }


@app.get("/admin/gallery/{item_id}/inbound.pdf", dependencies=[Depends(require_admin)])
def admin_gallery_inbound_pdf(item_id: int):
    """The original fax, so the moderator can verify the consent box and check
    for sensitive content before anything goes public."""
    session_factory = get_sessionmaker()
    with session_factory() as session:
        item = session.get(GalleryItem, item_id)
        inbound = session.get(InboundFax, item.inbound_fax_id) if item else None
    if inbound is None or not inbound.pdf_path:
        raise HTTPException(status_code=404, detail="original not on file")
    path = Path(inbound.pdf_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="original purged")
    return FileResponse(path, media_type="application/pdf")


@app.get("/admin/gallery/{item_id}/reply.pdf", dependencies=[Depends(require_admin)])
def admin_gallery_reply_pdf(item_id: int):
    settings = get_settings()
    session_factory = get_sessionmaker()
    with session_factory() as session:
        item = session.get(GalleryItem, item_id)
        inbound = session.get(InboundFax, item.inbound_fax_id) if item else None
    if inbound is None:
        raise HTTPException(status_code=404)
    path = settings.media_dir / f"reply_{inbound.fax_id}.pdf"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="reply purged")
    return FileResponse(path, media_type="application/pdf")


@app.post("/admin/gallery/{item_id}/approve", dependencies=[Depends(require_admin)])
def admin_gallery_approve(item_id: int):
    from app import gallery as gallery_mod

    settings = get_settings()
    session_factory = get_sessionmaker()
    with session_factory() as session:
        item = session.get(GalleryItem, item_id)
        if item is None or item.status != "pending":
            raise HTTPException(status_code=404, detail="no such pending item")
        item = gallery_mod.approve(session, settings, item)
        return {"status": "approved", "url": f"/gallery/{item.slug}"}


@app.post("/admin/gallery/{item_id}/reject", dependencies=[Depends(require_admin)])
def admin_gallery_reject(item_id: int):
    from app import gallery as gallery_mod

    session_factory = get_sessionmaker()
    with session_factory() as session:
        item = session.get(GalleryItem, item_id)
        if item is None or item.status != "pending":
            raise HTTPException(status_code=404, detail="no such pending item")
        gallery_mod.reject(session, item)
        return {"status": "rejected"}


@app.post("/admin/gallery/{item_id}/unpublish", dependencies=[Depends(require_admin)])
def admin_gallery_unpublish(item_id: int):
    """Withdraw an already-published item (moderator judgment or a removal
    request that arrived outside the fax channel)."""
    from app import gallery as gallery_mod

    settings = get_settings()
    session_factory = get_sessionmaker()
    with session_factory() as session:
        item = session.get(GalleryItem, item_id)
        if item is None or item.status != "approved":
            raise HTTPException(status_code=404, detail="no such published item")
        gallery_mod.unpublish(session, settings, item)
        return {"status": "removed"}


@app.get("/admin/recent", dependencies=[Depends(require_admin)])
def admin_recent():
    """Quick ops view: latest events, faxes, threads, and today's spend."""
    from app.worker import policy

    settings = get_settings()
    session_factory = get_sessionmaker()
    with session_factory() as session:
        events = session.scalars(
            select(WebhookEvent).order_by(WebhookEvent.id.desc()).limit(20)
        ).all()
        inbound = session.scalars(select(InboundFax).order_by(InboundFax.id.desc()).limit(10)).all()
        outbound = session.scalars(
            select(OutboundFax).order_by(OutboundFax.id.desc()).limit(10)
        ).all()
        threads = session.scalars(select(Thread).order_by(Thread.id.desc()).limit(10)).all()
        today = {
            "llm_spend_usd": round(policy.llm_spend_today_usd(session, settings), 4),
            "llm_budget_usd": settings.daily_llm_budget_usd,
            "outbound_faxes": policy.outbound_faxes_today(session),
            "outbound_cap": settings.daily_outbound_fax_cap,
            "breaker": policy.breaker_tripped(session, settings),
        }
    return {
        "today": today,
        "threads": [
            {
                "ref": t.ref_number,
                "ani": t.caller_ani,
                "status": t.status,
                "messages": t.message_count,
                "flagged": t.content_flagged,
                "removal_requested_at": t.removal_requested_at.isoformat()
                if t.removal_requested_at
                else None,
            }
            for t in threads
        ],
        "events": [
            {"event_type": e.event_type, "fax_id": e.fax_id, "at": e.received_at.isoformat()}
            for e in events
        ],
        "inbound": [
            {
                "fax_id": f.fax_id,
                "from": f.from_number,
                "status": f.status,
                "pages": f.page_count,
                "thread_id": f.thread_id,
            }
            for f in inbound
        ],
        "outbound": [
            {
                "fax_id": f.fax_id,
                "to": f.to_number,
                "status": f.status,
                "failure": f.failure_reason,
                "attempts": f.attempts,
            }
            for f in outbound
        ],
    }


@app.post("/webhooks/telnyx")
async def telnyx_webhook(request: Request):
    settings = get_settings()
    body = await request.body()

    if settings.webhook_verify:
        signature = request.headers.get("telnyx-signature-ed25519", "")
        timestamp = request.headers.get("telnyx-timestamp", "")
        if not telnyx_client.verify_webhook_signature(
            settings.telnyx_public_key.get_secret_value(), signature, timestamp, body
        ):
            raise HTTPException(status_code=400, detail="invalid webhook signature")

    try:
        data = json.loads(body)["data"]
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=400, detail="malformed event")

    event_id = data.get("id", "")
    event_type = data.get("event_type", "unknown")
    payload = data.get("payload", {}) or {}
    fax_id = payload.get("fax_id") or payload.get("id")
    direction = payload.get("direction")

    session_factory = get_sessionmaker()
    with session_factory() as session:
        # Dedupe: Telnyx may redeliver the same event.
        if session.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id)):
            return {"status": "duplicate"}

        logger.info("webhook %s direction=%s fax_id=%s", event_type, direction, fax_id)

        if event_type == "fax.received" and direction == "inbound":
            outcome = _handle_inbound_fax(session, payload, fax_id)
            if outcome == "retry":
                # Transient download failure. The failed_download row is
                # committed for visibility, but no WebhookEvent is recorded,
                # so Telnyx's redelivery passes dedup and retries — the media
                # URL is short-lived and redelivery is the only recovery path.
                raise HTTPException(status_code=503, detail="media download failed; redeliver")
        elif event_type == "fax.failed" and direction == "inbound":
            logger.warning(
                "inbound fax failed: fax_id=%s reason=%s", fax_id, payload.get("failure_reason")
            )
        elif direction == "outbound":
            _update_outbound_status(session, event_type, payload, fax_id)

        # Record the event atomically with whatever the handler staged, so a
        # crash before this point leaves redelivery unblocked.
        session.add(
            WebhookEvent(
                event_id=event_id,
                event_type=event_type,
                direction=direction,
                fax_id=fax_id,
                payload=payload,
            )
        )
        try:
            session.commit()
        except IntegrityError:
            # A concurrent redelivery raced past the dedup check.
            session.rollback()
            return {"status": "duplicate"}

    return {"status": "ok"}


def _handle_inbound_fax(session, payload: dict, fax_id: str) -> str:
    """Download and stage an inbound fax. Returns "ok" or "retry".

    "ok": the caller records the WebhookEvent and acknowledges with 200.
    "retry": transient download failure — the caller answers 503 with the
    event unrecorded, so Telnyx redelivers and the download is retried.
    """
    settings = get_settings()
    from_number = payload.get("from", "")
    to_number = payload.get("to", "")

    if not fax_id or not FAX_ID_RE.match(fax_id):
        logger.warning("ignoring inbound fax with invalid fax_id=%r", fax_id)
        return "ok"

    # Loop guard: the test number receives fax-bot's replies during round-trip
    # tests; never respond to faxes addressed to it or sent by ourselves.
    # (Empty settings must never match — that would swallow every fax.)
    if (settings.test_fax_number and to_number == settings.test_fax_number) or (
        settings.fax_bot_number and from_number == settings.fax_bot_number
    ):
        logger.info("loop guard: ignoring fax to=%s from=%s", to_number, from_number)
        return "ok"

    inbound = session.scalar(select(InboundFax).where(InboundFax.fax_id == fax_id))
    if inbound is not None and inbound.status != "failed_download":
        return "ok"  # already ingested; a prior failed_download is retried below
    if inbound is None:
        inbound = InboundFax(
            fax_id=fax_id,
            from_number=from_number,
            to_number=to_number,
            page_count=payload.get("page_count"),
        )
        session.add(inbound)

    # The one real-time constraint in the whole system: the signed media_url
    # expires minutes after fax.received, so download before doing anything else.
    media_url = payload.get("media_url")
    if not media_url:
        # Nothing to fetch; the worker sends the "pages lost" denial.
        return "ok"
    try:
        content = telnyx_client.download_media(media_url)
    except telnyx_client.MediaValidationError:
        # Permanent: redelivering the same bad blob is pointless.
        logger.exception("rejected media for fax %s", fax_id)
        inbound.status = "failed_download"
        return "ok"
    except Exception:
        logger.exception("failed to download media for fax %s", fax_id)
        inbound.status = "failed_download"
        session.commit()  # visible in /admin/recent, but retryable on redelivery
        return "retry"

    pdf_path = settings.inbound_dir / f"{fax_id}.pdf"
    pdf_path.write_bytes(content)
    inbound.pdf_path = str(pdf_path)
    inbound.status = "received"
    # The worker process (app.worker.run) polls for status='received' and does
    # everything slow; the caller commits this row together with the event.
    logger.info("downloaded inbound fax %s (%d bytes), awaiting worker", fax_id, len(content))
    return "ok"


def _update_outbound_status(session, event_type: str, payload: dict, fax_id: str) -> None:
    outbound = session.scalar(select(OutboundFax).where(OutboundFax.fax_id == fax_id))
    if outbound is None:
        # A late webhook for a superseded retry attempt? The newest attempt is
        # authoritative for failures, but a delivery proves the recipient got
        # the fax — record it and stop retrying.
        attempt = session.scalar(
            select(OutboundAttempt).where(OutboundAttempt.telnyx_fax_id == fax_id)
        )
        if attempt is None:
            logger.warning("outbound webhook for unknown fax_id=%s (%s)", fax_id, event_type)
            return
        if event_type == "fax.delivered":
            parent = session.get(OutboundFax, attempt.outbound_id)
            parent.status = "delivered"
            parent.next_retry_at = None
            session.commit()
        else:
            logger.info("ignoring %s for superseded attempt %s", event_type, fax_id)
        return
    if event_type == "fax.failed":
        # Classify and schedule retry (or give up); may mark thread undeliverable.
        from app.worker.delivery import handle_failure

        handle_failure(session, outbound, payload.get("failure_reason"))
        return
    status_map = {
        "fax.queued": "queued",
        "fax.media.processed": "queued",
        "fax.sending.started": "sending",
        "fax.delivered": "delivered",
    }
    if event_type in status_map:
        outbound.status = status_map[event_type]
        session.commit()
