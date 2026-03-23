# Single image: nginx (port 3000) + Next.js (standalone) + FastAPI. Build from repo root:
#   docker build -t icici-breeze-modern .
#
# Run (requires secrets — use an env file or mount):
#   podman run --rm -p 3000:3000 --env-file .env ghcr.io/<owner>/<repo>:latest
# Optional: persist backend data
#   podman run ... -v breeze-data:/app/backend/data ...

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

ENV NEXT_TELEMETRY_DISABLED=1
# Browser API base: omit NEXT_PUBLIC_BACKEND_URL so the client uses window.location.origin
# (same host/port as /auth/*). Hardcoding localhost breaks 127.0.0.1 or non-default port maps.
ENV BACKEND_UPSTREAM_URL=http://127.0.0.1:8000

RUN npm run build

# Node binary only (no apt NodeSource): matches bookworm base used by python:3.12-slim-bookworm.
FROM node:22-bookworm-slim AS node-runtime

# 3.12: prebuilt wheels for pinned pydantic; 3.13 + pydantic 2.5 can fail on arm64 (source build).
# slim-bookworm: much smaller than full bookworm; extra apt packages keep scipy/numpy + ssl working.
FROM python:3.12-slim-bookworm

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
    libssl3 \
    nginx \
    supervisor \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
RUN cp data/db.empty.sqlite3 data/users.sqlite3 \
 && cp data/scrips.empty.sqlite3 data/scrips.sqlite3

WORKDIR /app/frontend
COPY --from=frontend-builder /app/.next/standalone ./
COPY --from=frontend-builder /app/.next/static ./.next/static
COPY --from=frontend-builder /app/public ./public

COPY deploy/nginx.all-in-one.conf /etc/nginx/nginx.conf
COPY deploy/supervisor/breeze.conf /etc/supervisor/conf.d/breeze.conf

RUN nginx -t

ENV PYTHONPATH=/app/backend/src
ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/', timeout=5)"

CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
