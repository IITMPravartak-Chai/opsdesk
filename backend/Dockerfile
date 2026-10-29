FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 opsdesk && mkdir -p /app/media && chown -R opsdesk /app && chmod +x /app/entrypoint.sh
USER opsdesk

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:${API_PORT:-8001}/healthz || exit 1
ENTRYPOINT ["./entrypoint.sh"]
CMD ["api"]
