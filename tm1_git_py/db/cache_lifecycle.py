"""Thin, embedder-facing wrappers for changeset/model sqlite cache lifecycle."""

from __future__ import annotations

from typing import Optional

from tm1_git_py.db.changeset_store import ChangesetStore
from tm1_git_py.db.model_store import ModelStore


def purge_changeset_cache(changeset_id: str, base_dir: Optional[str] = None, force: bool = False) -> bool:
    """Close then delete a changeset's sqlite cache file(s). Returns True if any file was removed.

    Raises CacheInUseError if the cache is busy (refcount > 0) and force is False.
    """
    return ChangesetStore.purge(changeset_id=changeset_id, base_dir=base_dir, force=force)


def purge_model_cache(model_id: str, force: bool = False) -> bool:
    """Close then delete a model's sqlite cache file(s). Returns True if any file was removed.

    Raises CacheInUseError if the cache is busy (refcount > 0) and force is False.
    """
    return ModelStore.purge_for_model_id(model_id, force=force)
