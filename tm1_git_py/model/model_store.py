import logging
import os
import time
import hashlib
import threading
import re
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Optional
import orjson
import sqlite3


DEFAULT_BULK_INSERT_BATCH_SIZE = 10_000
DEFAULT_ITER_PROGRESS_EVERY = 100_000
DEFAULT_HASH_FETCH_BATCH_SIZE = 5_000
DEFAULT_PARALLEL_HASH_CHUNK_SIZE = 200_000


logger = logging.getLogger(__name__)

    
def _json_dumps(value: Any, *, sort_keys: bool = False) -> str:
    option = orjson.OPT_SORT_KEYS if sort_keys else 0
    return orjson.dumps(value, option=option).decode("utf-8")


def _json_loads(raw: str) -> Any:
    return orjson.loads(raw)


def _identity_key_query_for_type(payload_table: str, normalized: str) -> str:
    if normalized in ("element", "elements"):
        return (
            f"SELECT COALESCE(Name, ''), COALESCE(Type, ''), seq FROM {payload_table} "
            "WHERE group_id=? ORDER BY COALESCE(Name, ''), seq LIMIT 1 OFFSET ?"
        )
    if normalized in ("edge", "edges"):
        return (
            f"SELECT COALESCE(ParentName, ''), COALESCE(ComponentName, ''), "
            f"COALESCE(CAST(Weight AS TEXT), ''), seq FROM {payload_table} "
            "WHERE group_id=? ORDER BY COALESCE(ParentName, ''), COALESCE(ComponentName, ''), seq LIMIT 1 OFFSET ?"
        )
    if normalized in ("subset", "subsets"):
        return (
            f"SELECT COALESCE(Name, ''), seq FROM {payload_table} "
            "WHERE group_id=? ORDER BY COALESCE(Name, ''), seq LIMIT 1 OFFSET ?"
        )
    raise ValueError(f"Unsupported group object type for parallel identity hashing: '{normalized}'")


def _identity_chunk_query_for_type(payload_table: str, normalized: str) -> str:
    if normalized in ("element", "elements"):
        return (
            f"SELECT COALESCE(Name, ''), COALESCE(Type, ''), seq FROM {payload_table} "
            "WHERE group_id=? ORDER BY COALESCE(Name, ''), seq LIMIT ? OFFSET ?"
        )
    if normalized in ("edge", "edges"):
        return (
            f"SELECT COALESCE(ParentName, ''), COALESCE(ComponentName, ''), "
            f"COALESCE(CAST(Weight AS TEXT), ''), seq FROM {payload_table} "
            "WHERE group_id=? ORDER BY COALESCE(ParentName, ''), COALESCE(ComponentName, ''), seq LIMIT ? OFFSET ?"
        )
    if normalized in ("subset", "subsets"):
        return (
            f"SELECT COALESCE(Name, ''), seq FROM {payload_table} "
            "WHERE group_id=? ORDER BY COALESCE(Name, ''), seq LIMIT ? OFFSET ?"
        )
    raise ValueError(f"Unsupported group object type for parallel identity hashing: '{normalized}'")


def _identity_line_for_type(normalized: str, row: tuple[Any, ...]) -> str:
    if normalized in ("element", "elements"):
        return f"{row[0]}\x1f{row[1]}"
    if normalized in ("edge", "edges"):
        return f"{row[0]}\x1f{row[1]}\x1f{row[2]}"
    if normalized in ("subset", "subsets"):
        return str(row[0])
    raise ValueError(f"Unsupported group object type for parallel identity hashing: '{normalized}'")



