FROM python:3.13-slim

WORKDIR /app/backend

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source + static/data/logs.
COPY . ./

# App DBs are gitignored locally; ship empty schemas from committed templates.
RUN cp data/db.empty.sqlite3 data/users.sqlite3 \
 && cp data/scrips.empty.sqlite3 data/scrips.sqlite3

# Ensure `icici_breeze_backend` (under `src/`) is importable.
ENV PYTHONPATH=/app/backend/src

EXPOSE 8000

CMD ["uvicorn", "icici_breeze_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

