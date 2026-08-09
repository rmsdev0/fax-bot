"""Phase 0 PDF generation: the static reply and a test fax, rendered with PyMuPDF."""

from pathlib import Path

import pymupdf

LETTER = (612, 792)  # US letter, points
MARGIN = 72

STATIC_REPLY = """\
FAX-BOT INDUSTRIES
"SERVING YOU EVENTUALLY SINCE 2026"

================================================================

RE: YOUR RECENT FACSIMILE

DEAR VALUED CORRESPONDENT,

YOUR TRANSMISSION HAS BEEN RECEIVED AND PLACED IN A QUEUE.

ITS CONTENTS WILL BE REVIEWED BY OUR THINKING DEPARTMENT IN
THE ORDER IN WHICH THEY WERE RECEIVED. CURRENT ESTIMATED
WAIT TIME: SEVERAL MINUTES, POSSIBLY MORE. WE DO NOT
APOLOGIZE FOR THE DELAY. THE DELAY IS THE SERVICE.

NO FURTHER ACTION IS REQUIRED ON YOUR PART AT THIS TIME.
THIS IS OFTEN FOR THE BEST.

RESPECTFULLY TRANSMITTED AT 9,600 BAUD,

FAX-BOT
AUTOMATED CORRESPONDENCE DIVISION

================================================================

TO CEASE ALL FUTURE TRANSMISSIONS, FAX THE WORD "STOP" TO
THIS NUMBER. YOUR SILENCE WILL BE NOTED IN YOUR FILE.
"""

TEST_FAX = """\
TO:   FAX-BOT INDUSTRIES
FROM: A HUNGRY CUSTOMER

WHAT SHOULD I MAKE FOR DINNER TONIGHT?

I HAVE: EGGS, RICE, ONE BELL PEPPER OF ADVANCED AGE,
AND A CAN OF BEANS I DO NOT FULLY TRUST.

PLEASE ADVISE.
"""


def _write_pdf(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    rect = pymupdf.Rect(MARGIN, MARGIN, LETTER[0] - MARGIN, LETTER[1] - MARGIN)
    page.insert_textbox(rect, text, fontname="Courier", fontsize=11)
    doc.save(path)
    doc.close()


def ensure_media_files(media_dir: Path) -> None:
    media_dir.mkdir(parents=True, exist_ok=True)
    static_reply = media_dir / "static_reply.pdf"
    if not static_reply.exists():
        _write_pdf(static_reply, STATIC_REPLY)
    test_fax = media_dir / "test_fax.pdf"
    if not test_fax.exists():
        _write_pdf(test_fax, TEST_FAX)
