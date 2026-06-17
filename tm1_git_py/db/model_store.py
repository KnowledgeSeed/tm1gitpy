import logging
import os
import time
import hashlib
import threading
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional
import orjson

from tm1_git_py.db._worker_db import WorkerDBLease, WorkerDBRegistry


DEFAULT_BULK_INSERT_BATCH_SIZE = 10_000
DEFAULT_ITER_PROGRESS_EVERY = 100_000
DEFAULT_ITER_PAGE_SIZE = 10_000
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
            f"SELECT Name FROM {payload_table} "
            "WHERE group_id=? ORDER BY Name LIMIT 1 OFFSET ?"
        )
    if normalized in ("edge", "edges"):
        return (
            f"SELECT ParentName, ComponentName FROM {payload_table} "
            "WHERE group_id=? ORDER BY ParentName, ComponentName LIMIT 1 OFFSET ?"
        )
    if normalized in ("subset", "subsets"):
        return (
            f"SELECT Name FROM {payload_table} "
            "WHERE group_id=? ORDER BY Name LIMIT 1 OFFSET ?"
        )
    raise ValueError(f"Unsupported group object type for parallel identity hashing: '{normalized}'")


def _identity_chunk_query_for_type(payload_table: str, normalized: str) -> str:
    if normalized in ("element", "elements"):
        return (
            f"SELECT Name, Type FROM {payload_table} "
            "WHERE group_id=? ORDER BY Name LIMIT ? OFFSET ?"
        )
    if normalized in ("edge", "edges"):
        return (
            f"SELECT ParentName, ComponentName, CAST(Weight AS TEXT) FROM {payload_table} "
            "WHERE group_id=? ORDER BY ParentName, ComponentName LIMIT ? OFFSET ?"
        )
    if normalized in ("subset", "subsets"):
        return (
            f"SELECT Name, Expression, Elements FROM {payload_table} "
            "WHERE group_id=? ORDER BY Name LIMIT ? OFFSET ?"
        )
    raise ValueError(f"Unsupported group object type for parallel identity hashing: '{normalized}'")


def _identity_line_for_type(normalized: str, row: tuple[Any, ...]) -> str:
    if normalized in ("element", "elements"):
        return f"{_empty_if_none(row[0])}\x1f{_empty_if_none(row[1])}"
    if normalized in ("edge", "edges"):
        return f"{_empty_if_none(row[0])}\x1f{_empty_if_none(row[1])}\x1f{_empty_if_none(row[2])}"
    if normalized in ("subset", "subsets"):
        return (
            f"{_empty_if_none(row[0])}\x1f"
            f"{_empty_if_none(row[1])}\x1f"
            f"{_empty_if_none(row[2])}"
        )
    raise ValueError(f"Unsupported group object type for parallel identity hashing: '{normalized}'")


def _empty_if_none(value: Any) -> Any:
    return "" if value is None else value


