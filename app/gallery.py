"""Gallery publication: rasterize an approved exchange into public page images.

Rasterizing at approval time decouples the gallery from retention — the
originals can purge on schedule while published copies live on.
"""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pymupdf

from app.config import Settings
from app.models import GalleryItem, InboundFax, Thread

logger = logging.getLogger("faxbot.gallery")


def _rasterize_to(pdf_path: Path, out_dir: Path, prefix: str, dpi: int) -> int:
    if not pdf_path.exists():
        return 0
    count = 0
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            pix.save(out_dir / f"{prefix}{i}.png")
            count += 1
    return count


def approve(session, settings: Settings, item: GalleryItem) -> GalleryItem:
    inbound = session.get(InboundFax, item.inbound_fax_id)
    thread = session.get(Thread, item.thread_id) if item.thread_id else None
    ref = (thread.ref_number if thread and thread.ref_number else f"sb-item-{item.id}").lower()
    slug = f"{ref}-x{inbound.id}"

    out_dir = settings.gallery_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    dpi = settings.gallery_raster_dpi
    in_pages = 0
    if inbound.pdf_path:
        in_pages = _rasterize_to(Path(inbound.pdf_path), out_dir, "in_", dpi)
    tx_pages = _rasterize_to(
        settings.media_dir / f"reply_{inbound.fax_id}.pdf", out_dir, "tx_", dpi
    )

    item.slug = slug
    item.in_pages = in_pages
    item.tx_pages = tx_pages
    item.status = "approved"
    item.published_at = datetime.now(UTC)
    session.commit()
    logger.info(
        "gallery item %s published as /gallery/%s (%d in, %d tx pages)",
        item.id,
        slug,
        in_pages,
        tx_pages,
    )
    return item


def reject(session, item: GalleryItem) -> GalleryItem:
    item.status = "rejected"
    session.commit()
    return item


def unpublish(session, settings: Settings, item: GalleryItem) -> GalleryItem:
    """Withdraw an item from display (REMOVE request or admin action).

    Deletes the published raster files as well as flipping the status — the
    media route checks status, but files that shouldn't be public shouldn't
    exist either.
    """
    if item.slug:
        out_dir = settings.gallery_dir / item.slug
        if out_dir.exists():
            shutil.rmtree(out_dir)
    item.status = "removed"
    session.commit()
    logger.info("gallery item %s (%s) withdrawn from display", item.id, item.slug)
    return item
