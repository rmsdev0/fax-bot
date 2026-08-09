# Contributing

Delighted you're here. fax-bot is a small codebase and means to stay that way.

## Setup

```bash
docker compose up -d postgres
uv sync && uv run playwright install chromium
cp .env.example .env   # fakes are fine unless you're talking to real Telnyx
```

## Before you open a PR

```bash
uv run ruff check .
uv run ruff format .
uv run pytest -q
```

All three must pass; CI runs exactly these. Tests use SQLite and fake
Telnyx/Anthropic clients — no credentials or network needed.

## Ground rules

- Schema changes need an Alembic migration (`alembic/versions/`); startup
  applies them automatically on Postgres. Migrations must work on a populated
  database, not just a fresh one.
- The persona (`app/persona/system_prompt.md`) is the product's voice. Edits
  to it are welcome but held to a high standard of bureaucratic whimsy.
- Privacy promises in `app/templates/privacy.html` must stay implemented, not
  aspirational. If you change behavior, change the policy page in the same PR.
