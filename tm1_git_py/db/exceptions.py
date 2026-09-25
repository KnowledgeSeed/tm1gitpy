"""Shared exception types for tm1_git_py.db, importable without circular imports."""

from __future__ import annotations


class CacheInUseError(RuntimeError):
    """Raised when a purge is refused because the cache still has open connections."""
