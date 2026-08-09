import pymupdf
import pytest


def _make_pdf(path, pages: int, text: str = "WHAT SHOULD I MAKE FOR DINNER?"):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), text, fontname="Courier", fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


# ---------- ingest ----------


def test_ingest_rasterizes_and_caps(tmp_path):
    from app.worker import ingest

    pdf = tmp_path / "in.pdf"
    _make_pdf(pdf, pages=3)
    assert ingest.page_count(pdf) == 3
    images = ingest.rasterize(pdf, max_pages=20)
    assert len(images) == 3
    assert all(png.startswith(b"\x89PNG") for png in images)
    assert len(ingest.rasterize(pdf, max_pages=2)) == 2


# ---------- pipeline (real render via Chromium; brain and Telnyx faked) ----------


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://faxbot.test")

    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    from app.db import init_db

    settings = get_settings()
    settings.inbound_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    from app import telnyx_client

    sent: list[dict] = []

    def fake_send_fax(settings, to, media_url, from_number=None):
        sent.append({"to": to, "media_url": media_url})
        return f"out-{len(sent)}"

    monkeypatch.setattr(telnyx_client, "send_fax", fake_send_fax)

    yield {"settings": settings, "sent": sent, "tmp": tmp_path}

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _insert_inbound(pdf_path, fax_id="fax-p1"):
    from app.db import get_sessionmaker
    from app.models import InboundFax

    with get_sessionmaker()() as session:
        session.add(
            InboundFax(
                fax_id=fax_id,
                from_number="+15551234567",
                to_number="+15550001234",
                pdf_path=str(pdf_path),
            )
        )
        session.commit()


def test_pipeline_end_to_end(pipeline_env, monkeypatch):
    from app.worker import brain, pipeline

    pdf = pipeline_env["settings"].inbound_dir / "fax-p1.pdf"
    _make_pdf(pdf, pages=1)
    _insert_inbound(pdf)

    def fake_generate_reply(settings, images, date, history=None):
        assert len(images) == 1
        return brain.BrainResult(
            kind="reply",
            body="RE: YOUR FACSIMILE\n\nMAKE FRIED RICE.",
            inbound_summary="A DINNER INQUIRY.",
        )

    monkeypatch.setattr(brain, "generate_reply", fake_generate_reply)

    assert pipeline.process_pending() == 1

    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import InboundFax, OutboundFax, Thread

    with get_sessionmaker()() as session:
        inbound = session.scalars(select(InboundFax)).one()
        outbound = session.scalars(select(OutboundFax)).one()
        thread = session.scalars(select(Thread)).one()

    assert inbound.status == "replied"
    assert inbound.thread_id == thread.id
    assert thread.ref_number and thread.ref_number.startswith("FB-")
    assert outbound.media_url == "https://faxbot.test/media/reply_fax-p1.pdf"
    assert outbound.reply_to_fax_id == "fax-p1"

    reply_pdf = pipeline_env["settings"].media_dir / "reply_fax-p1.pdf"
    with pymupdf.open(reply_pdf) as doc:
        assert doc.page_count == 2  # cover + one sheet
    # Idempotent: nothing left to process.
    assert pipeline.process_pending() == 0


def test_pipeline_oversize_skips_brain(pipeline_env, monkeypatch):
    from app.worker import brain, pipeline

    pdf = pipeline_env["settings"].inbound_dir / "fax-big.pdf"
    _make_pdf(pdf, pages=21, text="PAGE OF MANY")
    _insert_inbound(pdf, fax_id="fax-big")

    def explode(*args, **kwargs):
        raise AssertionError("brain must not run for oversize faxes")

    monkeypatch.setattr(brain, "generate_reply", explode)

    assert pipeline.process_pending() == 1
    assert pipeline_env["sent"][0]["media_url"].endswith("reply_fax-big.pdf")

    from sqlalchemy import select

    from app.db import get_sessionmaker
    from app.models import InboundFax

    with get_sessionmaker()() as session:
        inbound = session.scalars(select(InboundFax)).one()
    assert inbound.status == "replied"
    assert inbound.page_count == 21
