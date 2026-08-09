"""Render fax-bot's letter: Jinja2 HTML -> headless Chromium PDF -> fax-grit filter."""

import io
import random
import textwrap
from pathlib import Path

import pymupdf
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from app.config import Settings

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
LETTER = (612, 792)
LINES_PER_SHEET = 30
WRAP_COLUMNS = 68
GRIT_DPI = 200

_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


def _paginate(body: str) -> list[str]:
    lines: list[str] = []
    for raw_line in body.splitlines():
        if raw_line.strip():
            lines.extend(textwrap.wrap(raw_line, WRAP_COLUMNS) or [""])
        else:
            lines.append("")
    sheets = [
        "\n".join(lines[i : i + LINES_PER_SHEET]) for i in range(0, len(lines), LINES_PER_SHEET)
    ]
    return sheets or [""]


def _htmls(settings: Settings, ref_number: str, body: str, date: str) -> list[str]:
    sheets = _paginate(body)
    context = {
        "ref_number": ref_number,
        "date": date,
        "fax_number": settings.fax_bot_number,
        "page_count": len(sheets) + 1,
    }
    pages = [_env.get_template("cover.html").render(**context)]
    for i, sheet in enumerate(sheets, start=1):
        pages.append(
            _env.get_template("reply.html").render(
                **context, body=sheet, sheet=i, sheets=len(sheets)
            )
        )
    return pages


def _html_pages_to_pdf(htmls: list[str]) -> pymupdf.Document:
    from playwright.sync_api import sync_playwright

    merged = pymupdf.open()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for html in htmls:
            page.set_content(html)
            pdf_bytes = page.pdf(format="Letter", print_background=True)
            with pymupdf.open("pdf", pdf_bytes) as src:
                merged.insert_pdf(src)
        browser.close()
    return merged


def _apply_grit(doc: pymupdf.Document) -> pymupdf.Document:
    """Make a crisp Chromium PDF look like it survived a phone line."""
    out = pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=GRIT_DPI, colorspace=pymupdf.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        img = img.rotate(random.uniform(-0.5, 0.5), resample=Image.BICUBIC, fillcolor=255)
        noise = Image.effect_noise(img.size, 60)
        img = Image.blend(img, noise, 0.08)
        img = img.convert("1")  # 1-bit dither: the authentic fax texture
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        new_page = out.new_page(width=LETTER[0], height=LETTER[1])
        new_page.insert_image(pymupdf.Rect(0, 0, LETTER[0], LETTER[1]), stream=buffer.getvalue())
    return out


def render_reply_pdf(
    settings: Settings, ref_number: str, body: str, date: str, out_path: Path
) -> None:
    htmls = _htmls(settings, ref_number, body, date)
    crisp = _html_pages_to_pdf(htmls)
    gritty = _apply_grit(crisp)
    gritty.save(out_path, deflate=True)
    gritty.close()
    crisp.close()
