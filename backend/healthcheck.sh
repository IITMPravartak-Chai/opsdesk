#!/bin/sh
# Container health check for the shared backend image. One image runs several roles, so the check depends on the role:
#
#   api      HTTP GET /healthz on the API port (also checks the database)
#   worker   a running `celery ... worker` process
#   beat     a running `celery ... beat` process
#   other    one-off commands (migrate, CLI): nothing to watch, always healthy
#
# Usage: healthcheck.sh [api|worker|beat]
# Without an argument the role is the one entrypoint.sh recorded in /tmp/opsdesk-role when the container started.
# The image has no ps or pgrep, so processes are found by reading /proc.

role="${1:-$(cat /tmp/opsdesk-role 2>/dev/null)}"

# True when a process whose command line is `... celery ... <$1> ...` is running.
celery_running() {
  for f in /proc/[0-9]*/cmdline; do
    cmd=$(tr '\0' ' ' < "$f" 2>/dev/null) || continue
    case "$cmd" in
      *"celery "*" $1 "*) return 0 ;;
    esac
  done
  return 1
}

case "$role" in
  api)
    curl -fsS -o /dev/null "http://localhost:${API_PORT:-8001}/healthz" || exit 1
    ;;
  worker|beat)
    if ! celery_running "$role"; then
      echo "no running celery $role process" >&2
      exit 1
    fi
    ;;
  *)
    exit 0
    ;;
esac