def _parallel_identity_chunk_hash_worker(
    job: dict[str, Any],
    on_chunk_start: Optional[Callable[[int], None]] = None,
    on_chunk_end: Optional[Callable[[int], None]] = None,
) -> tuple[int, int, str]:
    db_path = str(job["db_path"])
    payload_table = str(job["payload_table"])
    normalized = str(job["normalized"])
    group_id = int(job["group_id"])
    chunk_idx = int(job["chunk_idx"])
    chunk_offset = max(0, int(job.get("chunk_offset", 0)))
    chunk_limit = max(0, int(job.get("chunk_limit", 0)))
    fetch_batch_size = max(1, int(job.get("fetch_batch_size", DEFAULT_HASH_FETCH_BATCH_SIZE)))

    if on_chunk_start is not None:
        on_chunk_start(fetch_batch_size)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        sql = _identity_chunk_query_for_type(payload_table, normalized)
        cursor = conn.execute(sql, (group_id, chunk_limit, chunk_offset))
        hasher = hashlib.sha256()
        row_count = 0
        while True:
            rows = cursor.fetchmany(fetch_batch_size)
            if not rows:
                break
            for row in rows:
                hasher.update((_identity_line_for_type(normalized, tuple(row)) + "\n").encode("utf-8"))
                row_count += 1
        if on_chunk_end is not None:
            on_chunk_end(row_count)
        return chunk_idx, row_count, hasher.hexdigest()
    finally:
        conn.close()


