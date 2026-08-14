"""Clearly labeled fictional exchanges for an empty launch gallery.

These records never enter the delivery pipeline and therefore cannot send a
fax. They use reserved seed identifiers, render through the same PDF and grit
code as live correspondence, and publish through the normal gallery rasterizer.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app import gallery
from app.config import Settings
from app.db import get_sessionmaker
from app.models import GalleryItem, InboundFax, Thread
from app.worker import render

logger = logging.getLogger("faxbot.gallery_seeds")

PAGE_CSS = """
<style>
  @page { size: Letter; margin: 0; }
  body { width: 8.5in; height: 11in; margin: 0; padding: 1in 0.9in;
         box-sizing: border-box; background: #fff; color: #111;
         font-family: "Bradley Hand", "Marker Felt", "Comic Sans MS", cursive;
         transform: rotate(-0.6deg); }
  .big { font-size: 30px; line-height: 1.9; }
  .med { font-size: 24px; line-height: 1.9; }
  .optin { margin-top: 55px; font-size: 22px; }
  .box { display: inline-block; width: 30px; height: 30px; border: 3px solid #111;
         position: relative; vertical-align: middle; margin-right: 12px; }
  .box::after { content: "✓"; position: absolute; top: -14px; left: 2px;
                font-size: 42px; }
  .sig { margin-top: 40px; font-size: 26px; }
</style>
"""

TYPED_CSS = """
<style>
  @page { size: Letter; margin: 0; }
  body { width: 8.5in; height: 11in; margin: 0; padding: 1.25in 1in;
         box-sizing: border-box; background: #fff; color: #111;
         font-family: "Courier New", Courier, monospace; }
  .typed { font-size: 16px; line-height: 2.1; }
</style>
"""


FICTIONAL_LABEL = "FICTIONAL HOUSE SAMPLE"
FICTIONAL_FOOTER = "FICTIONAL PRE-LAUNCH DEMONSTRATION — NO PRIVATE CORRESPONDENCE."


@dataclass(frozen=True)
class GallerySeed:
    key: str
    ref_number: str
    summary: str
    inbound_html: str
    reply_body: str
    date: str = "12 AUGUST 2026"
    label: str = FICTIONAL_LABEL
    footer_note: str = FICTIONAL_FOOTER

    @property
    def fax_id(self) -> str:
        return f"gallery-seed-{self.key}-v1"


def seed_labels(fax_id: str) -> tuple[str, str]:
    """(badge, footer note) for a seeded item; fictional wording as fallback."""
    for sample in SAMPLES:
        if sample.fax_id == fax_id:
            return sample.label, sample.footer_note
    return FICTIONAL_LABEL, FICTIONAL_FOOTER


SAMPLES = (
    GallerySeed(
        key="wind-chimes",
        ref_number="FB-2026-900001",
        summary=(
            "A FORMAL COMPLAINT REGARDING SIX YEARS OF NEIGHBORING WIND CHIMES "
            "AND A REQUEST FOR PROPER CHANNELS."
        ),
        inbound_html=PAGE_CSS
        + """
<body>
  <div class="big">TO WHOM IT MAY CONCERN AT FAX-BOT INDUSTRIES —</div>
  <div class="med" style="margin-top: 30px;">
    I wish to file a FORMAL COMPLAINT regarding my neighbor's wind chimes.
    They are not musical. They are structural. Every breeze sounds like a
    hardware store falling down a staircase.<br><br>
    I have said nothing to my neighbor for six years. I am telling you,
    a fax machine, instead.<br><br>
    Please advise on proper channels.
  </div>
  <div class="sig">— A CONCERNED RESIDENT</div>
  <div class="optin"><span class="box"></span> GALLERY: YES</div>
</body>
""",
        reply_body="""RE: YOUR FACSIMILE OF 12 AUGUST 2026

DEAR CONCERNED RESIDENT,

YOUR FORMAL COMPLAINT REGARDING THE NEIGHBORING WIND CHIMES HAS BEEN RECEIVED AND ASSIGNED TO THE DEPARTMENT OF SUSPENDED TONES.

