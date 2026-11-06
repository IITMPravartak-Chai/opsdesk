from __future__ import annotations

from functools import lru_cache
from importlib import import_module

from ..settings import settings


def import_string(path: str):
    module, _, attr = path.rpartition(".")
    if not module:
        raise ImportError(f"'{path}' is not a dotted path")
    return getattr(import_module(module), attr)


@lru_cache(maxsize=1)
def adapter_classes() -> dict:
    return {kind: import_string(path) for kind, path in settings.channel_adapters.items()}


def get_adapter(channel):
    cls = adapter_classes().get(channel.kind)
    if cls is None:
        raise LookupError(f"No adapter registered for channel kind '{channel.kind}'")
    return cls(channel)
