import sqlite3
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


class ChangesetStore:
    @classmethod
    def path_for(cls, *, changeset_id: str, base_dir: Optional[str] = None) -> Path:
        root = Path(base_dir).expanduser().resolve() if base_dir else (Path.cwd().resolve() / ".tm1gitpy")
        return root / f"changeset-{changeset_id}.sqlite"

    def __init__(self, *, changeset_id: str, base_dir: Optional[str] = None, require_exists: bool = False):
        self.db_path = self.path_for(changeset_id=changeset_id, base_dir=base_dir)
        if require_exists and not self.db_path.exists():
            raise FileNotFoundError(f"Changeset store not found for id '{changeset_id}': {self.db_path}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            pass
        self._initialize()

    def _table_columns(self, table_name: str) -> list[str]:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [str(row[1]) for row in rows]

    def _table_exists(self, table_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    def _schema_needs_reset(self) -> bool:
        if not self._table_exists("changes"):
            return False
        expected = {
            "seq",
            "change_type",
            "object_type",
            "uri",
            "apply",
            "body_json",
            "dim_name",
            "hier_name",
            "object_name",
            "cube_name",
            "process_name",
            "chore_name",
            "type_rank",
            "precedence_rank",
            "body_name",
        }
        return set(self._table_columns("changes")) != expected

    def _initialize(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA busy_timeout=30000")
        if self._schema_needs_reset():
            self._conn.execute("DROP TABLE IF EXISTS changes")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS changes (
                seq INTEGER PRIMARY KEY,
                change_type TEXT NOT NULL,
                object_type TEXT NOT NULL,
                uri TEXT NOT NULL,
                apply INTEGER NOT NULL,
                body_json TEXT NOT NULL,
                dim_name TEXT NULL,
                hier_name TEXT NULL,
                object_name TEXT NULL,
                cube_name TEXT NULL,
                process_name TEXT NULL,
                chore_name TEXT NULL,
                type_rank INTEGER NOT NULL,
                precedence_rank INTEGER NOT NULL,
                body_name TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_changes_apply_sort
            ON changes(apply, type_rank, precedence_rank, body_name, uri, seq)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_changes_sort
            ON changes(type_rank, precedence_rank, body_name, uri, seq)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_changes_dim_hier_object_seq
            ON changes(dim_name, hier_name, object_name, seq)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_changes_cube_object_seq
            ON changes(cube_name, object_name, seq)
            """
        )
        self._conn.commit()

    def _prepare_rows(self, rows: Iterable[dict[str, Any]]) -> list[tuple[Any, ...]]:
        prepared_rows: list[tuple[Any, ...]] = []
        for row in rows:
            prepared_rows.append(
                (
                    int(row["seq"]),
                    str(row["change_type"]),
                    str(row["object_type"]),
                    str(row["uri"]),
                    1 if bool(row["apply"]) else 0,
                    str(row["body_json"]),
                    row.get("dim_name"),
                    row.get("hier_name"),
                    row.get("object_name"),
                    row.get("cube_name"),
                    row.get("process_name"),
                    row.get("chore_name"),
                    int(row["type_rank"]),
                    int(row["precedence_rank"]),
                    str(row["body_name"]),
                )
            )
        return prepared_rows

    def replace_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        self._conn.execute("DELETE FROM changes")
        prepared_rows = self._prepare_rows(rows)
        self._conn.executemany(
            """
            INSERT INTO changes(
                seq, change_type, object_type, uri, apply, body_json,
                dim_name, hier_name, object_name, cube_name, process_name, chore_name,
                type_rank, precedence_rank, body_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prepared_rows,
        )
        self._conn.commit()

    def append_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        current_seq = self.count_rows()
        adjusted_rows = []
        for offset, row in enumerate(rows):
            data = dict(row)
            data["seq"] = current_seq + offset
            adjusted_rows.append(data)
        prepared_rows = self._prepare_rows(adjusted_rows)
        self._conn.executemany(
            """
            INSERT INTO changes(
                seq, change_type, object_type, uri, apply, body_json,
                dim_name, hier_name, object_name, cube_name, process_name, chore_name,
                type_rank, precedence_rank, body_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            prepared_rows,
        )
        self._conn.commit()

    def count_rows(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM changes").fetchone()
        return int(row[0]) if row is not None else 0

    def clear(self) -> None:
        self._conn.execute("DELETE FROM changes")
        self._conn.commit()

    @staticmethod
    def _order_clause(sorted_order: bool) -> str:
        if not sorted_order:
            return "seq"
        return "type_rank, precedence_rank, body_name, uri, seq"

    def iter_rows(
        self,
        *,
        apply_only: Optional[bool] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        sorted_order: bool = True,
    ) -> Iterator[sqlite3.Row]:
        where_clauses = []
        params: list[Any] = []
        if apply_only is not None:
            where_clauses.append("apply=?")
            params.append(1 if apply_only else 0)

        sql = (
            "SELECT seq, change_type, object_type, uri, apply, body_json, "
            "dim_name, hier_name, object_name, cube_name, process_name, chore_name "
            "FROM changes"
        )
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += f" ORDER BY {self._order_clause(sorted_order)}"

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), max(0, int(offset))])
        elif offset > 0:
            sql += " LIMIT -1 OFFSET ?"
            params.append(max(0, int(offset)))

        cursor = self._conn.execute(sql, tuple(params))
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            yield row

    def row_at(self, index: int, *, sorted_order: bool = True) -> Optional[sqlite3.Row]:
        if index < 0:
            return None
        sql = (
            "SELECT seq, change_type, object_type, uri, apply, body_json, "
            "dim_name, hier_name, object_name, cube_name, process_name, chore_name "
            "FROM changes "
            f"ORDER BY {self._order_clause(sorted_order)} LIMIT 1 OFFSET ?"
        )
        return self._conn.execute(sql, (index,)).fetchone()

    @staticmethod
    def _is_uri_selected(uri: str, rules_json: str) -> int:
        from tm1_git_py.filter import FilterRules
        import orjson

        try:
            parsed = orjson.loads(rules_json) if rules_json else []
        except Exception:
            parsed = []
        if not isinstance(parsed, list):
            parsed = []
        rules = FilterRules([str(rule) for rule in parsed])
        return 0 if rules.should_exclude(uri) else 1

    @staticmethod
    def _desired_apply(uri: str, rules_json: str) -> int:
        # apply=True for items that are not excluded by effective rules.
        return int(ChangesetStore._is_uri_selected(uri, rules_json))

    def _where_for_rules(
        self,
        *,
        filter_rules: Optional[list[str]],
        apply_only: Optional[bool],
        params: list[Any],
    ) -> str:
        clauses: list[str] = []
        if apply_only is not None:
            clauses.append("apply=?")
            params.append(1 if apply_only else 0)
        if filter_rules:
            import orjson

            rules_json = orjson.dumps([str(rule) for rule in filter_rules]).decode("utf-8")
            self._conn.create_function("uri_is_selected", 2, self._is_uri_selected)
            clauses.append("uri_is_selected(uri, ?) = 1")
            params.append(rules_json)
        return "" if not clauses else (" WHERE " + " AND ".join(clauses))

    def query_rows(
        self,
        *,
        filter_rules: Optional[list[str]] = None,
        offset: int = 0,
        limit: Optional[int] = None,
        apply_only: Optional[bool] = None,
        sorted_order: bool = True,
    ) -> Iterator[sqlite3.Row]:
        params: list[Any] = []
        where = self._where_for_rules(filter_rules=filter_rules, apply_only=apply_only, params=params)
        sql = (
            "SELECT seq, change_type, object_type, uri, apply, body_json, "
            "dim_name, hier_name, object_name, cube_name, process_name, chore_name "
            "FROM changes"
            + where
            + f" ORDER BY {self._order_clause(sorted_order)}"
        )
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), max(0, int(offset))])
        elif offset > 0:
            sql += " LIMIT -1 OFFSET ?"
            params.append(max(0, int(offset)))
        cursor = self._conn.execute(sql, tuple(params))
        while True:
            row = cursor.fetchone()
            if row is None:
                break
            yield row

    def toggle_apply(self, *, filter_rules: Optional[list[str]] = None) -> int:
        if not filter_rules:
            # With no rules, everything should end up apply=true.
            cursor = self._conn.execute(
                "UPDATE changes SET apply = 1 WHERE apply != 1"
            )
            self._conn.commit()
            return int(cursor.rowcount or 0)

        import orjson

        rules_json = orjson.dumps([str(rule) for rule in filter_rules]).decode("utf-8")
        self._conn.create_function("desired_apply", 2, self._desired_apply)
        cursor = self._conn.execute(
            """
            UPDATE changes
            SET apply = desired_apply(uri, ?)
            WHERE apply != desired_apply(uri, ?)
            """,
            (rules_json, rules_json),
        )
        self._conn.commit()
        return int(cursor.rowcount or 0)

    def summary_counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT change_type, COUNT(*) AS cnt FROM changes GROUP BY change_type"
        ).fetchall()
        summary = {"add": 0, "remove": 0, "modify": 0}
        for row in rows:
            change_type = str(row[0]).lower()
            summary[change_type] = int(row[1])
        return summary