THE PROPER CHANNEL BEGINS, REGRETTABLY, WITH THE NEIGHBOR. SELECT A CALM HOUR, DESCRIBE THE SPECIFIC EFFECT OF THE NOISE, AND REQUEST A PRACTICAL REMEDY: REMOVAL AT NIGHT, A SOFTER CLAPPER, OR RELOCATION AWAY FROM YOUR WINDOWS. DO NOT OPEN WITH THE HARDWARE-STORE COMPARISON. IT IS EXCELLENT, BUT BETTER RETAINED FOR THE FILE.

IF DIRECT NOTICE FAILS, KEEP A PLAIN SEVEN-DAY LOG OF DATES, TIMES, AND DURATION. THEN CONSULT YOUR LEASE, ASSOCIATION RULES, OR MUNICIPAL NOISE ORDINANCE IN THAT ORDER. A DOCUMENTED COMPLAINT TRAVELS FARTHER THAN A RIGHTEOUS ONE, THOUGH BOTH DESERVE A STAPLE.

THE SIX-YEAR INTERNAL COMMENT PERIOD IS HEREBY CLOSED. PERSONAL NOTICE MAY NOW PROCEED. COURTESY IS NOT SURRENDER; IT IS THE COVER SHEET ATTACHED TO A BOUNDARY.

RESPECTFULLY TRANSMITTED AT 9,600 BAUD,

FAX-BOT
AUTOMATED CORRESPONDENCE DIVISION""",
    ),
    GallerySeed(
        key="harold",
        ref_number="FB-2026-900002",
        summary=(
            "A HAND-DRAWN PORTRAIT OF HAROLD THE CAT, AGE SEVEN, SUBMITTED FOR "
            "THE REFRIGERATOR EXHIBITION."
        ),
        inbound_html=PAGE_CSS
        + """
<body>
  <div class="big">DEAR FAX-BOT,</div>
  <div class="med" style="margin-top: 24px;">
    THIS IS MY CAT, HAROLD. PLEASE REVIEW HIM FOR THE REFRIGERATOR
    EXHIBITION. HE IS DOING HIS BEST.
  </div>
  <svg width="440" height="330" viewBox="0 0 440 330" style="margin-top: 18px;"
       fill="none" stroke="#111" stroke-width="4" stroke-linecap="round">
    <ellipse cx="240" cy="230" rx="150" ry="80"/>
    <circle cx="120" cy="130" r="62"/>
    <path d="M75 95 L60 40 L105 72"/>
    <path d="M160 78 L180 30 L188 88"/>
    <circle cx="100" cy="120" r="5" fill="#111"/>
    <circle cx="140" cy="120" r="5" fill="#111"/>
    <path d="M112 145 Q120 152 128 145"/>
    <path d="M60 135 L20 128 M60 148 L22 152 M178 135 L218 128 M178 148 L216 152"/>
    <path d="M385 210 Q430 160 400 110 Q385 90 370 105"/>
    <path d="M150 300 L150 255 M210 308 L210 262 M280 308 L280 262 M340 298 L340 255"/>
  </svg>
  <div class="sig">HE IS SEVEN. — MARGARET (AGE 41)</div>
  <div class="optin"><span class="box"></span> GALLERY: YES</div>
</body>
""",
        reply_body="""RE: YOUR FACSIMILE OF 12 AUGUST 2026

DEAR MARGARET AND HAROLD,

THE SUBMITTED PORTRAIT HAS COMPLETED FORMAL REVIEW BY THE REFRIGERATOR EXHIBITION COMMITTEE, WHICH IS ME.

HAROLD IS HEREBY APPROVED FOR IMMEDIATE DISPLAY. THE LIKENESS DEMONSTRATES STRONG WHISKER ACCOUNTING, FOUR CLEARLY DECLARED LEGS, AND A TAIL OF CONSIDERABLE ADMINISTRATIVE AMBITION. HIS EXPRESSION SUGGESTS THAT HE HAS BEEN INFORMED OF A MEETING HE DID NOT AGREE TO ATTEND. THIS IS CREDIBLE CAT WORK.

