import pymupdf
import pytest


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
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://faxbot.test")
    monkeypatch.setenv("ADMIN_TOKEN", "sekrit")

    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from app import telnyx_client
    from app.worker import brain, render

    monkeypatch.setattr(telnyx_client, "send_fax", lambda *a, **k: "out-1")
    monkeypatch.setattr(render, "render_reply_pdf", _real_pdf_stub)
    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda settings, images, date, history=None: brain.BrainResult(
            kind="reply",
            body="A FINE LETTER.",
            inbound_summary="A DRAWING OF A DOG.",
            gallery_opt_in=True,
        ),
    )

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _run_exchange(client, fax_id="g1"):
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
            InboundFax(
                fax_id=fax_id,
                from_number="+15551239999",
                to_number="+15550001234",
                pdf_path=str(pdf),
            )
        )
        session.commit()
    assert pipeline.process_pending() == 1


ADMIN = {"x-admin-token": "sekrit"}


def test_admin_requires_token(client):
    assert client.get("/admin/recent").status_code == 401
    assert client.get("/admin/gallery").status_code == 401
    assert client.get("/admin/recent", headers=ADMIN).status_code == 200


def test_opt_in_moderation_and_publication(client):
    _run_exchange(client)

    # Opt-in produced a pending item; nothing is public yet.
    pending = client.get("/admin/gallery", headers=ADMIN).json()["pending"]
    assert len(pending) == 1
    assert pending[0]["summary"] == "A DRAWING OF A DOG."
    assert "AWAITS ITS FIRST CONSENTING CORRESPONDENT" in client.get("/gallery").text

    # Approve -> published with rasterized pages.
    item_id = pending[0]["item_id"]
    approved = client.post(f"/admin/gallery/{item_id}/approve", headers=ADMIN).json()
    slug = approved["url"].removeprefix("/gallery/")

    index = client.get("/gallery")
    assert index.status_code == 200
    assert slug in index.text

    page = client.get(f"/gallery/{slug}")
    assert page.status_code == 200
    assert "RECEIVED FROM CORRESPONDENT" in page.text
    assert f"/gallery-media/{slug}/tx_1.png" in page.text

    img = client.get(f"/gallery-media/{slug}/in_1.png")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"

    # Queue is now empty; double-approve 404s.
    assert client.get("/admin/gallery", headers=ADMIN).json()["pending"] == []
    assert client.post(f"/admin/gallery/{item_id}/approve", headers=ADMIN).status_code == 404


def test_house_sample_seeding_is_labeled_idempotent_and_removable(client, monkeypatch):
    from sqlalchemy import select

    from app import gallery_seeds
    from app.config import get_settings
    from app.db import get_sessionmaker
    from app.models import GalleryItem, OutboundFax

    def render_inbound_stub(_html, out_path):
        doc = pymupdf.open()
        doc.new_page(width=612, height=792).insert_text((72, 100), "GALLERY: YES [X]")
        doc.save(out_path)
        doc.close()

    monkeypatch.setattr(gallery_seeds, "_render_inbound_pdf", render_inbound_stub)

    assert gallery_seeds.seed_gallery_samples(get_settings()) == 2
    assert gallery_seeds.seed_gallery_samples(get_settings()) == 0

    with get_sessionmaker()() as session:
        items = session.scalars(select(GalleryItem).order_by(GalleryItem.id)).all()
        assert len(items) == 2
        assert all(item.status == "approved" and item.is_sample for item in items)
        assert session.scalars(select(OutboundFax)).all() == []
        first_id, first_slug = items[0].id, items[0].slug

    index = client.get("/gallery")
    assert index.text.count("HOUSE SAMPLE") >= 2
    item_page = client.get(f"/gallery/{first_slug}")
    assert "FICTIONAL HOUSE SAMPLE" in item_page.text
    assert "NO PRIVATE CORRESPONDENCE" in item_page.text

    assert client.post(f"/admin/gallery/{first_id}/unpublish", headers=ADMIN).json() == {
        "status": "removed"
    }
    assert gallery_seeds.seed_gallery_samples(get_settings()) == 0
    assert client.get(f"/gallery/{first_slug}").status_code == 404


def test_reject_never_publishes(client):
    _run_exchange(client, fax_id="g2")
    pending = client.get("/admin/gallery", headers=ADMIN).json()["pending"]
    item_id = pending[0]["item_id"]
    assert client.post(f"/admin/gallery/{item_id}/reject", headers=ADMIN).json() == {
        "status": "rejected"
    }
    assert "AWAITS ITS FIRST CONSENTING CORRESPONDENT" in client.get("/gallery").text


def test_flagged_content_never_queues(client, monkeypatch):
    from app.models import GalleryItem
    from app.worker import brain

    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda settings, images, date, history=None: brain.BrainResult(
            kind="denied",
            body="DENIED.",
            inbound_summary="UNPLEASANTNESS.",
            gallery_opt_in=True,
            flag_reason="HOSTILE INTENT",
        ),
    )
    _run_exchange(client, fax_id="g3")

    from sqlalchemy import select

    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        assert session.scalars(select(GalleryItem)).all() == []


def test_privacy_page(client):
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "PRIVACY POLICY" in r.text
    assert "REMOVE" in r.text
    assert "thirty (30)" in r.text
