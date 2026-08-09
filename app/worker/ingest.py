"""Rasterize inbound fax PDFs into page images for the vision model."""

from pathlib import Path

import pymupdf

RASTER_DPI = 150  # plenty for fax content; keeps image tokens reasonable


def page_count(pdf_path: Path) -> int:
    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def rasterize(pdf_path: Path, max_pages: int) -> list[bytes]:
    """PDF -> list of PNG bytes, one per page, capped at max_pages."""
    images: list[bytes] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc.pages(0, min(doc.page_count, max_pages)):
            pix = page.get_pixmap(dpi=RASTER_DPI, colorspace=pymupdf.csGRAY)
            images.append(pix.tobytes("png"))
    return images
