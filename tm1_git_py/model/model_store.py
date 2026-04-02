import json
import logging
import os
import sqlite3
import time
import hashlib
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional
import orjson

DEFAULT_BULK_INSERT_BATCH_SIZE = 10_000
DEFAULT_ITER_PROGRESS_EVERY = 100_000


logger = logging.getLogger(__name__)


def _json_dumps(value: Any, *, sort_keys: bool = False) -> str:
    option = orjson.OPT_SORT_KEYS if sort_keys else 0
    return orjson.dumps(value, option=option).decode("utf-8")


def _json_loads(raw: str) -> Any:
    return orjson.loads(raw)


class ModelStore:
    HASH_ALGO = "sha256-chain-v1"
    EMPTY_CONTENT_HASH = hashlib.sha256(b"").hexdigest()
    _instances: dict[str, "ModelStore"] = {}

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._anonymous_model_id: Optional[int] = None
        self._initialize()

    @classmethod
    def for_main_dir(cls, main_dir: Optional[str] = None) -> "ModelStore":
        if main_dir:
            base_dir = Path(main_dir).expanduser().resolve()
        else:
            base_dir = Path.cwd().resolve()
        db_path = str(base_dir / ".tm1gitpy" / "model_store.sqlite")
        abs_path = os.path.abspath(db_path)
        existing = cls._instances.get(abs_path)
        if existing is not None:
            return existing
        created = cls(abs_path)
        cls._instances[abs_path] = created
        return created

    @classmethod
    def for_model_dir(cls, model_dir: str) -> "ModelStore":
        # Backward-compatible alias; in tests model_dir acts as the tool main directory.
        return cls.for_main_dir(model_dir)

    def _initialize(self) -> None:
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._create_schema()
        self._conn.commit()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                model_id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_config_name TEXT NULL,
                export_path TEXT NULL UNIQUE,
                created_at_ns INTEGER NOT NULL,
                updated_at_ns INTEGER NOT NULL
            )
            """
        )
        model_columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(models)").fetchall()
        }
        if "export_path" not in model_columns:
            self._conn.execute("ALTER TABLE models ADD COLUMN export_path TEXT NULL")
            self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_models_export_path ON models(export_path)")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL REFERENCES models(model_id) ON DELETE CASCADE,
                dimension_name TEXT NOT NULL,
                hierarchy_name TEXT NOT NULL,
                object_type TEXT NOT NULL,
                etag TEXT NULL,
                filter_rules_json JSONB NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                hash_algo TEXT NOT NULL,
                source_json_mtime_ns INTEGER NULL,
                updated_at_ns INTEGER NOT NULL,
                UNIQUE(model_id, dimension_name, hierarchy_name, object_type)
            )
            """
        )
        group_columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(groups)").fetchall()
        }
        if "etag" not in group_columns:
            self._conn.execute("ALTER TABLE groups ADD COLUMN etag TEXT NULL")
        if "filter_rules_json" not in group_columns:
            self._conn.execute("ALTER TABLE groups ADD COLUMN filter_rules_json JSONB NULL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS objects (
                group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                identity_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(group_id, seq)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_objects_group_identity ON objects(group_id, identity_key, seq)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_groups_lookup ON groups(model_id, dimension_name, hierarchy_name, object_type)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_models_server ON models(server_config_name)"
        )
        self._conn.commit()

    @staticmethod
    def _normalize_export_path(export_path: str) -> str:
        return str(Path(export_path).expanduser().resolve())

    def _create_model(self, server_config_name: Optional[str], export_path: Optional[str] = None) -> int:
        now = time.time_ns()
        cursor = self._conn.execute(
            "INSERT INTO models(server_config_name, export_path, created_at_ns, updated_at_ns) VALUES (?, ?, ?, ?)",
            (server_config_name, export_path, now, now),
        )
        model_id = int(cursor.lastrowid)
        return model_id

    def _delete_model(self, model_id: int) -> None:
        self._conn.execute("DELETE FROM models WHERE model_id=?", (model_id,))

    def _duplicate_model_data(self, source_model_id: int, target_model_id: int) -> None:
        group_rows = self._conn.execute(
            """
            SELECT group_id, dimension_name, hierarchy_name, object_type, etag, filter_rules_json, row_count, content_hash, hash_algo, source_json_mtime_ns, updated_at_ns
            FROM groups WHERE model_id=?
            """,
            (source_model_id,),
        ).fetchall()
        old_to_new_group: dict[int, int] = {}
        for group in group_rows:
            cursor = self._conn.execute(
                """
                INSERT INTO groups(model_id, dimension_name, hierarchy_name, object_type, etag, filter_rules_json, row_count, content_hash, hash_algo, source_json_mtime_ns, updated_at_ns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_model_id,
                    group["dimension_name"],
                    group["hierarchy_name"],
                    group["object_type"],
                    group["etag"],
                    group["filter_rules_json"],
                    group["row_count"],
                    group["content_hash"],
                    group["hash_algo"],
                    group["source_json_mtime_ns"],
                    group["updated_at_ns"],
                ),
            )
            old_to_new_group[int(group["group_id"])] = int(cursor.lastrowid)
        for old_group_id, new_group_id in old_to_new_group.items():
            self._conn.execute(
                """
                INSERT INTO objects(group_id, seq, identity_key, payload_json)
                SELECT ?, seq, identity_key, payload_json
                FROM objects
                WHERE group_id=?
                ORDER BY seq
                """,
                (new_group_id, old_group_id),
            )

    def resolve_model_for_export(self, server_config_name: str, export_path: str) -> int:
        export_path_n = self._normalize_export_path(export_path)
        with self.tx():
            row = self._conn.execute(
                "SELECT model_id, server_config_name FROM models WHERE export_path=?",
                (export_path_n,),
            ).fetchone()
            if row is not None:
                existing_model_id = int(row["model_id"])
                existing_server = str(row["server_config_name"] or "")
                if existing_server != server_config_name:
                    self._delete_model(existing_model_id)
                    model_id = self._create_model(server_config_name, export_path_n)
                    return model_id
                self._conn.execute(
                    "UPDATE models SET updated_at_ns=? WHERE model_id=?",
                    (time.time_ns(), existing_model_id),
                )
                return existing_model_id

            source = self._conn.execute(
                """
                SELECT model_id FROM models
                WHERE server_config_name=? AND export_path IS NOT NULL
                ORDER BY updated_at_ns DESC, model_id DESC
                LIMIT 1
                """,
                (server_config_name,),
            ).fetchone()
            if source is None:
                model_id = self._create_model(server_config_name, export_path_n)
                return model_id
            source_model_id = int(source["model_id"])
            model_id = self._create_model(server_config_name, export_path_n)
            self._duplicate_model_data(source_model_id, model_id)
            return model_id

    def resolve_model_for_deserialize(self, export_path: str) -> int:
        export_path_n = self._normalize_export_path(export_path)
        with self.tx():
            row = self._conn.execute(
                "SELECT model_id FROM models WHERE export_path=?",
                (export_path_n,),
            ).fetchone()
            if row is not None:
                return int(row["model_id"])
            model_id = self._create_model(None, export_path_n)
            return model_id

    def _ensure_anonymous_model_id(self) -> int:
        if self._anonymous_model_id is not None:
            return self._anonymous_model_id
        with self.tx():
            model_id = self._create_model("__anonymous__", None)
        self._anonymous_model_id = model_id
        return model_id


    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            self._conn.execute("BEGIN")
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def ensure_group(
        self,
        dimension_name: str,
        hierarchy_name: str,
        object_type: str,
        *,
        model_id: Optional[int] = None,
    ) -> int:
        model_id = model_id if model_id is not None else self._ensure_anonymous_model_id()
        now = time.time_ns()
        self._conn.execute(
            """
            INSERT INTO groups(model_id, dimension_name, hierarchy_name, object_type, etag, filter_rules_json, row_count, content_hash, hash_algo, source_json_mtime_ns, updated_at_ns)
            VALUES (?, ?, ?, ?, NULL, NULL, 0, ?, ?, NULL, ?)
            ON CONFLICT(model_id, dimension_name, hierarchy_name, object_type) DO NOTHING
            """,
            (model_id, dimension_name, hierarchy_name, object_type, self.EMPTY_CONTENT_HASH, self.HASH_ALGO, now),
        )
        row = self._conn.execute(
            """
            SELECT group_id FROM groups
            WHERE model_id=? AND dimension_name=? AND hierarchy_name=? AND object_type=?
            """,
            (model_id, dimension_name, hierarchy_name, object_type),
        ).fetchone()
        self._conn.commit()
        if row is None:
            raise RuntimeError("Failed to create or resolve group id.")
        return int(row["group_id"])

    def get_hierarchy_etag(self, model_id: int, dimension_name: str, hierarchy_name: str) -> Optional[str]:
        row = self._conn.execute(
            """
            SELECT etag
            FROM groups
            WHERE model_id=? AND dimension_name=? AND hierarchy_name=?
            ORDER BY group_id
            LIMIT 1
            """,
            (model_id, dimension_name, hierarchy_name),
        ).fetchone()
        if row is None:
            return None
        value = row["etag"]
        return str(value) if value is not None else None

    def set_group_etag(self, group_id: int, etag: Optional[str]) -> None:
        self._conn.execute(
            "UPDATE groups SET etag=?, updated_at_ns=? WHERE group_id=?",
            (etag, time.time_ns(), group_id),
        )
        self._conn.commit()

    def group_etag(self, group_id: int) -> Optional[str]:
        row = self._conn.execute(
            "SELECT etag FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        value = row["etag"]
        return str(value) if value is not None else None

    def set_group_filter_rules(self, group_id: int, filter_rules: list[str]) -> None:
        payload = _json_dumps(list(filter_rules))
        self._conn.execute(
            "UPDATE groups SET filter_rules_json=?, updated_at_ns=? WHERE group_id=?",
            (payload, time.time_ns(), group_id),
        )
        self._conn.commit()

    def group_filter_rules(self, group_id: int) -> list[str]:
        row = self._conn.execute(
            "SELECT filter_rules_json FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            return []
        raw = row["filter_rules_json"]
        if raw in (None, ""):
            return []
        try:
            parsed = _json_loads(str(raw))
        except (TypeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed]

    def get_group_reuse_metadata(
        self,
        *,
        model_id: int,
        dimension_name: str,
        hierarchy_name: str,
        object_type: str,
    ) -> tuple[Optional[str], list[str]]:
        row = self._conn.execute(
            """
            SELECT etag, filter_rules_json
            FROM groups
            WHERE model_id=? AND dimension_name=? AND hierarchy_name=? AND object_type=?
            LIMIT 1
            """,
            (model_id, dimension_name, hierarchy_name, object_type),
        ).fetchone()
        if row is None:
            return None, []
        etag = row["etag"]
        raw_rules = row["filter_rules_json"]
        if raw_rules in (None, ""):
            return (str(etag) if etag is not None else None, [])
        try:
            parsed = _json_loads(str(raw_rules))
        except (TypeError, ValueError):
            parsed = []
        if not isinstance(parsed, list):
            parsed = []
        return (
            str(etag) if etag is not None else None,
            [str(item) for item in parsed],
        )

    def _load_group_state(self, group_id: int) -> tuple[int, str]:
        row = self._conn.execute(
            "SELECT row_count, content_hash FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            return 0, self.EMPTY_CONTENT_HASH
        return int(row["row_count"]), str(row["content_hash"])

    @staticmethod
    def _payload_to_json(payload: dict[str, Any]) -> str:
        return _json_dumps(payload, sort_keys=True)

    @classmethod
    def _hash_line(cls, previous_hash: str, line_bytes: bytes) -> str:
        hasher = hashlib.sha256()
        hasher.update(bytes.fromhex(previous_hash))
        hasher.update(line_bytes)
        return hasher.hexdigest()

    def append_payloads(
        self,
        group_id: int,
        payloads: Iterable[dict[str, Any]],
        identity_key_fn: Callable[[dict[str, Any]], str],
        *,
        batch_size: int = DEFAULT_BULK_INSERT_BATCH_SIZE,
    ) -> int:
        row_count, content_hash = self._load_group_state(group_id)
        next_seq = row_count
        inserted = 0
        batch_size = max(1, int(batch_size))
        with self.tx():
            pending_rows: list[tuple[int, int, str, str]] = []
            for payload in payloads:
                payload_json = self._payload_to_json(payload)
                line = payload_json + "\n"
                content_hash = self._hash_line(content_hash, line.encode("utf-8"))
                identity_key = identity_key_fn(payload)
                pending_rows.append((group_id, next_seq, identity_key, payload_json))
                next_seq += 1
                inserted += 1
                if len(pending_rows) >= batch_size:
                    self._conn.executemany(
                        "INSERT INTO objects(group_id, seq, identity_key, payload_json) VALUES (?, ?, ?, ?)",
                        pending_rows,
                    )
                    pending_rows.clear()
            if pending_rows:
                self._conn.executemany(
                    "INSERT INTO objects(group_id, seq, identity_key, payload_json) VALUES (?, ?, ?, ?)",
                    pending_rows,
                )
            if inserted:
                now = time.time_ns()
                row_count = row_count + inserted
                self._conn.execute(
                    """
                    UPDATE groups
                    SET row_count=?, content_hash=?, hash_algo=?, updated_at_ns=?
                    WHERE group_id=?
                    """,
                    (row_count, content_hash, self.HASH_ALGO, now, group_id),
                )
        return inserted

    def replace_group_payloads(
        self,
        group_id: int,
        payloads: Iterable[dict[str, Any]],
        identity_key_fn: Callable[[dict[str, Any]], str],
        *,
        source_json_mtime_ns: Optional[int] = None,
    ) -> int:
        row_count = 0
        content_hash = self.EMPTY_CONTENT_HASH
        now = time.time_ns()
        with self.tx():
            self._conn.execute("DELETE FROM objects WHERE group_id=?", (group_id,))
            for payload in payloads:
                payload_json = self._payload_to_json(payload)
                line = payload_json + "\n"
                content_hash = self._hash_line(content_hash, line.encode("utf-8"))
                identity_key = identity_key_fn(payload)
                self._conn.execute(
                    "INSERT INTO objects(group_id, seq, identity_key, payload_json) VALUES (?, ?, ?, ?)",
                    (group_id, row_count, identity_key, payload_json),
                )
                row_count += 1
            self._conn.execute(
                """
                UPDATE groups
                SET row_count=?, content_hash=?, hash_algo=?,
                    source_json_mtime_ns=COALESCE(?, source_json_mtime_ns), updated_at_ns=?
                WHERE group_id=?
                """,
                (
                    row_count,
                    content_hash,
                    self.HASH_ALGO,
                    source_json_mtime_ns,
                    now,
                    group_id,
                ),
            )
        return row_count

    def iter_payloads(
        self,
        group_id: int,
        *,
        ordered_by_identity: bool = False,
        progress_label: Optional[str] = None,
        progress_every: int = DEFAULT_ITER_PROGRESS_EVERY,
    ) -> Iterator[dict[str, Any]]:
        order_clause = "identity_key, seq" if ordered_by_identity else "seq"
        total = self.row_count(group_id)
        if progress_label:
            logger.info(
                "Starting DB payload stream '%s' group_id=%d total=%d ordered_by_identity=%s progress_every=%d",
                progress_label,
                group_id,
                total,
                ordered_by_identity,
                progress_every,
            )
        next_log_at = max(1, progress_every)
        emitted = 0
        cursor = self._conn.execute(
            f"SELECT payload_json FROM objects WHERE group_id=? ORDER BY {order_clause}",
            (group_id,),
        )
        for row in cursor:
            emitted += 1
            if progress_label and emitted >= next_log_at:
                logger.info(
                    "DB payload stream progress '%s' group_id=%d emitted=%d/%d",
                    progress_label,
                    group_id,
                    emitted,
                    total,
                )
                while emitted >= next_log_at:
                    next_log_at += max(1, progress_every)
            yield _json_loads(str(row["payload_json"]))
        if progress_label:
            logger.info(
                "Completed DB payload stream '%s' group_id=%d emitted=%d/%d",
                progress_label,
                group_id,
                emitted,
                total,
            )

    def iter_payload_json_strings(
        self,
        group_id: int,
        *,
        ordered_by_identity: bool = False,
        progress_label: Optional[str] = None,
        progress_every: int = DEFAULT_ITER_PROGRESS_EVERY,
    ) -> Iterator[str]:
        order_clause = "identity_key, seq" if ordered_by_identity else "seq"
        total = self.row_count(group_id)
        if progress_label:
            logger.info(
                "Starting DB JSON stream '%s' group_id=%d total=%d ordered_by_identity=%s progress_every=%d",
                progress_label,
                group_id,
                total,
                ordered_by_identity,
                progress_every,
            )
        next_log_at = max(1, progress_every)
        emitted = 0
        cursor = self._conn.execute(
            f"SELECT payload_json FROM objects WHERE group_id=? ORDER BY {order_clause}",
            (group_id,),
        )
        for row in cursor:
            emitted += 1
            if progress_label and emitted >= next_log_at:
                logger.info(
                    "DB JSON stream progress '%s' group_id=%d emitted=%d/%d",
                    progress_label,
                    group_id,
                    emitted,
                    total,
                )
                while emitted >= next_log_at:
                    next_log_at += max(1, progress_every)
            yield str(row["payload_json"])
        if progress_label:
            logger.info(
                "Completed DB JSON stream '%s' group_id=%d emitted=%d/%d",
                progress_label,
                group_id,
                emitted,
                total,
            )

    def row_count(self, group_id: int) -> int:
        row = self._conn.execute("SELECT row_count FROM groups WHERE group_id=?", (group_id,)).fetchone()
        return int(row["row_count"]) if row is not None else 0

    def content_signature(self, group_id: int) -> Optional[tuple[int, str]]:
        row = self._conn.execute(
            "SELECT row_count, content_hash FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        return int(row["row_count"]), str(row["content_hash"])

    def set_source_json_mtime_ns(self, group_id: int, source_json_mtime_ns: int) -> None:
        self._conn.execute(
            "UPDATE groups SET source_json_mtime_ns=?, updated_at_ns=? WHERE group_id=?",
            (int(source_json_mtime_ns), time.time_ns(), group_id),
        )
        self._conn.commit()

    def source_json_mtime_ns(self, group_id: int) -> Optional[int]:
        row = self._conn.execute(
            "SELECT source_json_mtime_ns FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        value = row["source_json_mtime_ns"]
        return int(value) if value is not None else None
