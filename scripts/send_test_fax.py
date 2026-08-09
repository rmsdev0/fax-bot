"""Send a test fax from the test number to fax-bot's public number.

Prerequisites: server running, ngrok tunnel up, PUBLIC_BASE_URL set to the
ngrok URL in .env, and the Telnyx fax app webhook pointed at it.

Usage: uv run python scripts/send_test_fax.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.telnyx_client import send_fax


def main() -> None:
    settings = get_settings()
    if "localhost" in settings.public_base_url:
        sys.exit(
            "PUBLIC_BASE_URL is still localhost — Telnyx cannot fetch the test PDF "
            "from there. Start ngrok and set PUBLIC_BASE_URL in .env first."
        )
    media_url = f"{settings.public_base_url}/media/test_fax.pdf"
    fax_id = send_fax(
        settings,
        to=settings.fax_bot_number,
        from_number=settings.test_fax_number,
        media_url=media_url,
    )
    print(f"Test fax queued: {fax_id}")
    print(f"  {settings.test_fax_number} -> {settings.fax_bot_number}")
    print("Watch the server logs; the round trip typically takes a few minutes.")
    print("Check status: curl http://localhost:8000/admin/recent")


if __name__ == "__main__":
    main()
