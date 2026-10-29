"""Celery application (worker and beat). No web framework involved."""
from celery import Celery

from ..settings import settings

app = Celery("opsdesk", broker=settings.redis_url, include=["opsdesk.worker.tasks"])
# "Current app" is thread-local; the API calls task.delay() from its request threads, so make this the process default.
app.set_default()
app.conf.update(
    task_ignore_result=True,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    beat_schedule={
        "sla-monitor": {"task": "opsdesk.tickets.tasks.sla_monitor", "schedule": 60.0},
        "poll-mailboxes": {"task": "opsdesk.channels.tasks.poll_pull_channels", "schedule": 120.0},
        "reconcile-inbound": {"task": "opsdesk.channels.tasks.reconcile_inbound", "schedule": 60.0},
        "auto-close-resolved": {"task": "opsdesk.tickets.tasks.auto_close_resolved", "schedule": 3600.0},
        "purge-revoked-tokens": {"task": "opsdesk.auth.purge_revoked_tokens", "schedule": 21600.0},
    },
)
