"""Shared test environment.

Every secret and operator-specific setting gets a fake value here, before any
test runs. Environment variables take precedence over `.env` in
pydantic-settings, so a developer's real `.env` (live API keys, real fax
numbers) can never leak into test behavior — or into a failing test's output.
"""

import pytest

FAKE_ENV = {
    "TELNYX_API_KEY": "test-telnyx-key",
    "TELNYX_PUBLIC_KEY": "",
    "TELNYX_CONNECTION_ID": "0000000000",
    "FAX_BOT_NUMBER": "+15550001234",
    "TEST_FAX_NUMBER": "+15550005678",
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "ADMIN_TOKEN": "test-admin-token",
    "SENTRY_DSN": "",
    "PUBLIC_BASE_URL": "https://faxbot.test",
}


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
