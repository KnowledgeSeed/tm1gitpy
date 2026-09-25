"""Regression coverage for issue #39: close+purge cache cleanup API."""

from pathlib import Path

import pytest

from tm1_git_py import CacheInUseError, purge_changeset_cache, purge_model_cache
from tm1_git_py.db._worker_db import WorkerDBRegistry
from tm1_git_py.db.changeset_store import ChangesetStore
from tm1_git_py.db.model_store import ModelStore


def _artifacts(db_path: str) -> list[Path]:
    path = Path(db_path)
    return [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]


class TestWorkerDBRegistryIsBusy:
    def test_is_busy_reflects_live_lease(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "busy.sqlite")
        lease = WorkerDBRegistry.acquire(db_path)
        try:
            assert WorkerDBRegistry.is_busy(db_path) is True
        finally:
            lease.release()
        assert WorkerDBRegistry.is_busy(db_path) is False
        WorkerDBRegistry.force_close(db_path)

    def test_is_busy_false_for_unknown_path(self, tmp_path: Path) -> None:
        assert WorkerDBRegistry.is_busy(str(tmp_path / "never-created.sqlite")) is False


class TestModelStorePurge:
    def test_purge_deletes_db_and_wal_shm_sidecars(self, tmp_path: Path) -> None:
        store = ModelStore.for_db_path(str(tmp_path / "model.sqlite"))
        artifacts = _artifacts(store.db_path)
        assert any(p.exists() for p in artifacts)
        store.close()

        removed = ModelStore.purge_for_db_path(store.db_path)

        assert removed is True
        assert all(not p.exists() for p in artifacts)

    def test_purge_is_idempotent(self, tmp_path: Path) -> None:
        store = ModelStore.for_db_path(str(tmp_path / "model.sqlite"))
        store.close()
        assert ModelStore.purge_for_db_path(store.db_path) is True

        removed_again = ModelStore.purge_for_db_path(store.db_path)

        assert removed_again is False

    def test_purge_on_never_created_cache_is_a_no_op(self, tmp_path: Path) -> None:
        removed = ModelStore.purge_for_db_path(str(tmp_path / "does-not-exist.sqlite"))

        assert removed is False

    def test_purge_refuses_while_busy_without_force(self, tmp_path: Path) -> None:
        store = ModelStore.for_db_path(str(tmp_path / "model.sqlite"))

        with pytest.raises(CacheInUseError):
            ModelStore.purge_for_db_path(store.db_path)

        assert Path(store.db_path).exists()
        store.close()

    def test_purge_with_force_closes_and_deletes_while_busy(self, tmp_path: Path) -> None:
        store = ModelStore.for_db_path(str(tmp_path / "model.sqlite"))

        removed = ModelStore.purge_for_db_path(store.db_path, force=True)

        assert removed is True
        assert not Path(store.db_path).exists()

    def test_purge_for_model_id_matches_purge_for_db_path(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        store = ModelStore.for_model_id("model-a")
        store.close()

        removed = ModelStore.purge_for_model_id("model-a")

        assert removed is True
        assert not Path(ModelStore.path_for(model_id="model-a")).exists()

    def test_purging_one_model_does_not_affect_another_open_cache(self, tmp_path: Path) -> None:
        store_a = ModelStore.for_db_path(str(tmp_path / "model-a.sqlite"))
        store_b = ModelStore.for_db_path(str(tmp_path / "model-b.sqlite"))
        store_a.close()

        ModelStore.purge_for_db_path(store_a.db_path)

        assert Path(store_b.db_path).exists()
        assert WorkerDBRegistry.is_busy(store_b.db_path) is True
        store_b.close()
        WorkerDBRegistry.force_close(store_b.db_path)

    def test_model_store_recreate_after_purge_works_end_to_end(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "model.sqlite")
        store = ModelStore.for_db_path(db_path)
        group_id = store.ensure_group("Dim1", "Dim1", "element")
        store.append_payloads(group_id, [{"Name": "Elem1", "Type": "Numeric"}])
        store.close()

        ModelStore.purge_for_db_path(db_path)

        reopened = ModelStore.for_db_path(db_path)
        new_group_id = reopened.ensure_group("Dim1", "Dim1", "element")
        assert reopened.row_count(new_group_id) == 0
        reopened.close()
        WorkerDBRegistry.force_close(db_path)


class TestChangesetStorePurge:
    def test_purge_deletes_db_and_wal_shm_sidecars(self, tmp_path: Path) -> None:
        store = ChangesetStore.for_changeset_id(changeset_id="cs-1", base_dir=str(tmp_path))
        artifacts = _artifacts(str(store.db_path))
        assert any(p.exists() for p in artifacts)
        store.close()

        removed = ChangesetStore.purge(changeset_id="cs-1", base_dir=str(tmp_path))

        assert removed is True
        assert all(not p.exists() for p in artifacts)

    def test_purge_is_idempotent(self, tmp_path: Path) -> None:
        store = ChangesetStore.for_changeset_id(changeset_id="cs-1", base_dir=str(tmp_path))
        store.close()
        assert ChangesetStore.purge(changeset_id="cs-1", base_dir=str(tmp_path)) is True

        removed_again = ChangesetStore.purge(changeset_id="cs-1", base_dir=str(tmp_path))

        assert removed_again is False

    def test_purge_on_never_created_cache_is_a_no_op(self, tmp_path: Path) -> None:
        removed = ChangesetStore.purge(changeset_id="never-created", base_dir=str(tmp_path))

        assert removed is False

    def test_purge_refuses_while_busy_without_force(self, tmp_path: Path) -> None:
        store = ChangesetStore.for_changeset_id(changeset_id="cs-1", base_dir=str(tmp_path))

        with pytest.raises(CacheInUseError):
            ChangesetStore.purge(changeset_id="cs-1", base_dir=str(tmp_path))

        assert store.db_path.exists()
        store.close()

    def test_purge_with_force_closes_and_deletes_while_busy(self, tmp_path: Path) -> None:
        store = ChangesetStore.for_changeset_id(changeset_id="cs-1", base_dir=str(tmp_path))

        removed = ChangesetStore.purge(changeset_id="cs-1", base_dir=str(tmp_path), force=True)

        assert removed is True
        assert not store.db_path.exists()

    def test_purging_one_changeset_does_not_affect_another_open_cache(self, tmp_path: Path) -> None:
        store_a = ChangesetStore.for_changeset_id(changeset_id="cs-a", base_dir=str(tmp_path))
        store_b = ChangesetStore.for_changeset_id(changeset_id="cs-b", base_dir=str(tmp_path))
        store_a.close()

        ChangesetStore.purge(changeset_id="cs-a", base_dir=str(tmp_path))

        assert store_b.db_path.exists()
        assert WorkerDBRegistry.is_busy(str(store_b.db_path)) is True
        store_b.close()
        WorkerDBRegistry.force_close(str(store_b.db_path))

    def test_changeset_store_recreate_after_purge_works_end_to_end(self, tmp_path: Path) -> None:
        store = ChangesetStore.for_changeset_id(changeset_id="cs-1", base_dir=str(tmp_path))
        store.replace_rows(
            [
                {
                    "seq": 0,
                    "change_type": "add",
                    "object_type": "process",
                    "uri": "Processes('P1')",
                    "apply": True,
                    "body_json": "{}",
                    "type_rank": 0,
                    "precedence_rank": 0,
                    "body_name": "P1",
                }
            ]
        )
        assert store.count_rows() == 1
        store.close()

        ChangesetStore.purge(changeset_id="cs-1", base_dir=str(tmp_path))

        reopened = ChangesetStore.for_changeset_id(changeset_id="cs-1", base_dir=str(tmp_path))
        assert reopened.count_rows() == 0
        reopened.close()
        WorkerDBRegistry.force_close(str(reopened.db_path))


class TestTopLevelPurgeExports:
    def test_purge_changeset_cache_delegates_to_changeset_store(self, tmp_path: Path) -> None:
        store = ChangesetStore.for_changeset_id(changeset_id="cs-top", base_dir=str(tmp_path))
        store.close()

        removed = purge_changeset_cache("cs-top", base_dir=str(tmp_path))

        assert removed is True
        assert not store.db_path.exists()

    def test_purge_changeset_cache_raises_cache_in_use_error_when_busy(self, tmp_path: Path) -> None:
        store = ChangesetStore.for_changeset_id(changeset_id="cs-top-busy", base_dir=str(tmp_path))

        with pytest.raises(CacheInUseError):
            purge_changeset_cache("cs-top-busy", base_dir=str(tmp_path))

        store.close()

    def test_purge_model_cache_delegates_to_model_store(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        store = ModelStore.for_model_id("model-top")
        store.close()

        removed = purge_model_cache("model-top")

        assert removed is True
        assert not Path(ModelStore.path_for(model_id="model-top")).exists()
