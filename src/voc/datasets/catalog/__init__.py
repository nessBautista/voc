"""Register and resolve personal dataset references; no artifact uploads."""

from .operations import publish, resolve_local

__all__ = ["publish", "resolve_local"]
