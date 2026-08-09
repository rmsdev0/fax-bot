"""Reliability behavior: download hardening, webhook loss-proofing, worker recovery."""

from datetime import UTC, datetime, timedelta

import httpx
import pymupdf
import pytest

# ---------- download hardening (unit) ----------


def _stub_stream(monkeypatch, content: bytes, content_type: str = "application/pdf"):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=content, headers={"content-type": content_type})
    )

    def fake_stream(method, url, **kwargs):
        return httpx.Client(transport=transport).stream(method, url)

    monkeypatch.setattr(httpx, "stream", fake_stream)


def test_download_media_accepts_pdf(monkeypatch):
    from app import telnyx_client

    _stub_stream(monkeypatch, b"%PDF-1.4 hello")
    assert telnyx_client.download_media("https://telnyx.test/m") == b"%PDF-1.4 hello"


def test_download_media_rejects_wrong_content_type(monkeypatch):
    from app import telnyx_client

    _stub_stream(monkeypatch, b"%PDF-1.4 hello", content_type="text/html")
    with pytest.raises(telnyx_client.MediaValidationError):
        telnyx_client.download_media("https://telnyx.test/m")


def test_download_media_rejects_non_pdf_magic(monkeypatch):
    from app import telnyx_client

    _stub_stream(monkeypatch, b"GIF89a not a pdf")
    with pytest.raises(telnyx_client.MediaValidationError):
        telnyx_client.download_media("https://telnyx.test/m")


def test_download_media_enforces_size_cap(monkeypatch):
    from app import telnyx_client

    monkeypatch.setattr(telnyx_client, "MAX_MEDIA_BYTES", 64)
    _stub_stream(monkeypatch, b"%PDF-1.4 " + b"x" * 100)
    with pytest.raises(telnyx_client.MediaValidationError):
        telnyx_client.download_media("https://telnyx.test/m")


# ---------- webhook client fixture (sqlite + faked Telnyx) ----------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("WEBHOOK_VERIFY", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from app import telnyx_client

    monkeypatch.setattr(telnyx_client, "download_media", lambda url: b"%PDF-1.4 fake")
    monkeypatch.setattr(telnyx_client, "send_fax", lambda *a, **k: "out-1")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _event(event_id, event_type, payload):
    return {"data": {"id": event_id, "event_type": event_type, "payload": payload}}


def _inbound_event(event_id, fax_id, media_url="https://telnyx.test/media/signed"):
    return _event(
        event_id,
        "fax.received",
        {
            "fax_id": fax_id,
            "direction": "inbound",
            "from": "+15551234567",
            "to": "+15550001234",
            "media_url": media_url,
        },
    )


def _get_all(model):
    from sqlalchemy import select

    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        return session.scalars(select(model)).all()


# ---------- webhook never loses a fax ----------


def test_transient_download_failure_returns_503_and_recovers(client, monkeypatch):
    from app import telnyx_client
    from app.models import InboundFax, WebhookEvent

    def boom(url):
        raise httpx.ConnectError("connection reset")

    monkeypatch.setattr(telnyx_client, "download_media", boom)
    response = client.post("/webhooks/telnyx", json=_inbound_event("evt-r1", "fax-r1"))
    assert response.status_code == 503
    # The failure is visible, but no event row blocks the redelivery.
    assert _get_all(WebhookEvent) == []
    (fax,) = _get_all(InboundFax)
    assert fax.status == "failed_download"

    # Redelivery of the same event retries the download and recovers in place.
    monkeypatch.setattr(telnyx_client, "download_media", lambda url: b"%PDF-1.4 ok")
    response = client.post("/webhooks/telnyx", json=_inbound_event("evt-r1", "fax-r1"))
    assert response.status_code == 200
    (fax,) = _get_all(InboundFax)
    assert fax.status == "received"
    assert fax.pdf_path
    assert [e.event_id for e in _get_all(WebhookEvent)] == ["evt-r1"]

    # A third delivery is a plain duplicate.
    response = client.post("/webhooks/telnyx", json=_inbound_event("evt-r1", "fax-r1"))
    assert response.json() == {"status": "duplicate"}


def test_invalid_media_is_permanent_not_retried(client, monkeypatch):
    from app import telnyx_client
    from app.models import InboundFax, WebhookEvent

    def bad(url):
        raise telnyx_client.MediaValidationError("not a pdf")

    monkeypatch.setattr(telnyx_client, "download_media", bad)
    response = client.post("/webhooks/telnyx", json=_inbound_event("evt-b1", "fax-b1"))
    # 200 + recorded event: redelivering the same bad blob would be pointless.
    assert response.status_code == 200
    assert [e.event_id for e in _get_all(WebhookEvent)] == ["evt-b1"]
    (fax,) = _get_all(InboundFax)
    assert fax.status == "failed_download"