class ModelStore:
    PARALLEL_HASH_ALGO = "sha256-tree-v1"
    HASH_ALGO = PARALLEL_HASH_ALGO
    EMPTY_CONTENT_HASH = hashlib.sha256(b"").hexdigest()
    _instances: dict[str, "ModelStore"] = {}
    _instances_lock = threading.RLock()

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn_lease: WorkerDBLease = WorkerDBRegistry.acquire(
            db_path,
            execute_init=(
                "PRAGMA foreign_keys=OFF",
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA temp_store=MEMORY",
                "PRAGMA busy_timeout=30000",
            ),
        )
        self._conn = self._conn_lease.worker
        self._closed = False
        self._session_refs = 0
        self._lifecycle_lock = threading.RLock()
        self._initialize()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._conn_lease.release()

    def __getstate__(self) -> dict[str, Any]:
        # The worker thread, lease and lifecycle lock cannot be pickled. Ship
        # only the db path so the receiver can re-acquire via the registry.
        return {"db_path": self.db_path}

    def __setstate__(self, state: dict[str, Any]) -> None:
        # Re-bind by going through the canonical entry point so the registry
        # cache is honoured and the schema/PRAGMAs are applied as needed.
        rebound = ModelStore.for_db_path(str(state["db_path"]))
        self.__dict__.update(rebound.__dict__)

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
        return cls.for_db_path(db_path)

    @classmethod
    def for_db_path(cls, db_path: str) -> "ModelStore":
        abs_path = os.path.abspath(db_path)
        with cls._instances_lock:
            existing = cls._instances.get(abs_path)
            if existing is not None:
                if not existing._closed:
                    return existing
                cls._instances.pop(abs_path, None)
            created = cls(abs_path)
            cls._instances[abs_path] = created
            return created

    @classmethod
    def close_for_model_id(cls, model_id: str) -> None:
        db_path = cls._db_path_for_model_id(model_id)
        cls.close_for_db_path(db_path)

    @classmethod
    def close_for_db_path(cls, db_path: str) -> None:
        abs_path = os.path.abspath(db_path)
        with cls._instances_lock:
            store = cls._instances.pop(abs_path, None)
        if store is not None:
            store.close()
        WorkerDBRegistry.force_close(abs_path)

    @classmethod
    def close_all_current_thread(cls) -> None:
        cls.close_all()

    @classmethod
    def close_all(cls) -> None:
        with cls._instances_lock:
            stores = list(cls._instances.values())
            cls._instances.clear()
        for store in stores:
            store.close()
            WorkerDBRegistry.force_close(store.db_path)

    def _initialize(self) -> None:
        if self._schema_needs_reset():
            self._reset_schema()
        self._create_schema()
        self._conn.commit()

    def _table_columns(self, table_name: str) -> list[str]:
        rows = self._conn.fetch_all(
            "SELECT name FROM pragma_table_info(?)",
            (table_name,),
        )
        return [str(row[0]) for row in rows]

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.fetch_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        )
        return row is not None

    def _table_sql(self, table_name: str) -> str:
        row = self._conn.fetch_one(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        )
        if row is None:
            return ""
        value = self._cell(row, "sql", 0)
        return str(value or "")

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
            "sort_metadata_json",
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
            "element_objects": {"group_id", "Name", "Type", "ElementIndex"},
            "edge_objects": {"group_id", "ParentName", "ComponentName", "Weight", "ComponentIndex"},
            "subset_objects": {"group_id", "Name", "Expression", "Elements"},
        }
        for table_name, expected_cols in expected_object_tables.items():
            if not self._table_exists(table_name):
                return True
            if set(self._table_columns(table_name)) != expected_cols:
                return True
            if "WITHOUT ROWID" not in self._table_sql(table_name).upper():
                return True
        element_sql = self._table_sql("element_objects").replace(" ", "").upper()
        if "PRIMARYKEY(GROUP_ID,NAME)" not in element_sql:
            return True
        # Legacy table indicates pre-refactor layout; reset instead of migrating.
        if self._table_exists("objects"):
            return True
        return False

    def _reset_schema(self) -> None:
        logger.debug("ModelStore schema mismatch detected, discarding previous model store at '%s'", self.db_path)
        self._conn.run_sync("DROP TABLE IF EXISTS objects")
        self._conn.run_sync("DROP TABLE IF EXISTS edge_objects")
        self._conn.run_sync("DROP TABLE IF EXISTS element_objects")
        self._conn.run_sync("DROP TABLE IF EXISTS subset_objects")
        self._conn.run_sync("DROP TABLE IF EXISTS groups")

    def _create_schema(self) -> None:
        self._conn.run_sync(
            """
            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension_name TEXT NOT NULL,
                hierarchy_name TEXT NOT NULL,
                object_type TEXT NOT NULL,
                etag TEXT NULL,
                filter_rules_json JSONB NULL,
                sort_metadata_json JSONB NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                hash_algo TEXT NOT NULL,
                source_json_mtime_ns INTEGER NULL,
                updated_at_ns INTEGER NOT NULL,
                UNIQUE(dimension_name, hierarchy_name, object_type)
            )
            """
        )
        self._conn.run_sync(
            """
            CREATE TABLE IF NOT EXISTS element_objects (
                group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                Name TEXT NOT NULL,
                Type TEXT NULL,
                ElementIndex INTEGER NULL,
                PRIMARY KEY(group_id, Name)
            ) WITHOUT ROWID
            """
        )
        self._conn.run_sync(
            """
            CREATE TABLE IF NOT EXISTS edge_objects (
                group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                ParentName TEXT NOT NULL,
                ComponentName TEXT NOT NULL,
                Weight NUMERIC NULL,
                ComponentIndex INTEGER NULL,
                PRIMARY KEY(group_id, ParentName, ComponentName)
            ) WITHOUT ROWID
            """
        )
        self._conn.run_sync(
            """
            CREATE TABLE IF NOT EXISTS subset_objects (
                group_id INTEGER NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
                Name TEXT NOT NULL,
                Expression TEXT NULL,
                Elements JSONB NULL,
                PRIMARY KEY(group_id, Name)
            ) WITHOUT ROWID
            """
        )
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
        row = self._conn.fetch_one(
            "SELECT object_type FROM groups WHERE group_id=?",
            (group_id,),
        )
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
        row = self._conn.fetch_one(
            f"SELECT COUNT(*) FROM {table_name} WHERE group_id=?",
            (group_id,),
        )
        return int(self._cell(row, "COUNT(*)", 0)) if row is not None else 0

    @staticmethod
    def _identity_order_clause_for_type(normalized: str) -> str:
        if normalized in ("element", "elements"):
            return "Name"
        if normalized in ("edge", "edges"):
            return "ParentName, ComponentName"
        if normalized in ("subset", "subsets"):
            return "Name"
        raise ValueError(f"Unsupported group object type for identity ordering: '{normalized}'")

    def _internal_index_order_clause_for_type(self, normalized: str) -> str:
        if normalized in ("element", "elements"):
            return "ElementIndex IS NULL, ElementIndex, Name"
        if normalized in ("edge", "edges"):
            return "ComponentIndex IS NULL, ComponentIndex, ParentName, ComponentName"
        if normalized in ("subset", "subsets"):
            return self._identity_order_clause_for_type(normalized)
        raise ValueError(f"Unsupported group object type for internal index ordering: '{normalized}'")

    @staticmethod
    def _parallel_identity_order_clause_for_type(normalized: str) -> str:
        if normalized in ("element", "elements"):
            return "Name"
        if normalized in ("edge", "edges"):
            return "ParentName, ComponentName"
        if normalized in ("subset", "subsets"):
            return "Name"
        raise ValueError(f"Unsupported group object type for parallel identity ordering: '{normalized}'")

    @staticmethod
    def _payload_columns_for_type(normalized: str) -> tuple[str, ...]:
        if normalized in ("element", "elements"):
            return ("Name", "Type", "ElementIndex")
        if normalized in ("edge", "edges"):
            return ("ParentName", "ComponentName", "Weight", "ComponentIndex")
        if normalized in ("subset", "subsets"):
            return ("Name", "Expression", "Elements")
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    @staticmethod
    def _subset_element_ids_from_payload(payload: dict[str, Any]) -> list[str]:
        element_payloads = payload.get("element_ids")
        if element_payloads is None:
            element_payloads = payload.get("elements")
        if element_payloads is None:
            element_payloads = payload.get("Elements")
        if element_payloads is None:
            return []
        element_ids: list[str] = []
        for element_payload in element_payloads:
            if isinstance(element_payload, str):
                element_ids.append(element_payload)
                continue
            if isinstance(element_payload, dict):
                element_id = element_payload.get("@id") or element_payload.get("@odata.id")
                if isinstance(element_id, str):
                    element_ids.append(element_id)
                    continue
            raise ValueError(f"Unable to resolve subset element reference id from payload: {element_payload!r}")
        return element_ids

    @staticmethod
    def _subset_expression_from_payload(payload: dict[str, Any]) -> Any:
        expression = payload.get("Expression")
        if expression is None:
            expression = payload.get("expression")
        return expression

    @staticmethod
    def _subset_elements_from_storage(raw_elements: Any) -> list[dict[str, str]]:
        if raw_elements in (None, ""):
            return []
        stored = _json_loads(str(raw_elements))
        return [{"@id": str(element_id)} for element_id in stored]

    @staticmethod
    def _internal_index_from_payload(
        payload: dict[str, Any],
        *keys: str,
        default_index: Optional[int] = None,
    ) -> Optional[int]:
        for key in keys:
            value = payload.get(key)
            if value is not None:
                return int(value)
        return default_index

    def _payload_values_for_type(
        self,
        normalized: str,
        payload: dict[str, Any],
        *,
        default_index: Optional[int] = None,
    ) -> tuple[Any, ...]:
        if normalized in ("element", "elements"):
            name = payload.get("Name")
            if name is None:
                name = payload.get("name")
            return (
                "" if name is None else name,
                payload.get("Type") or payload.get("type"),
                self._internal_index_from_payload(
                    payload,
                    "ElementIndex",
                    "element_index",
                    default_index=default_index,
                ),
            )
        if normalized in ("edge", "edges"):
            weight = payload.get("Weight")
            if weight is None:
                weight = payload.get("weight")
            parent_name = payload.get("ParentName")
            if parent_name is None:
                parent_name = payload.get("parentName")
            if parent_name is None:
                parent_name = payload.get("parent")
            component_name = payload.get("ComponentName")
            if component_name is None:
                component_name = payload.get("componentName")
            if component_name is None:
                component_name = payload.get("name")
            if component_name is None:
                component_name = payload.get("Name")
            return (
                "" if parent_name is None else parent_name,
                "" if component_name is None else component_name,
                weight,
                self._internal_index_from_payload(
                    payload,
                    "ComponentIndex",
                    "component_index",
                    "edge_index",
                    default_index=default_index,
                ),
            )
        if normalized in ("subset", "subsets"):
            name = payload.get("Name")
            if name is None:
                name = payload.get("name")
            expression = self._subset_expression_from_payload(payload)
            elements_json = None
            if expression in (None, ""):
                elements_json = _json_dumps(self._subset_element_ids_from_payload(payload))
            return (
                "" if name is None else name,
                expression,
                elements_json,
            )
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    def _payload_dict_from_row_for_type(
        self,
        normalized: str,
        row: Any,
        *,
        include_internal_indexes: bool = False,
    ) -> dict[str, Any]:
        if normalized in ("element", "elements"):
            payload = {
                "Name": self._cell(row, "Name", 0),
                "Type": self._cell(row, "Type", 1),
            }
            if include_internal_indexes:
                payload["ElementIndex"] = self._cell(row, "ElementIndex", 2)
            return payload
        if normalized in ("edge", "edges"):
            payload = {
                "ParentName": self._cell(row, "ParentName", 0),
                "ComponentName": self._cell(row, "ComponentName", 1),
                "Weight": self._cell(row, "Weight", 2),
            }
            if include_internal_indexes:
                payload["ComponentIndex"] = self._cell(row, "ComponentIndex", 3)
            return payload
        if normalized in ("subset", "subsets"):
            payload = {"name": self._cell(row, "Name", 0)}
            expression = self._cell(row, "Expression", 1)
            if expression not in (None, ""):
                payload["expression"] = expression
            else:
                payload["Elements"] = self._subset_elements_from_storage(
                    self._cell(row, "Elements", 2)
                )
            return payload
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
                f"\"ParentName\":{self._value_json(parent)},"
                f"\"ComponentName\":{self._value_json(component)},"
                f"\"Weight\":{self._value_json(weight)}"
                "}"
            )
        if normalized in ("subset", "subsets"):
            name = payload.get("Name") or payload.get("name")
            expression = self._subset_expression_from_payload(payload)
            if expression not in (None, ""):
                return (
                    "{"
                    f"\"expression\":{self._value_json(expression)},"
                    f"\"name\":{self._value_json(name)}"
                    "}"
                )
            elements = [{"@id": element_id} for element_id in self._subset_element_ids_from_payload(payload)]
            return (
                "{"
                f"\"Elements\":{self._value_json(elements)},"
                f"\"name\":{self._value_json(name)}"
                "}"
            )
        raise ValueError(f"Unsupported group object type: '{normalized}'")

    def _payload_json_from_row_for_type(self, normalized: str, row: Any) -> str:
        if normalized in ("element", "elements"):
            return (
                "{\n"
                f"\t\t\t\"Name\":{self._value_json(self._cell(row, 'Name', 0))},\n"
                f"\t\t\t\"Type\":{self._value_json(self._cell(row, 'Type', 1))}\n"
                "\t\t}"
            )
        if normalized in ("edge", "edges"):
            return (
                "{\n"
                f"\t\t\t\"ParentName\":{self._value_json(self._cell(row, 'ParentName', 0))},\n"
                f"\t\t\t\"ComponentName\":{self._value_json(self._cell(row, 'ComponentName', 1))},\n"
                f"\t\t\t\"Weight\":{self._value_json(self._cell(row, 'Weight', 2))}\n"
                "\t\t}"
            )
        if normalized in ("subset", "subsets"):
            expression = self._cell(row, "Expression", 1)
            if expression not in (None, ""):
                return (
                    "{\n"
                    f"\t\t\t\"expression\":{self._value_json(expression)},\n"
                    f"\t\t\t\"name\":{self._value_json(self._cell(row, 'Name', 0))}\n"
                    "\t\t}"
                )
            elements = self._subset_elements_from_storage(self._cell(row, "Elements", 2))
            return (
                "{\n"
                f"\t\t\t\"Elements\":{self._value_json(elements)},\n"
                f"\t\t\t\"name\":{self._value_json(self._cell(row, 'Name', 0))}\n"
                "\t\t}"
            )
        raise ValueError(f"Unsupported group object type: '{normalized}'")

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
        self._conn.run_sync(
            """
            INSERT INTO groups(dimension_name, hierarchy_name, object_type, etag, filter_rules_json, sort_metadata_json, row_count, content_hash, hash_algo, source_json_mtime_ns, updated_at_ns)
            VALUES (?, ?, ?, NULL, NULL, NULL, 0, ?, ?, NULL, ?)
            ON CONFLICT(dimension_name, hierarchy_name, object_type) DO NOTHING
            """,
            (dimension_name, hierarchy_name, object_type, self.EMPTY_CONTENT_HASH, self.HASH_ALGO, now),
        )
        row = self._conn.fetch_one(
            """
            SELECT group_id FROM groups
            WHERE dimension_name=? AND hierarchy_name=? AND object_type=?
            """,
            (dimension_name, hierarchy_name, object_type),
        )
        if row is None:
            raise RuntimeError("Failed to create or resolve group id.")
        return int(self._cell(row, "group_id", 0))

    def get_hierarchy_etag(self, model_id: str, dimension_name: str, hierarchy_name: str) -> Optional[str]:
        _ = model_id
        row = self._conn.fetch_one(
            """
            SELECT etag
            FROM groups
            WHERE dimension_name=? AND hierarchy_name=?
            ORDER BY group_id
            LIMIT 1
            """,
            (dimension_name, hierarchy_name),
        )
        if row is None:
            return None
        value = self._cell(row, "etag", 0)
        return str(value) if value is not None else None

    def set_group_etag(self, group_id: int, etag: Optional[str]) -> None:
        self._conn.run_sync(
            "UPDATE groups SET etag=?, updated_at_ns=? WHERE group_id=?",
            (etag, time.time_ns(), group_id),
        )
    def group_etag(self, group_id: int) -> Optional[str]:
        row = self._conn.fetch_one(
            "SELECT etag FROM groups WHERE group_id=?",
            (group_id,),
        )
        if row is None:
            return None
        value = self._cell(row, "etag", 0)
        return str(value) if value is not None else None

    def set_group_filter_rules(self, group_id: int, filter_rules: list[str]) -> None:
        payload = _json_dumps(list(filter_rules))
        self._conn.run_sync(
            "UPDATE groups SET filter_rules_json=?, updated_at_ns=? WHERE group_id=?",
            (payload, time.time_ns(), group_id),
        )

    def group_filter_rules(self, group_id: int) -> list[str]:
        row = self._conn.fetch_one(
            "SELECT filter_rules_json FROM groups WHERE group_id=?",
            (group_id,),
        )
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

    def set_group_sort_metadata(self, group_id: int, sort_metadata: dict[str, Any]) -> None:
        payload = _json_dumps(dict(sort_metadata), sort_keys=True)
        self._conn.run_sync(
            "UPDATE groups SET sort_metadata_json=?, updated_at_ns=? WHERE group_id=?",
            (payload, time.time_ns(), group_id),
        )

    def group_sort_metadata(self, group_id: int) -> dict[str, str]:
        row = self._conn.fetch_one(
            "SELECT sort_metadata_json FROM groups WHERE group_id=?",
            (group_id,),
        )
        if row is None:
            return {}
        raw = self._cell(row, "sort_metadata_json", 0)
        if raw in (None, ""):
            return {}
        try:
            parsed = _json_loads(str(raw))
        except (TypeError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): str(value) for key, value in parsed.items()}

    def get_group_reuse_metadata(
        self,
        *,
        model_id: str,
        dimension_name: str,
        hierarchy_name: str,
        object_type: str,
    ) -> tuple[Optional[str], list[str], Optional[tuple[int, str]]]:
        _ = model_id
        row = self._conn.fetch_one(
            """
            SELECT group_id, etag, filter_rules_json, content_hash
            FROM groups
            WHERE dimension_name=? AND hierarchy_name=? AND object_type=?
            LIMIT 1
            """,
            (dimension_name, hierarchy_name, object_type),
        )
        if row is None:
            return None, [], None
        group_id = int(self._cell(row, "group_id", 0))
        etag = self._cell(row, "etag", 1)
        raw_rules = self._cell(row, "filter_rules_json", 2)
        content_hash = self._cell(row, "content_hash", 3)
        if raw_rules in (None, ""):
            rules: list[str] = []
        else:
            try:
                parsed = _json_loads(str(raw_rules))
            except (TypeError, ValueError):
                parsed = []
            if not isinstance(parsed, list):
                parsed = []
            rules = [str(item) for item in parsed]
        content_signature: tuple[int, str] = (
            self._actual_row_count(group_id),
            str(content_hash),
        )
        return (
            str(etag) if etag is not None else None,
            rules,
            content_signature,
        )

    @staticmethod
    def _payload_to_json(payload: dict[str, Any]) -> str:
        return _json_dumps(payload, sort_keys=True)

    def append_payloads(
        self,
        group_id: int,
        payloads: Iterable[dict[str, Any]],
        *,
        start_index: Optional[int] = None,
        batch_size: int = DEFAULT_BULK_INSERT_BATCH_SIZE,
        progress_label: Optional[str] = None,
        progress_every: int = DEFAULT_ITER_PROGRESS_EVERY,
    ) -> int:
        object_type_normalized = self._object_type_normalized_for_group(group_id)
        payload_table = self._object_table_for_group(group_id)
        payload_columns = self._payload_columns_for_type(object_type_normalized)
        inserted = 0
        batch_size = max(1, int(batch_size))
        next_log_at = max(1, int(progress_every))
        insert_sql = (
            f"INSERT INTO {payload_table}(group_id, {', '.join(payload_columns)}) "
            f"VALUES (?, {', '.join(['?'] * len(payload_columns))})"
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
            nonlocal pending_rows, next_log_at
            if not pending_rows:
                return

            self._conn.executemany_sync(
                insert_sql,
                pending_rows,
            )
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

        existing_count = self._actual_row_count(group_id)
        default_start_index = existing_count if start_index is None else int(start_index)
        for payload in payloads:
            pending_rows.append(
                (
                    group_id,
                    *self._payload_values_for_type(
                        object_type_normalized,
                        payload,
                        default_index=default_start_index + inserted,
                    ),
                )
            )
            inserted += 1
            if len(pending_rows) >= batch_size:
                _flush_pending_rows()
        _flush_pending_rows()
        
        now = time.time_ns()
        self._conn.run_sync(
            f"""
            UPDATE groups
            SET row_count=(SELECT COUNT(*) FROM {payload_table} WHERE group_id=?),
                content_hash=?, hash_algo=?, updated_at_ns=?
            WHERE group_id=?
            """,
            (
                group_id,
                self.EMPTY_CONTENT_HASH,
                self.PARALLEL_HASH_ALGO,
                now,
                group_id,
            ),
        )
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
        # with self.tx():
        self._conn.run_sync(f"DELETE FROM {payload_table} WHERE group_id=?", (group_id,))
        for payload in payloads:
            self._conn.run_sync(
                (
                    f"INSERT OR REPLACE INTO {payload_table}(group_id, {', '.join(payload_columns)}) "
                    f"VALUES (?, {', '.join(['?'] * len(payload_columns))})"
                ),
                (
                    group_id,
                    *self._payload_values_for_type(
                        object_type_normalized,
                        payload,
                        default_index=row_count,
                    ),
                ),
            )
            row_count += 1
        row_count = self._actual_row_count(group_id)
        if source_json_mtime_ns is None:
            self._conn.run_sync(
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
        else:
            self._conn.run_sync(
                """
                UPDATE groups
                SET row_count=?, content_hash=?, hash_algo=?,
                    source_json_mtime_ns=?, updated_at_ns=?
                WHERE group_id=?
                """,
                (
                    row_count,
                    self.EMPTY_CONTENT_HASH,
                    self.PARALLEL_HASH_ALGO,
                    str(int(source_json_mtime_ns)),
                    now,
                    group_id,
                ),
            )
        return row_count

    def _iter_payload_rows_paged(
        self,
        *,
        group_id: int,
        payload_table: str,
        object_type_normalized: str,
        payload_columns: tuple[str, ...],
        page_size: int,
        order_by_internal_index: bool = False,
    ) -> Iterator[tuple[Any, ...]]:
        """Yield rows in identity sort order using LIMIT/OFFSET paging."""
        cols = ", ".join(payload_columns)
        if order_by_internal_index:
            order = self._internal_index_order_clause_for_type(object_type_normalized)
        else:
            order = self._identity_order_clause_for_type(object_type_normalized)
        sql = (
            f"SELECT {cols} FROM {payload_table} "
            f"WHERE group_id=? ORDER BY {order} LIMIT ? OFFSET ?"
        )
        gid = int(group_id)
        ps = max(1, int(page_size))
        offset = 0
        while True:
            batch = self._conn.fetch_all(sql, (gid, ps, offset))
            if not batch:
                return
            yield from batch
            if len(batch) < ps:
                return
            offset += len(batch)

    def iter_payloads(
        self,
        group_id: int,
        *,
        ordered_by_identity: bool = False,
        order_by_internal_index: bool = False,
        include_internal_indexes: bool = False,
        progress_label: Optional[str] = None,
        progress_every: int = DEFAULT_ITER_PROGRESS_EVERY,
    ) -> Iterator[dict[str, Any]]:
        payload_table = self._object_table_for_group(group_id)
        object_type_normalized = self._object_type_normalized_for_group(group_id)
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
        for row in self._iter_payload_rows_paged(
            group_id=group_id,
            payload_table=payload_table,
            object_type_normalized=object_type_normalized,
            payload_columns=payload_columns,
            page_size=DEFAULT_ITER_PAGE_SIZE,
            order_by_internal_index=order_by_internal_index,
        ):
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
            yield self._payload_dict_from_row_for_type(
                object_type_normalized,
                row,
                include_internal_indexes=include_internal_indexes,
            )
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
        order_by_internal_index: bool = False,
        progress_label: Optional[str] = None,
        progress_every: int = DEFAULT_ITER_PROGRESS_EVERY,
    ) -> Iterator[str]:
        payload_table = self._object_table_for_group(group_id)
        object_type_normalized = self._object_type_normalized_for_group(group_id)
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
        for row in self._iter_payload_rows_paged(
            group_id=group_id,
            payload_table=payload_table,
            object_type_normalized=object_type_normalized,
            payload_columns=payload_columns,
            page_size=DEFAULT_ITER_PAGE_SIZE,
            order_by_internal_index=order_by_internal_index,
        ):
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
        row = self._conn.fetch_one(
            _identity_key_query_for_type(payload_table, normalized),
            (group_id, int(offset)),
        )
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
        self._conn.run_sync(
            """
            UPDATE groups
            SET row_count=?, content_hash=?, hash_algo=?, updated_at_ns=?
            WHERE group_id=?
            """,
            (int(row_count), str(content_hash), self.PARALLEL_HASH_ALGO, now, group_id),
        )

    def row_count(self, group_id: int) -> int:
        return self._actual_row_count(group_id)

    def content_signature(self, group_id: int) -> Optional[tuple[int, str]]:
        row = self._conn.fetch_one(
            "SELECT content_hash FROM groups WHERE group_id=?",
            (group_id,),
        )
        if row is None:
            return None
        return self._actual_row_count(group_id), str(self._cell(row, "content_hash", 0))

    def set_content_signature(self, group_id: int, *, row_count: int, content_hash: str) -> None:
        self._conn.run_sync(
            """
            UPDATE groups
            SET row_count=?, content_hash=?, hash_algo=?, updated_at_ns=?
            WHERE group_id=?
            """,
            (int(row_count), str(content_hash), self.PARALLEL_HASH_ALGO, time.time_ns(), group_id),
        )

    def set_source_json_mtime_ns(self, group_id: int, source_json_mtime_ns: int) -> None:
        self._conn.run_sync(
            "UPDATE groups SET source_json_mtime_ns=?, updated_at_ns=? WHERE group_id=?",
            (str(int(source_json_mtime_ns)), time.time_ns(), group_id), 
        )

    def source_json_mtime_ns(self, group_id: int) -> Optional[int]:
        row = self._conn.fetch_one(
            "SELECT source_json_mtime_ns FROM groups WHERE group_id=?",
            (group_id,),
        )
        if row is None:
            return None
        value = self._cell(row, "source_json_mtime_ns", 0)
        return int(value) if value is not None else None
