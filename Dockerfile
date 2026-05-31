# Single image: nginx (port 3000) + Next.js (standalone) + FastAPI. Build from repo root:
#   docker build -t breeze-core-engine .
#
# Run (requires secrets — use an env file or mount):
#   podman run --rm -p 3000:3000 --env-file .env ghcr.io/<owner>/<repo>:latest
# Optional: persist backend data
#   podman run ... -v /opt/breeze-core-engine/data:/app/backend/data ...

FROM node:22-bookworm-slim AS version-extract

WORKDIR /src
COPY scripts/changelog-latest-version.mjs scripts/changelog-latest-version.mjs
COPY frontend/src/lib/changelog.ts frontend/src/lib/changelog.ts
RUN node scripts/changelog-latest-version.mjs frontend/src/lib/changelog.ts > /tmp/app_version

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

ENV NEXT_TELEMETRY_DISABLED=1
ENV DOCKER_BUILD=1
# Browser API base: omit NEXT_PUBLIC_BACKEND_URL so the client uses window.location.origin
# (same host/port as /auth/*). Hardcoding localhost breaks 127.0.0.1 or non-default port maps.
ENV BACKEND_UPSTREAM_URL=http://127.0.0.1:8000

RUN npm run build

# Node binary only (no apt NodeSource): matches bookworm base used by python:3.12-slim-bookworm.
FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm AS backend-builder

WORKDIR /build

COPY backend/requirements.txt ./
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

COPY backend/src ./src
COPY backend/static ./static
COPY backend/data/users.empty.sqlite3 backend/data/scrips.empty.sqlite3 \
  backend/data/NSEFreezeLimits.txt backend/data/BSEFreezeLimits.txt \
  ./data/

RUN python -m compileall -q -b /build/src

# Templates outside data/ survive `docker run -v ...:/app/backend/data` (bind mount hides data/*.empty.sqlite3).
RUN mkdir -p db-templates data \
  && cp data/users.empty.sqlite3 db-templates/ \
  && cp data/scrips.empty.sqlite3 db-templates/ \
  && cp data/NSEFreezeLimits.txt db-templates/ \
  && cp data/BSEFreezeLimits.txt db-templates/ \
  && cp data/users.empty.sqlite3 data/users.sqlite3 \
  && cp data/scrips.empty.sqlite3 data/scrips.sqlite3

# 3.12: prebuilt wheels for pinned pydantic; 3.13 + pydantic 2.5 can fail on arm64 (source build).
FROM python:3.12-slim-bookworm AS runtime

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=backend-builder /install /usr/local

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
    libssl3 \
    nginx \
    supervisor \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY --from=backend-builder /build /app/backend

WORKDIR /app/frontend
COPY --from=frontend-builder /app/.next/standalone ./
COPY --from=frontend-builder /app/.next/static ./.next/static
COPY --from=frontend-builder /app/public ./public

COPY deploy/nginx.all-in-one.conf /etc/nginx/nginx.conf
COPY deploy/supervisor/breeze.conf /etc/supervisor/conf.d/breeze.conf
COPY deploy/breeze/portal_allowed_hosts.txt /etc/breeze/portal_allowed_hosts.txt

ARG PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PEM=""
ARG PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_B64=""
ARG PORTAL_ALLOWED_HOSTS=""
RUN mkdir -p /etc/breeze \
  && if [ -n "$PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_B64" ]; then \
       printf '%s' "$PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_B64" | base64 -d > /etc/breeze/portal_heartbeat_public.pem; \
     elif [ -n "$PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PEM" ]; then \
       printf '%s\n' "$PORTAL_HEARTBEAT_JWT_PUBLIC_KEY_PEM" > /etc/breeze/portal_heartbeat_public.pem; \
     fi \
  && if [ -n "$PORTAL_ALLOWED_HOSTS" ]; then \
       printf '%s\n' "$PORTAL_ALLOWED_HOSTS" > /etc/breeze/portal_allowed_hosts.txt; \
     fi \
  && test -s /etc/breeze/portal_heartbeat_public.pem \
  && grep -v '^[[:space:]]*#' /etc/breeze/portal_allowed_hosts.txt | grep -qv '^[[:space:]]*$'

RUN nginx -t

ENV PYTHONPATH=/app/backend/src
ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=version-extract /tmp/app_version /etc/breeze_app_version
ENV APP_VERSION_FILE=/etc/breeze_app_version

EXPOSE 3000

# Probe /health via nginx → FastAPI (same path as SaaS console deployment probes).
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=5)"

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
