FROM python:3.13-slim

WORKDIR /app/backend

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source + static/data/logs.
COPY . ./

# Ensure `icici_breeze_backend` (under `src/`) is importable.
ENV PYTHONPATH=/app/backend/src

EXPOSE 8000

CMD ["uvicorn", "icici_breeze_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

