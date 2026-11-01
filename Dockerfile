# python:3.14-slim, digest-pinned for reproducible builds
FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    DATA_DIR=/data

# uv 0.12.3
COPY --from=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Chromium + system deps for Playwright rendering
RUN uv run playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

COPY . .

# The persona is loaded at runtime; a .dockerignore edit that drops it must
# fail the build, not the first fax.
RUN test -f app/persona/system_prompt.md

EXPOSE 8080
CMD ["./start.sh"]