# ---------- fax_id validation ----------


def test_traversal_fax_id_is_ignored(client, tmp_path):
    from app.models import InboundFax

    response = client.post("/webhooks/telnyx", json=_inbound_event("evt-t1", "../../etc/passwd"))
    assert response.status_code == 200
    assert _get_all(InboundFax) == []
    # Nothing was written anywhere under the data dir.
    inbound_dir = tmp_path / "data" / "inbound"
    assert not any(inbound_dir.rglob("*")) if inbound_dir.exists() else True


# ---------- worker claim + crash recovery ----------


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker, init_db

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    settings = get_settings()
    settings.inbound_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    from app import telnyx_client
    from app.worker import render

    sent: list[str] = []

    def fake_send_fax(settings, to, media_url, from_number=None):
        sent.append(to)
        return f"out-{len(sent)}"

    monkeypatch.setattr(telnyx_client, "send_fax", fake_send_fax)
    monkeypatch.setattr(
        render,
        "render_reply_pdf",
        lambda settings, ref_number, body, date, out_path: out_path.write_bytes(b"%PDF stub"),
    )

    yield settings

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _add_fax(settings, fax_id, ani="+15551230001", **cols):
    from app.db import get_sessionmaker
    from app.models import InboundFax

    pdf = settings.inbound_dir / f"{fax_id}.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "HELLO", fontname="Courier")
    doc.save(pdf)
    doc.close()
    with get_sessionmaker()() as session:
        fax = InboundFax(
            fax_id=fax_id, from_number=ani, to_number="+15550001234", pdf_path=str(pdf), **cols
        )
        session.add(fax)
        session.commit()
        return fax.id


def test_claim_is_atomic_and_counted(pipeline_env):
    from app.db import get_sessionmaker
    from app.worker import pipeline

    _add_fax(pipeline_env, "cl1")
    with get_sessionmaker()() as session:
        first = pipeline._claim_next(session)
        assert first is not None
        assert first.status == "processing"
        assert first.attempts == 1
        # Nothing left to claim; a competing claim comes back empty-handed.
        assert pipeline._claim_next(session) is None


def test_stale_sweep_requeues_and_gives_up_at_cap(pipeline_env):
    from app.config import get_settings
    from app.db import get_sessionmaker
    from app.models import InboundFax
    from app.worker import pipeline

    old = datetime.now(UTC) - timedelta(hours=1)
    _add_fax(
        pipeline_env, "st1", ani="+15551110001", status="processing", claimed_at=old, attempts=1
    )
    _add_fax(
        pipeline_env, "st2", ani="+15551110002", status="processing", claimed_at=old, attempts=3
    )
    _add_fax(
        pipeline_env,
        "st3",
        ani="+15551110003",
        status="processing",
        claimed_at=datetime.now(UTC),
        attempts=1,
    )
    with get_sessionmaker()() as session:
        pipeline.requeue_stale(session, get_settings())
    faxes = {f.fax_id: f for f in _get_all(InboundFax)}
    assert faxes["st1"].status == "received"  # stale, attempts left -> requeued
    assert faxes["st2"].status == "failed_processing"  # stale, at cap -> give up
    assert faxes["st3"].status == "processing"  # fresh claim untouched


# ---------- outbound ordering + cost accounting ----------


def test_send_failure_leaves_pending_send_and_reuses_row(pipeline_env, monkeypatch):
    from app import telnyx_client
    from app.db import get_sessionmaker
    from app.models import InboundFax, OutboundAttempt, OutboundFax
    from app.worker import brain, pipeline

    _add_fax(pipeline_env, "os1")
    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda settings, images, date, history=None: brain.BrainResult(
            kind="reply", body="OK.", inbound_summary="S."
        ),
    )
    monkeypatch.setattr(
        telnyx_client,
        "send_fax",
        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("telnyx down")),
    )
    assert pipeline.process_pending() == 0
    # Intent was recorded before the failed API call.
    (outbound,) = _get_all(OutboundFax)
    assert outbound.status == "pending_send"
    assert outbound.fax_id is None
    assert outbound.attempts == 0
    assert _get_all(OutboundAttempt) == []

    # Stale window passes; the retried pipeline reuses the same row.
    with get_sessionmaker()() as session:
        row = session.scalars(session.query(InboundFax).statement).one()
        row.claimed_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    monkeypatch.setattr(telnyx_client, "send_fax", lambda *a, **k: "out-real-1")
    assert pipeline.process_pending() == 1
    (outbound,) = _get_all(OutboundFax)  # still exactly one row
    assert outbound.status == "queued"
    assert outbound.fax_id == "out-real-1"
    assert outbound.attempts == 1
    (attempt,) = _get_all(OutboundAttempt)
    assert attempt.telnyx_fax_id == "out-real-1"


