#!/bin/sh
# One image, several roles: api | worker | beat | migrate | <any `opsdesk` CLI command>
set -e

case "$1" in
  api)
    exec uvicorn opsdesk.api.main:app --host 0.0.0.0 --port "${API_PORT:-8001}" \
      --workers "${API_WORKERS:-2}" --proxy-headers --forwarded-allow-ips="*"
    ;;
  worker)
    exec celery -A opsdesk.worker.celery_app worker --loglevel "${LOG_LEVEL:-INFO}" --concurrency "${CELERY_CONCURRENCY:-4}"
    ;;
  beat)
    # Run exactly ONE beat instance per environment.
    exec celery -A opsdesk.worker.celery_app beat --loglevel "${LOG_LEVEL:-INFO}" --schedule /tmp/celerybeat-schedule
    ;;
  migrate)
    # Runs with the schema-owner credentials (MIGRATION_DATABASE_URL): applies migrations, then re-applies row-level
    # security policies and grants to DB_APP_ROLE.
    exec python -m opsdesk migrate
    ;;
  *)
    exec python -m opsdesk "$@"
    ;;
esac