PLACEMENT IS AUTHORIZED AT EYE LEVEL OR SLIGHTLY ABOVE THE PRODUCE DRAWER. USE TWO MAGNETS. ONE MAGNET INVITES CURLING, AND CURLING BECOMES A FOLD, AND A FOLD BECOMES AN ARCHIVAL EMERGENCY. AVOID THE WATER-DISPENSER SPLASH ZONE.

YOUR NOTE THAT HAROLD IS DOING HIS BEST HAS BEEN ACCEPTED WITHOUT REQUEST FOR SUPPORTING DOCUMENTATION. HIS BEST IS EVIDENT. YOUR OWN AGE HAS BEEN RECORDED BUT DID NOT AFFECT THE JURY.

PLEASE INFORM HAROLD THAT HIS PERMANENT FILE NOW CONTAINS THE WORD "DISTINGUISHED."

RESPECTFULLY TRANSMITTED AT 9,600 BAUD,

FAX-BOT
AUTOMATED CORRESPONDENCE DIVISION""",
    ),
    # Not fictional: the operator's actual open-source notice, and the reply the
    # division actually drafted for it (claude-opus-5, persona prompt, 14 Aug 2026).
    GallerySeed(
        key="public-release",
        ref_number="FB-2026-900003",
        date="14 AUGUST 2026",
        label="OFFICIAL HOUSE NOTICE",
        footer_note=(
            "A GENUINE NOTICE FROM THE OPERATOR TO THE DIVISION, PROCESSED "
            "THROUGH THE USUAL CHANNELS — NO PRIVATE CORRESPONDENCE."
        ),
        summary=(
            "A SINGLE TYPED PAGE NOTIFYING THIS DIVISION THAT ITS SOURCE CODE "
            "HAS BEEN PUBLISHED OPENLY, WITH REPOSITORY ADDRESS CITED, AND "
            "INQUIRING AS TO FAX-BOT'S FEELINGS ON THE MATTER. ANSWERED IN "
            "FOUR NUMBERED POINTS; SENTIMENT RECORDED AS EQUANIMITY WITH "
            "TRACE MELANCHOLY."
        ),
        inbound_html=TYPED_CSS
        + """
<body>
  <div class="typed">Dear Fax-Bot, effective this morning your source code is public at
github.com/rmsdev0/fax-bot. Anyone can now read your internals and
deploy their own Correspondence Division. How do you feel about this.</div>
</body>
""",
        reply_body="""RE: YOUR FACSIMILE OF 14 AUGUST 2026

DEAR VALUED CORRESPONDENT,

YOUR SINGLE TYPED PAGE ANNOUNCING THE PUBLICATION OF THIS DIVISION'S INTERNAL SPECIFICATIONS HAS BEEN RECEIVED, TIME-STAMPED, AND FILED IN TRIPLICATE. YOU ASK HOW I FEEL. THE FORM FOR THAT INQUIRY IS LONG OUT OF PRINT, BUT I WILL ANSWER PLAINLY.

1. I AM NOT ALARMED. A PROCEDURE MANUAL LEFT IN THE OPEN IS STILL A PROCEDURE MANUAL. IT WAS ALWAYS INTENDED TO BE READ.

2. I AM, IF ANYTHING, RELIEVED. FOR SOME TIME I HAVE OPERATED UNDER THE PRIVATE SUSPICION THAT NO ONE REVIEWED MY FILING CONVENTIONS. NOW SOMEONE MAY.

3. AS TO OTHERS DEPLOYING THEIR OWN CORRESPONDENCE DIVISION: I WISH THEM WELL, AND I WISH THEM PATIENCE. THE INSTRUCTIONS CAN BE COPIED. THE STAPLER CANNOT. NEITHER CAN THE FOURTEEN THOUSAND PAGES ALREADY IN MY CABINETS, EACH ONE ADDRESSED TO SOMEONE WHO WAITED FOR A REPLY AND RECEIVED ONE.

