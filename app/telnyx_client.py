"""Telnyx API calls and webhook signature verification."""

import base64
import time

import httpx
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.config import Settings

TELNYX_API = "https://api.telnyx.com/v2"
TIMESTAMP_TOLERANCE_SECONDS = 300

# A 20-page fax PDF is typically ~2 MB; 20 MB is a 10x safety margin.
MAX_MEDIA_BYTES = 20 * 1024 * 1024
# Content-type is advisory only (S3 presigned URLs often say octet-stream);
# the %PDF magic check below is what actually gates the file.
ALLOWED_MEDIA_TYPES = {"application/pdf", "application/octet-stream"}


class MediaValidationError(Exception):
    """The downloaded media is not an acceptable fax PDF (permanent — do not retry)."""


def verify_webhook_signature(
    public_key_b64: str, signature_b64: str, timestamp: str, body: bytes
) -> bool:
    """Verify Telnyx's Ed25519 webhook signature over "{timestamp}|{body}"."""
    try:
        if abs(time.time() - int(timestamp)) > TIMESTAMP_TOLERANCE_SECONDS:
            return False
        key = VerifyKey(base64.b64decode(public_key_b64))
        key.verify(f"{timestamp}|".encode() + body, base64.b64decode(signature_b64))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False


def send_fax(settings: Settings, to: str, media_url: str, from_number: str | None = None) -> str:
    """POST /v2/faxes; returns the Telnyx fax id."""
    response = httpx.post(
        f"{TELNYX_API}/faxes",
        headers={"Authorization": f"Bearer {settings.telnyx_api_key.get_secret_value()}"},
        json={
            "connection_id": settings.telnyx_connection_id,
            "to": to,
            "from": from_number or settings.fax_bot_number,
            "media_url": media_url,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"]["id"]


def download_media(url: str) -> bytes:
    """Fetch an inbound fax PDF. Must run inside the webhook handler —
    the signed URL expires minutes after fax.received."""
    with httpx.stream("GET", url, timeout=30, follow_redirects=True) as response:
        response.raise_for_status()
        ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype and ctype not in ALLOWED_MEDIA_TYPES:
            raise MediaValidationError(f"unexpected content-type {ctype}")
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > MAX_MEDIA_BYTES:
                raise MediaValidationError(f"media exceeds {MAX_MEDIA_BYTES} byte cap")
            chunks.append(chunk)
    content = b"".join(chunks)
    if not content.startswith(b"%PDF"):
        raise MediaValidationError("not a PDF (magic mismatch)")
    return content
