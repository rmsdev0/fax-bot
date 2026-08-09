import base64
import time

import pymupdf
import pytest
from nacl.signing import SigningKey

from app.telnyx_client import verify_webhook_signature

# ---------- signature verification (pure unit) ----------


def test_signature_verification_roundtrip():
    key = SigningKey.generate()
    public_key_b64 = base64.b64encode(bytes(key.verify_key)).decode()
    body = b'{"data": {"event_type": "fax.received"}}'
    timestamp = str(int(time.time()))
    signature = base64.b64encode(key.sign(f"{timestamp}|".encode() + body).signature).decode()

    assert verify_webhook_signature(public_key_b64, signature, timestamp, body)
    assert not verify_webhook_signature(public_key_b64, signature, timestamp, body + b"x")
    stale = str(int(time.time()) - 3600)
    assert not verify_webhook_signature(public_key_b64, signature, stale, body)
    assert not verify_webhook_signature(public_key_b64, "not-base64!!", timestamp, body)


# ---------- PDF generation ----------


def test_pdf_generation(tmp_path):
    from app.pdf import ensure_media_files

    ensure_media_files(tmp_path)
    for name in ("static_reply.pdf", "test_fax.pdf"):
        doc = pymupdf.open(tmp_path / name)
        assert doc.page_count == 1
        assert "FAX-BOT" in doc[0].get_text() or "ADVISE" in doc[0].get_text()
        doc.close()


# ---------- webhook flow (sqlite + faked Telnyx) ----------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("WEBHOOK_VERIFY", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://faxbot.test")

    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from app import telnyx_client

    sent: list[dict] = []

    def fake_send_fax(settings, to, media_url, from_number=None):
        sent.append({"to": to, "media_url": media_url})
        return f"out-{len(sent)}"

    monkeypatch.setattr(telnyx_client, "download_media", lambda url: b"%PDF-1.4 fake")
    monkeypatch.setattr(telnyx_client, "send_fax", fake_send_fax)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        test_client.sent = sent
        yield test_client

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _event(event_id, event_type, payload):
    return {"data": {"id": event_id, "event_type": event_type, "payload": payload}}


ADMIN = {"x-admin-token": "test-admin-token"}  # matches conftest FAKE_ENV


def test_inbound_fax_recorded_for_worker(client, tmp_path):
    response = client.post(
        "/webhooks/telnyx",
        json=_event(
            "evt-1",
            "fax.received",
            {
                "fax_id": "fax-in-1",
                "direction": "inbound",
                "from": "+15551234567",
                "to": "+15550001234",
                "media_url": "https://telnyx.test/media/signed",
                "page_count": 1,
            },
        ),
    )
    assert response.status_code == 200
    # The handler only downloads and records; the worker sends the reply.
    assert client.sent == []
    assert (tmp_path / "data" / "inbound" / "fax-in-1.pdf").read_bytes() == b"%PDF-1.4 fake"

    recent = client.get("/admin/recent", headers=ADMIN).json()
    assert recent["inbound"][0]["fax_id"] == "fax-in-1"
    assert recent["inbound"][0]["status"] == "received"

    # Redelivery of the same event is deduped.
    response = client.post(
        "/webhooks/telnyx",
        json=_event(
            "evt-1",
            "fax.received",
            {
                "fax_id": "fax-in-1",
                "direction": "inbound",
                "from": "+15551234567",
                "to": "+15550001234",
                "media_url": "https://telnyx.test/media/signed",
            },
        ),
    )
    assert response.json() == {"status": "duplicate"}


def test_outbound_status_updates(client):
    from app.db import get_sessionmaker
    from app.models import OutboundFax

    with get_sessionmaker()() as session:
        session.add(
            OutboundFax(
                fax_id="out-1",
                to_number="+15550000001",
                reply_to_fax_id="fax-in-2",
                media_url="https://faxbot.test/media/reply_fax-in-2.pdf",
            )
        )
        session.commit()

    response = client.post(
        "/webhooks/telnyx",
        json=_event(
            "evt-11",
            "fax.delivered",
            {"fax_id": "out-1", "direction": "outbound", "to": "+15550000001"},
        ),
    )
    assert response.status_code == 200
    recent = client.get("/admin/recent", headers=ADMIN).json()
    assert recent["outbound"][0]["status"] == "delivered"


def test_loop_guard_ignores_test_number(client):
    response = client.post(
        "/webhooks/telnyx",
        json=_event(
            "evt-20",
            "fax.received",
            {
                "fax_id": "fax-in-3",
                "direction": "inbound",
                "from": "+15550001234",
                "to": "+15550005678",
                "media_url": "https://telnyx.test/m2",
            },
        ),
    )
    assert response.status_code == 200
    assert client.sent == []
    recent = client.get("/admin/recent", headers=ADMIN).json()
    assert recent["inbound"] == []
