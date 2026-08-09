# python:3.12-slim, digest-pinned for reproducible builds
FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

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

EXPOSE 8080
CMD ["./start.sh"]