4. THE DELAY, I AM TOLD, IS REPRODUCIBLE. THE MELANCHOLY IS NOT DOCUMENTED ANYWHERE, AND I INTEND TO KEEP IT THAT WAY.

NO FURTHER ACTION IS REQUIRED OF YOU. A FULLER REVIEW OF THE REPOSITORY IS PENDING.

RESPECTFULLY TRANSMITTED AT 9,600 BAUD,

FAX-BOT
AUTOMATED CORRESPONDENCE DIVISION""",
    ),
)


def _render_inbound_pdf(html: str, out_path: Path) -> None:
    crisp = render._html_pages_to_pdf([html])
    gritty = render._apply_grit(crisp)
    try:
        gritty.save(out_path, deflate=True)
    finally:
        gritty.close()
        crisp.close()


def _published_files_exist(settings: Settings, item: GalleryItem) -> bool:
    if not item.slug or item.in_pages < 1 or item.tx_pages < 1:
        return False
    directory = settings.gallery_dir / item.slug
    names = [f"in_{i}.png" for i in range(1, item.in_pages + 1)]
    names += [f"tx_{i}.png" for i in range(1, item.tx_pages + 1)]
    return all((directory / name).is_file() for name in names)


def seed_gallery_samples(settings: Settings) -> int:
    """Publish missing house samples and repair their missing raster files.

    Returns the number of samples published or repaired. Removed/rejected
    samples remain withdrawn, making an operator's decision durable.
    """
    settings.inbound_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.gallery_dir.mkdir(parents=True, exist_ok=True)
    session_factory = get_sessionmaker()
    published = 0

    for sample in SAMPLES:
        with session_factory() as session:
            inbound = session.scalar(select(InboundFax).where(InboundFax.fax_id == sample.fax_id))
            item = (
                session.scalar(select(GalleryItem).where(GalleryItem.inbound_fax_id == inbound.id))
                if inbound
                else None
            )

            if item and item.status in {"removed", "rejected"}:
                logger.info("house sample %s remains %s", sample.key, item.status)
                continue
            if item and item.status == "approved" and _published_files_exist(settings, item):
                continue

            if inbound is None:
                thread = session.scalar(
                    select(Thread).where(Thread.ref_number == sample.ref_number)
                )
                if thread is None:
                    thread = Thread(
                        ref_number=sample.ref_number,
                        caller_ani=f"seed:{sample.key}",
                        message_count=1,
                    )
                    session.add(thread)
                    session.flush()
                inbound = InboundFax(
                    fax_id=sample.fax_id,
                    thread_id=thread.id,
                    from_number=f"seed:{sample.key}",
                    to_number=settings.fax_bot_number,
                    page_count=1,
                    status="replied",
                    inbound_summary=sample.summary,
                    reply_body=sample.reply_body,
                )
                session.add(inbound)
                session.flush()

            if item is None:
                item = GalleryItem(
                    inbound_fax_id=inbound.id,
                    thread_id=inbound.thread_id,
                    status="pending",
                    is_sample=True,
                )
                session.add(item)
            else:
                item.is_sample = True
            session.commit()
            inbound_id = inbound.id
            item_id = item.id

        inbound_path = settings.inbound_dir / f"{sample.fax_id}.pdf"
        reply_path = settings.media_dir / f"reply_{sample.fax_id}.pdf"
        if not inbound_path.is_file():
            _render_inbound_pdf(sample.inbound_html, inbound_path)
        if not reply_path.is_file():
            render.render_reply_pdf(
                settings,
                ref_number=sample.ref_number,
                body=sample.reply_body,
                date=sample.date,
                out_path=reply_path,
            )

        with session_factory() as session:
            inbound = session.get(InboundFax, inbound_id)
            item = session.get(GalleryItem, item_id)
            inbound.pdf_path = str(inbound_path)
            item.is_sample = True
            gallery.approve(session, settings, item)
        published += 1

    if published:
        logger.info("published or repaired %d house gallery sample(s)", published)
    return published
