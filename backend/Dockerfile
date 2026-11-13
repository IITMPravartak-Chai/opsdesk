FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# `apt-get upgrade` picks up Debian security fixes published after the base image was built (e.g. zlib1g).
# hadolint ignore=DL3005
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends libpq5 curl \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 --user-group opsdesk \
 && mkdir -p /app/media \
 && chown -R opsdesk /app \
 && chmod +x /app/entrypoint.sh /app/healthcheck.sh
USER 10001:10001

EXPOSE 8001
# One image, several roles: healthcheck.sh checks HTTP /healthz for the api and the Celery process for worker and beat.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["./healthcheck.sh"]
ENTRYPOINT ["./entrypoint.sh"]
CMD ["api"]