def test_tokens_survive_a_render_failure(pipeline_env, monkeypatch):
    from app.config import get_settings
    from app.db import get_sessionmaker
    from app.models import InboundFax
    from app.worker import brain, pipeline, policy, render

    _add_fax(pipeline_env, "tk1")
    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda settings, images, date, history=None: brain.BrainResult(
            kind="reply",
            body="OK.",
            inbound_summary="S.",
            input_tokens=100_000,
            output_tokens=50_000,
        ),
    )
    monkeypatch.setattr(
        render,
        "render_reply_pdf",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chromium crashed")),
    )
    assert pipeline.process_pending() == 0
    (fax,) = _get_all(InboundFax)
    assert fax.input_tokens == 100_000  # paid-for tokens survived the rollback
    assert fax.output_tokens == 50_000
    with get_sessionmaker()() as session:
        assert policy.llm_spend_today_usd(session, get_settings()) > 0


def test_retries_respect_breaker_and_count_toward_cap(pipeline_env, monkeypatch):
    from app.db import get_sessionmaker
    from app.models import OutboundAttempt, OutboundFax
    from app.worker import delivery

    monkeypatch.setenv("DAILY_OUTBOUND_FAX_CAP", "1")
    from app.config import get_settings

    get_settings.cache_clear()

    with get_sessionmaker()() as session:
        # One send already happened today (fills the cap of 1)...
        sent = OutboundFax(
            fax_id="done-1",
            to_number="+15551119999",
            media_url="https://faxbot.test/media/reply_a.pdf",
            attempts=1,
        )
        session.add(sent)
        session.flush()
        session.add(OutboundAttempt(outbound_id=sent.id, telnyx_fax_id="done-1", attempt_no=1))
        # ...and another fax is due for retry.
        session.add(
            OutboundFax(
                fax_id="due-1",
                to_number="+15551118888",
                media_url="https://faxbot.test/media/reply_b.pdf",
                attempts=1,
                status="retry_scheduled",
                next_retry_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        )
        session.commit()

    assert delivery.process_retries() == 0  # breaker holds the retry back
    faxes = {f.fax_id: f for f in _get_all(OutboundFax)}
    assert faxes["due-1"].status == "retry_scheduled"


def test_late_delivered_webhook_for_superseded_attempt(client):
    from app.db import get_sessionmaker
    from app.models import OutboundAttempt, OutboundFax

    with get_sessionmaker()() as session:
        outbound = OutboundFax(
            fax_id="new-2",
            to_number="+15551117777",
            media_url="https://faxbot.test/media/reply_c.pdf",
            attempts=2,
            status="retry_scheduled",
            next_retry_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(outbound)
        session.flush()
        session.add(OutboundAttempt(outbound_id=outbound.id, telnyx_fax_id="old-1", attempt_no=1))
        session.add(OutboundAttempt(outbound_id=outbound.id, telnyx_fax_id="new-2", attempt_no=2))
        session.commit()

    # The first attempt actually landed: stop retrying, record the delivery.
    response = client.post(
        "/webhooks/telnyx",
        json=_event("evt-late-1", "fax.delivered", {"fax_id": "old-1", "direction": "outbound"}),
    )
    assert response.status_code == 200
    (outbound,) = _get_all(OutboundFax)
    assert outbound.status == "delivered"
    assert outbound.next_retry_at is None

    # A failure of a superseded attempt is not authoritative — ignored.
    response = client.post(
        "/webhooks/telnyx",
        json=_event(
            "evt-late-2",
            "fax.failed",
            {"fax_id": "old-1", "direction": "outbound", "failure_reason": "busy"},
        ),
    )
    assert response.status_code == 200
    (outbound,) = _get_all(OutboundFax)
    assert outbound.status == "delivered"


def test_transient_failure_heals_on_a_later_cycle(pipeline_env, monkeypatch):
    from app.db import get_sessionmaker
    from app.models import InboundFax
    from app.worker import brain, pipeline

    _add_fax(pipeline_env, "tr1")
    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api blip")),
    )
    assert pipeline.process_pending() == 0
    (fax,) = _get_all(InboundFax)
    assert fax.status == "processing"  # left claimed; the sweep owns recovery
    assert fax.attempts == 1

    # Pretend the stale window has passed, then run a cycle with a healthy brain.
    with get_sessionmaker()() as session:
        row = session.get(InboundFax, fax.id)
        row.claimed_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda settings, images, date, history=None: brain.BrainResult(
            kind="reply", body="OK.", inbound_summary="S."
        ),
    )
    assert pipeline.process_pending() == 1
    (fax,) = _get_all(InboundFax)
    assert fax.status == "replied"
    assert fax.attempts == 2