class ModelStore:
    PARALLEL_HASH_ALGO = "sha256-tree-v1"
    HASH_ALGO = PARALLEL_HASH_ALGO
    EMPTY_CONTENT_HASH = hashlib.sha256(b"").hexdigest()
    _instances: dict[tuple[str, int], "ModelStore"] = {}

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, timeout=30.0)
        if hasattr(self._conn, "row_factory"):
            try:
                self._conn.row_factory = sqlite3.Row
            except Exception:
                pass
        self._tx_depth = 0
        self._initialize()

    @staticmethod
    def _cell(row: Any, key: str, index: int) -> Any:
        if row is None:
            return None
        try:
            return row[key]
        except Exception:
            return row[index]

    @classmethod
    def _db_path_for_model_id(cls, model_id: str) -> str:
        normalized_model_id = re.sub(r"[^A-Za-z0-9._-]+", "_", (model_id or "").strip())
        if not normalized_model_id:
            raise ValueError("model_id must not be empty")
        return str(Path.cwd().resolve() / ".tm1gitpy" / ".cache" / f"{normalized_model_id}.sqlite")

    @classmethod
    def for_model_id(cls, model_id: str) -> "ModelStore":
        db_path = cls._db_path_for_model_id(model_id)
        abs_path = os.path.abspath(db_path)
        key = (abs_path, threading.get_ident())
        existing = cls._instances.get(key)
        if existing is not None:
            return existing
        created = cls(abs_path)
        cls._instances[key] = created
        return created

    def _initialize(self) -> None:
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA busy_timeout=30000")
        if self._schema_needs_reset():
            self._reset_schema()
        self._create_schema()
        self._conn.commit()

    def _table_columns(self, table_name: str) -> list[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [str(self._cell(row, "name", 1)) for row in rows]

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _schema_needs_reset(self) -> bool:
        # Fresh DB: create schema normally.
        if not self._table_exists("groups"):
            return False
        expected_groups = {
            "group_id",
            "dimension_name",
            "hierarchy_name",
            "object_type",
            "etag",
            "filter_rules_json",
            "row_count",
            "content_hash",
            "hash_algo",
            "source_json_mtime_ns",
            "updated_at_ns",
        }
        actual_groups = set(self._table_columns("groups"))
        if actual_groups != expected_groups:
            return True
        expected_object_tables = {
            "element_objects": {"group_id", "seq", "Name", "Type"},
            "edge_objects": {"group_id", "seq", "ParentName", "ComponentName", "Weight"},
            "subset_objects": {"group_id", "seq", "Name", "Expression"},
        }
        for table_name, expected_cols in expected_object_tables.items():
            if not self._table_exists(table_name):
                return True
            if set(self._table_columns(table_name)) != expected_cols:
                return True
        # Legacy table indicates pre-refactor layout; reset instead of migrating.
        if self._table_exists("objects"):
            return True
        return False

    def _reset_schema(self) -> None:
        logger.debug("ModelStore schema mismatch detected, discarding previous model store at '%s'", self.db_path)
        with self.tx():
            self._conn.execute("DROP TABLE IF EXISTS objects")
            self._conn.execute("DROP TABLE IF EXISTS edge_objects")
            self._conn.execute("DROP TABLE IF EXISTS element_objects")
            self._conn.execute("DROP TABLE IF EXISTS subset_objects")
            self._conn.execute("DROP TABLE IF EXISTS groups")

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                UNIQUE(dimension_name, hierarchy_name, object_type)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS element_objects (
                group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                Name TEXT NULL,
                Type TEXT NULL,
                PRIMARY KEY(group_id, seq)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS edge_objects (
                group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                ParentName TEXT NULL,
                ComponentName TEXT NULL,
                Weight NUMERIC NULL,
                PRIMARY KEY(group_id, seq)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subset_objects (
                group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                Name TEXT NULL,
                Expression TEXT NULL,
                PRIMARY KEY(group_id, seq)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_element_objects_identity ON element_objects(
                group_id,
                Name
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_edge_objects_identity ON edge_objects(
                group_id,
                ParentName,
                ComponentName
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_subset_objects_identity ON subset_objects(
                group_id,
                Name
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_groups_lookup ON groups(dimension_name, hierarchy_name, object_type)"
        )
        self._conn.commit()

    @staticmethod
    def _object_table_for_type(object_type: str) -> str:
        normalized = str(object_type).strip().lower()
        if normalized in ("element", "elements"):
            return "element_objects"
        if normalized in ("edge", "edges"):
            return "edge_objects"
        if normalized in ("subset", "subsets"):
            return "subset_objects"
        raise ValueError(f"Unsupported group object type for payload storage: '{object_type}'")

    def _object_type_for_group(self, group_id: int) -> str:
        row = self._conn.execute(
            "SELECT object_type FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown group_id={group_id}")
        return str(self._cell(row, "object_type", 0))

    def _object_table_for_group(self, group_id: int) -> str:
        return self._object_table_for_type(self._object_type_for_group(group_id))

    @staticmethod
    def _object_type_normalized(object_type: str) -> str:
        return str(object_type).strip().lower()

    def _object_type_normalized_for_group(self, group_id: int) -> str:
        return self._object_type_normalized(self._object_type_for_group(group_id))

    def _actual_row_count(self, group_id: int) -> int:
        table_name = self._object_table_for_group(group_id)
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE group_id=?",
            (group_id,),
        ).fetchone()
        return int(self._cell(row, "COUNT(*)", 0)) if row is not None else 0

    def _identity_order_clause_for_type(self, normalized: str) -> str:
        if normalized in ("element", "elements"):
            return "Name, Type"
        if normalized in ("edge", "edges"):
            return "ParentName, ComponentName"
        if normalized in ("subset", "subsets"):
            return "Name"
        raise ValueError(f"Unsupported group object type for identity ordering: '{normalized}'")

    @staticmethod
    def _parallel_identity_order_clause_for_type(normalized: str) -> str:
        if normalized in ("element", "elements"):
            return "COALESCE(Name, ''), seq"
        if normalized in ("edge", "edges"):
            return "COALESCE(ParentName, ''), COALESCE(ComponentName, ''), seq"
        raise ValueError(f"Unsupported group object type for parallel identity ordering: '{normalized}'")

    def _payload_columns_for_type(self, normalized: str) -> tuple[str, ...]:
        if normalized in ("element", "elements"):
            return ("Name", "Type")
        if normalized in ("edge", "edges"):
            return ("ParentName", "ComponentName", "Weight")
        if normalized in ("subset", "subsets"):
            return ("Name", "Expression")
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    def _payload_values_for_type(self, normalized: str, payload: dict[str, Any]) -> tuple[Any, ...]:
        if normalized in ("element", "elements"):
            return (
                payload.get("Name") or payload.get("name"),
                payload.get("Type") or payload.get("type"),
            )
        if normalized in ("edge", "edges"):
            weight = payload.get("Weight")
            if weight is None:
                weight = payload.get("weight")
            return (
                payload.get("ParentName") or payload.get("parentName") or payload.get("parent"),
                payload.get("ComponentName") or payload.get("componentName") or payload.get("name") or payload.get("Name"),
                weight,
            )
        if normalized in ("subset", "subsets"):
            return (
                payload.get("Name") or payload.get("name"),
                payload.get("Expression") or payload.get("expression"),
            )
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    def _payload_dict_from_row_for_type(self, normalized: str, row: Any) -> dict[str, Any]:
        if normalized in ("element", "elements"):
            return {
                "Name": self._cell(row, "Name", 0),
                "Type": self._cell(row, "Type", 1),
            }
        if normalized in ("edge", "edges"):
            return {
                "ParentName": self._cell(row, "ParentName", 0),
                "ComponentName": self._cell(row, "ComponentName", 1),
                "Weight": self._cell(row, "Weight", 2),
            }
        if normalized in ("subset", "subsets"):
            return {
                "name": self._cell(row, "Name", 0),
                "expression": self._cell(row, "Expression", 1),
            }
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    @staticmethod
    def _value_json(value: Any) -> str:
        return orjson.dumps(value).decode("utf-8")

    def _payload_json_for_type(self, normalized: str, payload: dict[str, Any]) -> str:
        if normalized in ("element", "elements"):
            name = payload.get("Name") or payload.get("name")
            elem_type = payload.get("Type") or payload.get("type")
            return (
                "{"
                f"\"Name\":{self._value_json(name)},"
                f"\"Type\":{self._value_json(elem_type)}"
                "}"
            )
        if normalized in ("edge", "edges"):
            parent = payload.get("ParentName") or payload.get("parentName") or payload.get("parent")
            component = payload.get("ComponentName") or payload.get("componentName") or payload.get("name") or payload.get("Name")
            weight = payload.get("Weight")
            if weight is None:
                weight = payload.get("weight")
            return (
                "{"
                f"\"ComponentName\":{self._value_json(component)},"
                f"\"ParentName\":{self._value_json(parent)},"
                f"\"Weight\":{self._value_json(weight)}"
                "}"
            )
        if normalized in ("subset", "subsets"):
            name = payload.get("Name") or payload.get("name")
            expression = payload.get("Expression") or payload.get("expression")
            return (
                "{"
                f"\"expression\":{self._value_json(expression)},"
                f"\"name\":{self._value_json(name)}"
                "}"
            )
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    def _payload_json_from_row_for_type(self, normalized: str, row: Any) -> str:
        if normalized in ("element", "elements"):
            return (
                "{"
                f"\"Name\":{self._value_json(self._cell(row, 'Name', 0))},"
                f"\"Type\":{self._value_json(self._cell(row, 'Type', 1))}"
                "}"
            )
        if normalized in ("edge", "edges"):
            return (
                "{"
                f"\"ComponentName\":{self._value_json(self._cell(row, 'ComponentName', 1))},"
                f"\"ParentName\":{self._value_json(self._cell(row, 'ParentName', 0))},"
                f"\"Weight\":{self._value_json(self._cell(row, 'Weight', 2))}"
                "}"
            )
        if normalized in ("subset", "subsets"):
            return (
                "{"
                f"\"expression\":{self._value_json(self._cell(row, 'Expression', 1))},"
                f"\"name\":{self._value_json(self._cell(row, 'Name', 0))}"
                "}"
            )
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        # Use SAVEPOINT for all scopes to stay compatible with drivers that
        # may start implicit transactions.
        savepoint = f"sp_{self._tx_depth}_{time.time_ns()}"
        self._conn.execute(f"SAVEPOINT {savepoint}")
        self._tx_depth += 1
        try:
            yield self._conn
            self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        finally:
            self._tx_depth = max(0, self._tx_depth - 1)

    def ensure_group(
        self,
        dimension_name: str,
        hierarchy_name: str,
        object_type: str,
        *,
        model_id: Optional[str] = None,
    ) -> int:
        _ = model_id
        now = time.time_ns()
        self._conn.execute(
            """
            INSERT INTO groups(dimension_name, hierarchy_name, object_type, etag, filter_rules_json, row_count, content_hash, hash_algo, source_json_mtime_ns, updated_at_ns)
            VALUES (?, ?, ?, NULL, NULL, 0, ?, ?, NULL, ?)
            ON CONFLICT(dimension_name, hierarchy_name, object_type) DO NOTHING
            """,
            (dimension_name, hierarchy_name, object_type, self.EMPTY_CONTENT_HASH, self.HASH_ALGO, now),
        )
        row = self._conn.execute(
            """
            SELECT group_id FROM groups
            WHERE dimension_name=? AND hierarchy_name=? AND object_type=?
            """,
            (dimension_name, hierarchy_name, object_type),
        ).fetchone()
        self._conn.commit()
        if row is None:
            raise RuntimeError("Failed to create or resolve group id.")
        return int(self._cell(row, "group_id", 0))

    def get_hierarchy_etag(self, model_id: str, dimension_name: str, hierarchy_name: str) -> Optional[str]:
        _ = model_id
        row = self._conn.execute(
            """
            SELECT etag
            FROM groups
            WHERE dimension_name=? AND hierarchy_name=?
            ORDER BY group_id
            LIMIT 1
            """,
            (dimension_name, hierarchy_name),
        ).fetchone()
        if row is None:
            return None
        value = self._cell(row, "etag", 0)
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
        value = self._cell(row, "etag", 0)
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
        raw = self._cell(row, "filter_rules_json", 0)
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
        model_id: str,
        dimension_name: str,
        hierarchy_name: str,
        object_type: str,
    ) -> tuple[Optional[str], list[str]]:
        _ = model_id
        row = self._conn.execute(
            """
            SELECT etag, filter_rules_json
            FROM groups
            WHERE dimension_name=? AND hierarchy_name=? AND object_type=?
            LIMIT 1
            """,
            (dimension_name, hierarchy_name, object_type),
        ).fetchone()
        if row is None:
            return None, []
        etag = self._cell(row, "etag", 0)
        raw_rules = self._cell(row, "filter_rules_json", 1)
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
        stored_row_count = int(self._cell(row, "row_count", 0))
        actual_row_count = self._actual_row_count(group_id)
        content_hash = str(self._cell(row, "content_hash", 1))
        if stored_row_count != actual_row_count:
            # Metadata can get stale across schema/storage transitions; trust the table.
            content_hash = self.EMPTY_CONTENT_HASH
        return actual_row_count, content_hash

    @staticmethod
    def _payload_to_json(payload: dict[str, Any]) -> str:
        return _json_dumps(payload, sort_keys=True)

    def append_payloads(
        self,
        group_id: int,
        payloads: Iterable[dict[str, Any]],
        *,
        batch_size: int = DEFAULT_BULK_INSERT_BATCH_SIZE,
        progress_label: Optional[str] = None,
        progress_every: int = DEFAULT_ITER_PROGRESS_EVERY,
    ) -> int:
        row_count, _content_hash = self._load_group_state(group_id)
        object_type_normalized = self._object_type_normalized_for_group(group_id)
        payload_table = self._object_table_for_group(group_id)
        payload_columns = self._payload_columns_for_type(object_type_normalized)
        next_seq = row_count
        inserted = 0
        batch_size = max(1, int(batch_size))
        next_log_at = max(1, int(progress_every))
        insert_sql = (
            f"INSERT INTO {payload_table}(group_id, seq, {', '.join(payload_columns)}) "
            f"VALUES (?, ?, {', '.join(['?'] * len(payload_columns))})"
        )
        if progress_label:
            logger.info(
                "Starting DB append '%s' group_id=%d progress_every=%d",
                progress_label,
                group_id,
                next_log_at,
            )
        pending_rows: list[tuple[Any, ...]] = []

        def _flush_pending_rows() -> None:
            nonlocal pending_rows, row_count, next_log_at
            if not pending_rows:
                return
            batch_count = len(pending_rows)
            with self.tx():
                self._conn.executemany(
                    insert_sql,
                    pending_rows,
                )
            row_count += batch_count
            pending_rows = []
            if progress_label and inserted >= next_log_at:
                logger.info(
                    "DB append progress '%s' group_id=%d inserted=%d",
                    progress_label,
                    group_id,
                    inserted,
                )
                while inserted >= next_log_at:
                    next_log_at += max(1, int(progress_every))

        for payload in payloads:
            pending_rows.append((group_id, next_seq, *self._payload_values_for_type(object_type_normalized, payload)))
            next_seq += 1
            inserted += 1
            if len(pending_rows) >= batch_size:
                _flush_pending_rows()
        _flush_pending_rows()
        now = time.time_ns()
        self._conn.execute(
            """
            UPDATE groups
            SET row_count=?, content_hash=?, hash_algo=?, updated_at_ns=?
            WHERE group_id=?
            """,
            (
                row_count,
                self.EMPTY_CONTENT_HASH,
                self.PARALLEL_HASH_ALGO,
                now,
                group_id,
            ),
        )
        self._conn.commit()
        if progress_label:
            logger.info(
                "Completed DB append '%s' group_id=%d inserted=%d",
                progress_label,
                group_id,
                inserted,
            )
        return inserted

    def replace_group_payloads(
        self,
        group_id: int,
        payloads: Iterable[dict[str, Any]],
        *,
        source_json_mtime_ns: Optional[int] = None,
    ) -> int:
        row_count = 0
        object_type_normalized = self._object_type_normalized_for_group(group_id)
        payload_table = self._object_table_for_group(group_id)
        payload_columns = self._payload_columns_for_type(object_type_normalized)
        now = time.time_ns()
        with self.tx():
            self._conn.execute(f"DELETE FROM {payload_table} WHERE group_id=?", (group_id,))
            for payload in payloads:
                self._conn.execute(
                    (
                        f"INSERT INTO {payload_table}(group_id, seq, {', '.join(payload_columns)}) "
                        f"VALUES (?, ?, {', '.join(['?'] * len(payload_columns))})"
                    ),
                    (group_id, row_count, *self._payload_values_for_type(object_type_normalized, payload)),
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
                    self.EMPTY_CONTENT_HASH,
                    self.PARALLEL_HASH_ALGO,
                    str(int(source_json_mtime_ns)) if source_json_mtime_ns is not None else None,
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
        payload_table = self._object_table_for_group(group_id)
        object_type_normalized = self._object_type_normalized_for_group(group_id)
        order_clause = self._identity_order_clause_for_type(object_type_normalized) if ordered_by_identity else "seq"
        total = self.row_count(group_id)
        if progress_label:
            logger.debug(
                "Starting DB payload stream '%s' group_id=%d total=%d ordered_by_identity=%s progress_every=%d",
                progress_label,
                group_id,
                total,
                ordered_by_identity,
                progress_every,
            )
        next_log_at = max(1, progress_every)
        emitted = 0
        payload_columns = self._payload_columns_for_type(object_type_normalized)
        cursor = self._conn.execute(
            f"SELECT {', '.join(payload_columns)} FROM {payload_table} WHERE group_id=? ORDER BY {order_clause}",
            (group_id,),
        )
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            emitted += 1
            if progress_label and emitted >= next_log_at:
                logger.debug(
                    "DB payload stream progress '%s' group_id=%d emitted=%d/%d",
                    progress_label,
                    group_id,
                    emitted,
                    total,
                )
                while emitted >= next_log_at:
                    next_log_at += max(1, progress_every)
            yield self._payload_dict_from_row_for_type(object_type_normalized, row)
        if progress_label:
            logger.debug(
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
        payload_table = self._object_table_for_group(group_id)
        object_type_normalized = self._object_type_normalized_for_group(group_id)
        order_clause = self._identity_order_clause_for_type(object_type_normalized) if ordered_by_identity else "seq"
        total = self.row_count(group_id)
        if progress_label:
            logger.debug(
                "Starting DB JSON stream '%s' group_id=%d total=%d ordered_by_identity=%s progress_every=%d",
                progress_label,
                group_id,
                total,
                ordered_by_identity,
                progress_every,
            )
        next_log_at = max(1, progress_every)
        emitted = 0
        payload_columns = self._payload_columns_for_type(object_type_normalized)
        cursor = self._conn.execute(
            f"SELECT {', '.join(payload_columns)} FROM {payload_table} WHERE group_id=? ORDER BY {order_clause}",
            (group_id,),
        )
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            emitted += 1
            if progress_label and emitted >= next_log_at:
                logger.debug(
                    "DB JSON stream progress '%s' group_id=%d emitted=%d/%d",
                    progress_label,
                    group_id,
                    emitted,
                    total,
                )
                while emitted >= next_log_at:
                    next_log_at += max(1, progress_every)
            yield self._payload_json_from_row_for_type(object_type_normalized, row)
        if progress_label:
            logger.debug(
                "Completed DB JSON stream '%s' group_id=%d emitted=%d/%d",
                progress_label,
                group_id,
                emitted,
                total,
            )

    def _identity_key_at_offset(self, payload_table: str, normalized: str, group_id: int, offset: int) -> tuple[Any, ...]:
        row = self._conn.execute(
            _identity_key_query_for_type(payload_table, normalized),
            (group_id, int(offset)),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"Failed to resolve identity key at offset={offset} for group_id={group_id}"
            )
        return tuple(row)

    def resolve_parallel_hash_inputs(self, group_id: int) -> tuple[str, str, int]:
        normalized = self._object_type_normalized_for_group(group_id)
        payload_table = self._object_table_for_group(group_id)
        total_rows = self.row_count(group_id)
        return normalized, payload_table, total_rows

    def resolve_identity_key_at_offset(
        self,
        payload_table: str,
        normalized: str,
        group_id: int,
        offset: int,
    ) -> tuple[Any, ...]:
        return self._identity_key_at_offset(payload_table, normalized, group_id, offset)

    def commit_group_content_signature(
        self,
        group_id: int,
        *,
        row_count: int,
        content_hash: str,
    ) -> None:
        now = time.time_ns()
        self._conn.execute(
            """
            UPDATE groups
            SET row_count=?, content_hash=?, hash_algo=?, updated_at_ns=?
            WHERE group_id=?
            """,
            (int(row_count), str(content_hash), self.PARALLEL_HASH_ALGO, now, group_id),
        )
        self._conn.commit()

    def row_count(self, group_id: int) -> int:
        return self._actual_row_count(group_id)

    def content_signature(self, group_id: int) -> Optional[tuple[int, str]]:
        row = self._conn.execute(
            "SELECT content_hash FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        return self._actual_row_count(group_id), str(self._cell(row, "content_hash", 0))

    def set_content_signature(self, group_id: int, *, row_count: int, content_hash: str) -> None:
        self._conn.execute(
            """
            UPDATE groups
            SET row_count=?, content_hash=?, hash_algo=?, updated_at_ns=?
            WHERE group_id=?
            """,
            (int(row_count), str(content_hash), self.PARALLEL_HASH_ALGO, time.time_ns(), group_id),
        )
        self._conn.commit()

    def set_source_json_mtime_ns(self, group_id: int, source_json_mtime_ns: int) -> None:
        self._conn.execute(
            "UPDATE groups SET source_json_mtime_ns=?, updated_at_ns=? WHERE group_id=?",
            (str(int(source_json_mtime_ns)), time.time_ns(), group_id),
        )
        self._conn.commit()

    def source_json_mtime_ns(self, group_id: int) -> Optional[int]:
        row = self._conn.execute(
            "SELECT source_json_mtime_ns FROM groups WHERE group_id=?",
            (group_id,),
        ).fetchone()
        if row is None:
            return None
        value = self._cell(row, "source_json_mtime_ns", 0)
        return int(value) if value is not None else None
