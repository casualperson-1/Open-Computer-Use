# syntax=docker/dockerfile:1.7
#
# Server image: FastAPI + SQLite + the outbound node hub. No X server, no
# Playwright browsers and no secrets. Mouse, keyboard and browser control run on
# desktop nodes, which dial out to this container.

# ---------------------------------------------------------------------------
# Stage 1: build a self-contained virtualenv from the pinned lock file
# ---------------------------------------------------------------------------
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Compilers are only needed if a wheel is missing for this platform; they stay
# in the builder stage and never reach the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY server/requirements.lock.txt ./requirements.lock.txt
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/pip install -r requirements.lock.txt

# ---------------------------------------------------------------------------
# Stage 2: minimal runtime, unprivileged user, no build tooling
# ---------------------------------------------------------------------------
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app:/app/server" \
    OPEN_COMPUTER_USE_DATA_DIR=/data \
    BACKUP_DIR=/backups

RUN groupadd --gid 10001 ocu \
    && useradd --uid 10001 --gid ocu --no-create-home --home-dir /app \
        --shell /usr/sbin/nologin ocu \
    && mkdir -p /app /data /backups /run/secrets \
    && chown -R ocu:ocu /data /backups /run/secrets

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY server/ /app/server/
COPY node/ /app/node/
COPY shared/ /app/shared/
COPY docker/server-entrypoint.sh /usr/local/bin/ocu-entrypoint

RUN chmod 0755 /usr/local/bin/ocu-entrypoint

USER 10001:10001
EXPOSE 8765
STOPSIGNAL SIGTERM

# Uses the public health endpoint, so it works before the owner is created.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["/usr/local/bin/ocu-entrypoint"]
CMD ["serve"]
