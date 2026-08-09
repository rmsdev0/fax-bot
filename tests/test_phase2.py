from datetime import UTC, datetime, timedelta

import pymupdf
import pytest


def _setup(tmp_path, monkeypatch, **extra_env):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://faxbot.test")
    for key, value in extra_env.items():
        monkeypatch.setenv(key, str(value))

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

    sent: list[dict] = []

    def fake_send_fax(settings, to, media_url, from_number=None):
        sent.append({"to": to, "media_url": media_url})
        return f"out-{len(sent)}"

    monkeypatch.setattr(telnyx_client, "send_fax", fake_send_fax)
    # Rendering is covered by the Phase 1 tests; stub it here for speed.
    monkeypatch.setattr(
        render,
        "render_reply_pdf",
        lambda settings, ref_number, body, date, out_path: out_path.write_bytes(b"%PDF stub"),
    )
    return settings, sent


@pytest.fixture(autouse=True)
def _clear_caches_after():
    yield
    from app.config import get_settings
    from app.db import get_engine, get_sessionmaker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


def _add_inbound(settings, fax_id, ani="+15551230001", received_at=None, **cols):
    from app.db import get_sessionmaker
    from app.models import InboundFax

    pdf = settings.inbound_dir / f"{fax_id}.pdf"
    doc = pymupdf.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "HELLO", fontname="Courier")
    doc.save(pdf)
    doc.close()
    with get_sessionmaker()() as session:
        fax = InboundFax(
            fax_id=fax_id,
            from_number=ani,
            to_number="+15550001234",
            pdf_path=str(pdf),
            **cols,
        )
        if received_at:
            fax.received_at = received_at
        session.add(fax)
        session.commit()
        return fax.id


def _brain_stub(monkeypatch, **result_kwargs):
    from app.worker import brain

    calls = []

    def fake(settings, images, date, history=None):
        calls.append({"history": history or []})
        return brain.BrainResult(
            kind="reply",
            body="A LETTER.",
            inbound_summary="A THING ARRIVED.",
            input_tokens=1000,
            output_tokens=500,
            **result_kwargs,
        )

    monkeypatch.setattr(brain, "generate_reply", fake)
    return calls


def _get_all(model):
    from sqlalchemy import select

    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        return session.scalars(select(model)).all()


# ---------- threading ----------


def test_thread_resolution_by_ani_and_ref(tmp_path, monkeypatch):
    settings, _sent = _setup(tmp_path, monkeypatch)
    calls = _brain_stub(monkeypatch)

    from app.models import InboundFax, Thread
    from app.worker import brain, pipeline

    _add_inbound(settings, "f1", ani="+15551230001")
    assert pipeline.process_pending() == 1
    threads = _get_all(Thread)
    assert len(threads) == 1
    ref1 = threads[0].ref_number
    assert ref1.startswith("FB-")
    assert calls[0]["history"] == []

    # Same ANI within the window -> same thread, with history supplied.
    _add_inbound(settings, "f2", ani="+15551230001")
    assert pipeline.process_pending() == 1
    assert len(_get_all(Thread)) == 1
    assert len(calls[1]["history"]) == 1
    assert calls[1]["history"][0]["inbound_summary"] == "A THING ARRIVED."

    # A different ANI quoting ref1 must NOT join the thread — refs are printed
    # on cover sheets and public gallery pages, so a guessed or copied ref
    # would otherwise let a stranger read (via history), opt out, or publish
    # under someone else's correspondence. It gets a fresh thread instead.
    def fake_with_ref(settings, images, date, history=None):
        return brain.BrainResult(
            kind="reply",
            body="A LETTER.",
            inbound_summary="FOLLOW-UP.",
            ref_number=ref1,
        )

    monkeypatch.setattr(brain, "generate_reply", fake_with_ref)
    _add_inbound(settings, "f3", ani="+15559990000")
    assert pipeline.process_pending() == 1
    threads = {t.ref_number: t for t in _get_all(Thread)}
    assert len(threads) == 2
    faxes = {f.fax_id: f for f in _get_all(InboundFax)}
    assert faxes["f3"].thread_id != faxes["f1"].thread_id
    assert threads[ref1].message_count == 2

    # The same ANI quoting its own ref still resolves to its thread.
    _add_inbound(settings, "f4", ani="+15551230001")
    assert pipeline.process_pending() == 1
    faxes = {f.fax_id: f for f in _get_all(InboundFax)}
    assert faxes["f4"].thread_id == faxes["f1"].thread_id


# ---------- caps and opt-out ----------


def test_per_ani_daily_cap(tmp_path, monkeypatch):
    settings, sent = _setup(tmp_path, monkeypatch, PER_ANI_DAILY_CAP=2)
    _brain_stub(monkeypatch)

    from app.models import InboundFax
    from app.worker import brain, pipeline

    for i in range(2):
        _add_inbound(settings, f"c{i}", ani="+15550001111")
    assert pipeline.process_pending() == 2

    # Third today: cap letter, brain must not run.
    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("brain ran over cap")),
    )
    _add_inbound(settings, "c2", ani="+15550001111")
    assert pipeline.process_pending() == 1
    faxes = {f.fax_id: f.status for f in _get_all(InboundFax)}
    assert faxes["c2"] == "capped"
    assert len(sent) == 3
    assert "ALLOTTED WHIMSY" in next(f.reply_body for f in _get_all(InboundFax) if f.fax_id == "c2")

    # Fourth: filed, cherished, not answered.
    _add_inbound(settings, "c3", ani="+15550001111")
    assert pipeline.process_pending() == 1
    faxes = {f.fax_id: f.status for f in _get_all(InboundFax)}
    assert faxes["c3"] == "ignored_cap"
    assert len(sent) == 3


