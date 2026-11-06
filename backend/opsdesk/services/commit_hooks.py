"""After-commit callbacks (replaces ``transaction.on_commit``).

Side effects that must only happen if the business change commits (queueing a task, sending a
notification) are registered with ``on_commit(session, fn)``. They run after the outermost commit;
a rollback discards them. ``robust=True`` logs a failing callback instead of raising, for work that is
secondary to a change that is already durable.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_KEY = "opsdesk_on_commit"


def on_commit(session: Session, fn: Callable[[], None], *, robust: bool = False) -> None:
    session.info.setdefault(_KEY, []).append((fn, robust))


@contextmanager
def savepoint(session: Session) -> Iterator[None]:
    """A SAVEPOINT whose after-commit callbacks are discarded if it rolls back (``atomic()`` in a transaction)."""
    mark = len(session.info.get(_KEY, []))
    try:
        with session.begin_nested():
            yield
    except BaseException:
        del session.info.get(_KEY, [])[mark:]
        raise


@event.listens_for(Session, "after_commit")
def _run_hooks(session: Session) -> None:
    hooks = session.info.pop(_KEY, [])
    for fn, robust in hooks:
        try:
            fn()
        except Exception:  # noqa: BLE001
            if not robust:
                raise
            log.exception("after-commit callback failed")


@event.listens_for(Session, "after_rollback")
def _drop_hooks(session: Session) -> None:
    session.info.pop(_KEY, None)
