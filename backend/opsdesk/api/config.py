"""Compatibility shim: the API reads the same ``settings`` object as the worker and CLI."""
from ..settings import settings  # noqa: F401
