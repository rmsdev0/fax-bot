"""The thinking department: Claude reads the fax pages and drafts Fax-Bot's letter."""

import base64
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import anthropic
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger("faxbot.brain")

PERSONA_PATH = Path(__file__).resolve().parent.parent / "persona" / "system_prompt.md"


class FaxBotReply(BaseModel):
    reply_body: str
    ref_number: str | None
    inbound_summary: str
    stop_request: bool
    removal_request: bool
    gallery_opt_in: bool
    content_flag: bool
    flag_reason: str | None


@dataclass
class BrainResult:
    kind: str  # "reply" | "denied" | "oversize" | "capped"
    body: str
    ref_number: str | None = None
    inbound_summary: str | None = None
    stop_request: bool = False
    removal_request: bool = False
    gallery_opt_in: bool = False
    flag_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@lru_cache
def _persona() -> str:
    return PERSONA_PATH.read_text()


def denial_letter(date: str, reason: str) -> str:
    return (
        f"RE: YOUR FACSIMILE OF {date}\n\n"
        "DEAR CORRESPONDENT,\n\n"
        "YOUR REQUEST HAS BEEN REVIEWED THROUGH PROPER CHANNELS.\n\n"
        "IT HAS BEEN DENIED.\n\n"
        f"REASON: {reason}\n\n"
        "THIS DECISION IS FINAL. IT MAY BE APPEALED BY FAX. APPEALS ARE\n"
        "REVIEWED IN THE ORDER RECEIVED. NO APPEAL HAS EVER BEEN REVIEWED.\n\n"
        "RESPECTFULLY TRANSMITTED AT 9,600 BAUD,\n\n"
        "FAX-BOT\n"
        "AUTOMATED CORRESPONDENCE DIVISION"
    )


def oversize_letter(date: str, pages: int) -> str:
    return (
        f"RE: YOUR FACSIMILE OF {date}\n\n"
        "DEAR PROLIFIC CORRESPONDENT,\n\n"
        f"YOUR {pages}-PAGE SUBMISSION HAS BEEN RECEIVED, WEIGHED, AND\n"
        "RESPECTFULLY DECLINED.\n\n"
        "OUR MACHINE IS TIRED.\n\n"
        "SUBMISSIONS OF TWENTY (20) PAGES OR FEWER RECEIVE OUR FULL\n"
        "ADMINISTRATIVE ATTENTION. YOU ARE INVITED TO RESUBMIT THE\n"
        "PORTION OF YOUR MATERIAL THAT MATTERS MOST. WE FIND THIS\n"
        "EXERCISE IS OFTEN CLARIFYING FOR ALL PARTIES.\n\n"
        "RESPECTFULLY TRANSMITTED AT 9,600 BAUD,\n\n"
        "FAX-BOT\n"
        "AUTOMATED CORRESPONDENCE DIVISION"
    )


def _history_block(history: list[dict]) -> str:
    entries = []
    for h in history:
        entries.append(
            f"- ON {h['date']}: CORRESPONDENT SENT: {h['inbound_summary']}\n"
            f"  FAX-BOT REPLIED: {h['reply_excerpt']}"
        )
    return "PRIOR CORRESPONDENCE ON FILE FOR THIS REF NUMBER (oldest first):\n" + "\n".join(entries)


def generate_reply(
    settings: Settings,
    page_images: list[bytes],
    date: str,
    history: list[dict] | None = None,
) -> BrainResult:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())

    content: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.b64encode(png).decode(),
            },
        }
        for png in page_images
    ]
    instruction = (
        f"TODAY'S DATE: {date}\n"
        f"The attached images are the {len(page_images)} page(s) of an inbound "
        "facsimile, in order. Read everything on them — typed text, handwriting, "
        "drawings, stamps, margins — and produce your structured reply per your "
        "standing instructions."
    )
    if history:
        instruction = _history_block(history) + "\n\n" + instruction
    content.append({"type": "text", "text": instruction})

    response = client.messages.parse(
        model=settings.anthropic_model,
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": _persona(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": content}],
        output_format=FaxBotReply,
    )

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    if response.stop_reason == "refusal":
        logger.warning("model refusal: %s", getattr(response, "stop_details", None))
        return BrainResult(
            kind="denied",
            body=denial_letter(date, "CONTENTS UNSUITABLE FOR PROCESSING"),
            flag_reason="MODEL REFUSAL",
            **usage,
        )

    parsed = response.parsed_output
    if parsed is None:
        logger.error("structured output parse failed (stop_reason=%s)", response.stop_reason)
        return BrainResult(
            kind="denied",
            body=denial_letter(date, "ADMINISTRATIVE ERROR (FORM 7 MISFILED)"),
            flag_reason="PARSE FAILURE",
            **usage,
        )

    if parsed.content_flag:
        reason = (parsed.flag_reason or "WHIMSY DEFICIT").upper()
        return BrainResult(
            kind="denied",
            body=denial_letter(date, reason),
            ref_number=parsed.ref_number,
            inbound_summary=parsed.inbound_summary,
            flag_reason=reason,
            **usage,
        )

    return BrainResult(
        kind="reply",
        body=parsed.reply_body,
        ref_number=parsed.ref_number,
        inbound_summary=parsed.inbound_summary,
        stop_request=parsed.stop_request,
        removal_request=parsed.removal_request,
        gallery_opt_in=parsed.gallery_opt_in,
        **usage,
    )
