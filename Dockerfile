# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim AS assets
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund --ignore-scripts
COPY scripts/build-assets.mjs scripts/build-assets.mjs
COPY frontend frontend
COPY app/templates app/templates
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app TMPDIR=/app/var/tmp
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update && apt-get install -y --no-install-recommends postgresql-client-16 \
 && apt-get purge -y --auto-remove curl gnupg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock
COPY alembic.ini ./
COPY migrations migrations
COPY app app
COPY --from=assets /src/app/static/dist app/static/dist
RUN printf '#!/bin/sh\nexec python -m app.cli "$@"\n' > /usr/local/bin/karto && chmod 755 /usr/local/bin/karto \
 && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin karto \
 && mkdir -p /app/var/tmp /app/var/uploads && chown -R karto:karto /app/var
USER karto
EXPOSE 8000
CMD ["karto", "web", "--host", "0.0.0.0", "--port", "8000"]