def test_stop_request_opts_thread_out(tmp_path, monkeypatch):
    settings, sent = _setup(tmp_path, monkeypatch)
    _brain_stub(monkeypatch, stop_request=True)

    from app.models import InboundFax, Thread
    from app.worker import brain, pipeline

    _add_inbound(settings, "s1", ani="+15557770000")
    assert pipeline.process_pending() == 1
    assert _get_all(Thread)[0].status == "opted_out"
    assert len(sent) == 1  # the farewell letter still goes out

    monkeypatch.setattr(
        brain,
        "generate_reply",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("brain ran for opted-out ANI")),
    )
    _add_inbound(settings, "s2", ani="+15557770000")
    assert pipeline.process_pending() == 1
    faxes = {f.fax_id: f.status for f in _get_all(InboundFax)}
    assert faxes["s2"] == "ignored_optout"
    assert len(sent) == 1


# ---------- delivery retries ----------


def test_retry_scheduling_and_permanent_failure(tmp_path, monkeypatch):
    _settings, _sent = _setup(tmp_path, monkeypatch)

    from app.db import get_sessionmaker
    from app.models import OutboundFax, Thread
    from app.worker import delivery

    with get_sessionmaker()() as session:
        thread = Thread(caller_ani="+15551112222", ref_number="FB-2026-000042")
        session.add(thread)
        session.flush()
        # Models an already-sent fax: one Telnyx send performed.
        outbound = OutboundFax(
            fax_id="o1",
            thread_id=thread.id,
            to_number="+15551112222",
            media_url="https://faxbot.test/media/reply_x.pdf",
            attempts=1,
        )
        session.add(outbound)
        session.commit()

        # Transient failure -> retry scheduled with backoff.
        delivery.handle_failure(session, outbound, "busy")
        assert outbound.status == "retry_scheduled"
        assert outbound.next_retry_at is not None

        # Force the retry due now; it re-sends and increments attempts.
        outbound.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    assert delivery.process_retries() == 1
    with get_sessionmaker()() as session:
        refreshed = session.get(OutboundFax, 1)
        assert refreshed.attempts == 2
        assert refreshed.status == "queued"
        assert refreshed.fax_id == "out-1"  # new Telnyx id from the resend

        # The resend is on the record (and counts toward the daily cap).
        from sqlalchemy import select

        from app.models import OutboundAttempt

        attempts = session.scalars(select(OutboundAttempt)).all()
        assert [(a.telnyx_fax_id, a.attempt_no) for a in attempts] == [("out-1", 2)]

        # Permanent failure -> give up immediately, thread marked undeliverable.
        delivery.handle_failure(session, refreshed, "voice_detected")
        assert refreshed.status == "failed_permanent"
        assert session.get(Thread, refreshed.thread_id).status == "undeliverable"


def test_retries_exhausted(tmp_path, monkeypatch):
    _settings, _sent = _setup(tmp_path, monkeypatch)

    from app.db import get_sessionmaker
    from app.models import OutboundFax
    from app.worker import delivery

    with get_sessionmaker()() as session:
        outbound = OutboundFax(
            fax_id="o2",
            to_number="+15551113333",
            media_url="https://faxbot.test/media/reply_y.pdf",
            attempts=4,  # initial send + 3 retries already spent
        )
        session.add(outbound)
        session.commit()
        delivery.handle_failure(session, outbound, "no_answer")
        assert outbound.status == "failed_permanent"


# ---------- circuit breaker ----------


def test_breaker_pauses_processing(tmp_path, monkeypatch):
    settings, sent = _setup(tmp_path, monkeypatch, DAILY_LLM_BUDGET_USD="0.01")
    _brain_stub(monkeypatch)

    from app.models import InboundFax
    from app.worker import pipeline

    # Prior exchange today already blew the 1-cent budget.
    _add_inbound(settings, "b0", input_tokens=2000, output_tokens=1000, status="replied")
    _add_inbound(settings, "b1")
    assert pipeline.process_pending() == 0
    faxes = {f.fax_id: f.status for f in _get_all(InboundFax)}
    assert faxes["b1"] == "received"  # queued, not lost
    assert sent == []


# ---------- retention ----------


def test_retention_purges_old_pdfs(tmp_path, monkeypatch):
    settings, _sent = _setup(tmp_path, monkeypatch)

    from app.models import InboundFax
    from app.worker import retention

    old = datetime.now(UTC) - timedelta(days=40)
    _add_inbound(settings, "old1", received_at=old, status="replied")
    _add_inbound(settings, "new1", status="replied")

    assert retention.purge_expired() == 1
    faxes = {f.fax_id: f for f in _get_all(InboundFax)}
    assert faxes["old1"].pdf_path is None
    assert not (settings.inbound_dir / "old1.pdf").exists()
    assert faxes["new1"].pdf_path is not None
    assert (settings.inbound_dir / "new1.pdf").exists()
