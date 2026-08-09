# fax-bot — the world's slowest chatbot

Fax **1-866-FAX-BOT5** (+1 866 329 2685). An LLM reads your fax — typed,
handwritten, or drawn — and faxes back a thoughtful reply on company
letterhead, several minutes later. The delay is the service.

Approved exchanges (opt-in only) appear in the public gallery at `/gallery`.

## How it works

```
your fax ──PSTN──▶ Telnyx fax number (Programmable Fax app)
                        │  webhooks (Ed25519-signed)
                  ┌─────▼──────────────────────────────┐
                  │ FastAPI server (app/main.py)        │
                  │  • verify signature                 │
                  │  • download PDF before URL expires  │  ← the only real-time
                  │  • record + return (or 503 so       │    constraint anywhere
                  │    Telnyx redelivers on failure)    │
                  └─────┬──────────────────────────────┘
                  ┌─────▼──────────────┐
                  │ DB-backed queue     │  worker polls Postgres (SQLite in
                  │ (app/worker/run.py) │  tests); slowness is free, Redis is not
                  └─────┬──────────────┘
        ┌───────────────▼─────────────────────────────┐
        │ pipeline (app/worker/pipeline.py)            │
        │  policy: per-ANI caps, STOP opt-out,         │
        │          daily $ circuit breaker             │
        │  thread: ref-number (sender-bound) → ANI     │
        │          → new                               │
        │  ingest: PyMuPDF → page PNGs                 │
        │  brain:  claude-opus-5 vision + persona      │
        │          (app/persona/system_prompt.md)      │
        │  render: Jinja2 → headless Chromium → PDF    │
        │          → Pillow fax-grit filter            │
        │  deliver: POST /v2/faxes, retries 5m/30m/2h  │
        └──────────────────────────────────────────────┘
```

The persona prompt is the product; the rest is plumbing. Read
[app/persona/system_prompt.md](app/persona/system_prompt.md).

Reliability model, briefly: inbound faxes are recorded atomically with their
webhook event (download failures return 503 so Telnyx redelivers); the worker
claims work with a conditional update and requeues anything stuck in
`processing`; outbound sends are recorded before the API call and every
attempt's Telnyx id is kept, so late status callbacks always find their fax.
Schema changes ship as Alembic migrations, applied automatically at startup.

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/), Docker (for Postgres),
[ngrok](https://ngrok.com) for receiving real webhooks.

```bash
docker compose up -d postgres                      # the database
uv sync && uv run playwright install chromium
cp .env.example .env    # fill in Telnyx + Anthropic keys, set an ADMIN_TOKEN
uv run uvicorn app.main:app --reload --port 8000   # terminal 1
uv run python -m app.worker.run                    # terminal 2
ngrok http 8000                                    # terminal 3
```

Set `PUBLIC_BASE_URL` in `.env` to the ngrok URL and point the Telnyx fax
app's webhook at `https://<ngrok>/webhooks/telnyx`. Then round-trip without a
fax machine (an optional second Telnyx number plays "the user"):

```bash
uv run python scripts/send_test_fax.py
```

Tests: `uv run pytest` · Lint: `uv run ruff check . && uv run ruff format --check .`

## Production (AWS)

Terraform in [infra/](infra/) provisions: ECS Fargate (x86_64, one task running
web + worker via `start.sh`), ALB + ACM cert + Route53 record, RDS Postgres,
EFS at `/data` for fax PDFs and gallery images, and SSM-backed secrets, all in
a dedicated VPC. ~$50/month all-in.

```bash
cd infra && cp terraform.tfvars.example terraform.tfvars  # region/domain/zone/numbers
terraform init && terraform apply
cd .. && ./scripts/put_aws_secrets.sh   # .env -> SSM (values stay out of TF state)
./scripts/deploy_aws.sh                 # build amd64 image -> ECR -> roll service
```

After the first deploy: point the Telnyx fax app webhook at
`$(terraform -chdir=infra output -raw webhook_url)` and retire the ngrok
tunnel. App secrets live in SSM under `/fax-bot/*`; the moderation token is
`aws ssm get-parameter --name /fax-bot/ADMIN_TOKEN --with-decryption`.
Database migrations run automatically when the task starts.

## Moderation

Gallery publication is double-gated: the correspondent must draw the checked
GALLERY: YES box on their fax (detected by the vision model), and a human must
approve it — after actually looking at the pages:

```bash
curl -H "x-admin-token: $ADMIN_TOKEN" https://<host>/admin/gallery
# review the material before approving:
curl -H "x-admin-token: $ADMIN_TOKEN" https://<host>/admin/gallery/<id>/inbound.pdf -o inbound.pdf
curl -H "x-admin-token: $ADMIN_TOKEN" https://<host>/admin/gallery/<id>/reply.pdf -o reply.pdf
# verify the consent box is drawn and nothing sensitive is on the pages, then:
curl -X POST -H "x-admin-token: $ADMIN_TOKEN" https://<host>/admin/gallery/<id>/approve
# or: .../reject (pending items), .../unpublish (already-published items)
```

`/admin/recent` shows threads (including pending REMOVE requests), delivery
statuses, and today's spend against the daily circuit breaker. All `/admin/*`
routes require `ADMIN_TOKEN` and are disabled while it is unset.

## Privacy posture

Solicited replies only (we only ever fax people who faxed us first); STOP
honored with a farewell letter and permanent silence; originals purged after
30 days; nothing published without a drawn checkbox *and* human approval;
faxing REMOVE withdraws published exchanges and purges the sender's stored
documents ahead of schedule; no training on correspondence. The full policy
is served at `/privacy`.

## License

MIT — see [LICENSE](LICENSE).
