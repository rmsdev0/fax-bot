"""The privacy promises, verified: REMOVE actually removes, moderators can see
what they approve, and unpublished material stops being served."""

import pymupdf
import pytest

ADMIN = {"x-admin-token": "test-admin-token"}  # matches conftest FAKE_ENV


def _real_pdf_stub(settings, ref_number, body, date, out_path):
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), body[:60], fontname="Courier")
    doc.save(out_path)
    doc.close()


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
    from app.worker import render

    sent: list[str] = []

    def fake_send_fax(settings, to, media_url, from_number=None):
        sent.append(to)
        return f"out-{len(sent)}"

    monkeypatch.setattr(telnyx_client, "send_fax", fake_send_fax)
    monkeypatch.setattr(render, "render_reply_pdf", _real_pdf_stub)

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _brain_stub(monkeypatch, **kwargs):
    from app.worker import brain

    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda settings, images, date, history=None: brain.BrainResult(
            kind="reply", body="A FINE LETTER.", inbound_summary="A DRAWING OF A DOG.", **kwargs
        ),
    )


def _run_exchange(fax_id, ani="+15551239999"):
    from app.config import get_settings
    from app.db import get_sessionmaker
    from app.models import InboundFax
    from app.worker import pipeline

    settings = get_settings()
    pdf = settings.inbound_dir / f"{fax_id}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "GALLERY: YES [X]")
    doc.save(pdf)
    doc.close()
    with get_sessionmaker()() as session:
        session.add(
            InboundFax(fax_id=fax_id, from_number=ani, to_number="+15550001234", pdf_path=str(pdf))
        )
        session.commit()
    assert pipeline.process_pending() == 1


def test_moderator_can_view_both_pdfs_before_approving(client, monkeypatch):
    _brain_stub(monkeypatch, gallery_opt_in=True)
    _run_exchange("m1")

    pending = client.get("/admin/gallery", headers=ADMIN).json()["pending"]
    item_id = pending[0]["item_id"]
    assert pending[0]["inbound_pdf"] == f"GET /admin/gallery/{item_id}/inbound.pdf"

    for name in ("inbound.pdf", "reply.pdf"):
        # Auth required...
        assert client.get(f"/admin/gallery/{item_id}/{name}").status_code == 401
        # ...and with it, the actual pages are reviewable.
        response = client.get(f"/admin/gallery/{item_id}/{name}", headers=ADMIN)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")


def test_remove_request_unpublishes_and_purges(client, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.models import GalleryItem, InboundFax, Thread
    from tests.test_reliability import _get_all

    # Exchange 1: opt in, approve, published.
    _brain_stub(monkeypatch, gallery_opt_in=True)
    _run_exchange("r1")
    item_id = client.get("/admin/gallery", headers=ADMIN).json()["pending"][0]["item_id"]
    slug = (
        client.post(f"/admin/gallery/{item_id}/approve", headers=ADMIN)
        .json()["url"]
        .removeprefix("/gallery/")
    )
    assert client.get(f"/gallery-media/{slug}/in_1.png").status_code == 200

    # Exchange 2, same sender: REMOVE.
    _brain_stub(monkeypatch, removal_request=True)
    _run_exchange("r2")

    (item,) = _get_all(GalleryItem)
    assert item.status == "removed"
    # Withdrawn from display and from disk.
    assert client.get(f"/gallery/{slug}").status_code == 404
    assert client.get(f"/gallery-media/{slug}/in_1.png").status_code == 404
    assert not (get_settings().gallery_dir / slug).exists()
    # Stored PDFs purged ahead of schedule.
    faxes = {f.fax_id: f for f in _get_all(InboundFax)}
    assert faxes["r1"].pdf_path is None
    assert faxes["r2"].pdf_path is None
    # "The record" includes the derived content, not just the page images.
    assert faxes["r1"].inbound_summary is None
    assert faxes["r1"].reply_body is None
    assert faxes["r2"].inbound_summary is None
    assert faxes["r2"].reply_body is None
    # The request is stamped for the admin view.
    (thread,) = _get_all(Thread)
    assert thread.removal_requested_at is not None
    recent = client.get("/admin/recent", headers=ADMIN).json()
    assert recent["threads"][0]["removal_requested_at"] is not None


def test_admin_unpublish_endpoint_withdraws_published_item(client, monkeypatch):
    from app.models import GalleryItem
    from tests.test_reliability import _get_all

    _brain_stub(monkeypatch, gallery_opt_in=True)
    _run_exchange("u1")
    item_id = client.get("/admin/gallery", headers=ADMIN).json()["pending"][0]["item_id"]
    slug = (
        client.post(f"/admin/gallery/{item_id}/approve", headers=ADMIN)
        .json()["url"]
        .removeprefix("/gallery/")
    )
    assert client.post(f"/admin/gallery/{item_id}/unpublish", headers=ADMIN).json() == {
        "status": "removed"
    }
    assert client.get(f"/gallery-media/{slug}/in_1.png").status_code == 404
    (item,) = _get_all(GalleryItem)
    assert item.status == "removed"


def test_admin_fails_closed_without_token(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("ADMIN_TOKEN", "")
    get_settings.cache_clear()
    # No token configured -> every admin request is refused, header or not.
    assert client.get("/admin/recent").status_code == 401
    assert client.get("/admin/recent", headers={"x-admin-token": ""}).status_code == 401
    assert client.get("/admin/recent", headers={"x-admin-token": "guess"}).status_code == 401
