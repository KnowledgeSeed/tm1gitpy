import json
import os
import hashlib
import heapq
import shutil
import tempfile
from collections.abc import Iterable, Iterator, MutableSequence
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class DiskBackedList(MutableSequence[T], Generic[T]):
    """Append-optimized list-like collection with optional JSONL backing."""
    HASH_ALGO = "sha256-chain-v1"
    EMPTY_CONTENT_HASH = hashlib.sha256(b"").hexdigest()

    def __init__(
        self,
        *,
        to_dict: Callable[[T], dict],
        from_dict: Callable[[dict], T],
        jsonl_path: Optional[str] = None,
        truncate: bool = False,
        on_append: Optional[Callable[[list[dict]], None]] = None,
        store_items: bool = True,
        allow_random_ops: bool = True,
    ):
        self._to_dict = to_dict
        self._from_dict = from_dict
        self._jsonl_path = jsonl_path
        self._on_append = on_append
        self._items: Optional[list[T]] = [] if store_items else None
        self._allow_random_ops = allow_random_ops and store_items
        self._count = 0
        if self._jsonl_path:
            os.makedirs(os.path.dirname(self._jsonl_path), exist_ok=True)
            if truncate:
                with open(self._jsonl_path, "w", encoding="utf-8"):
                    pass
                self._write_sidecar(count=0, content_hash=self.EMPTY_CONTENT_HASH, hash_algo=self.HASH_ALGO)
            self._count = self._count_lines()
            if self._items is not None and self._count:
                self._items.extend(list(self._iter_file_items()))

    @staticmethod
    def sidecar_path_for_jsonl(jsonl_path: str) -> str:
        return f"{jsonl_path}.meta.json"

    @classmethod
    def _hash_line(cls, previous_hash: str, line_bytes: bytes) -> str:
        hasher = hashlib.sha256()
        hasher.update(bytes.fromhex(previous_hash))
        hasher.update(line_bytes)
        return hasher.hexdigest()

    @classmethod
    def write_count_sidecar_for_jsonl(
        cls,
        jsonl_path: str,
        count: int,
        *,
        content_hash: Optional[str] = None,
        hash_algo: Optional[str] = None,
        sorted: Optional[bool] = None,
        sort_key: Optional[str] = None,
        source_json_mtime_ns: Optional[int] = None,
    ) -> None:
        sidecar_path = cls.sidecar_path_for_jsonl(jsonl_path)
        if not os.path.exists(jsonl_path):
            return
        stat = os.stat(jsonl_path)
        existing: dict[str, Any] = {}
        if os.path.exists(sidecar_path):
            try:
                with open(sidecar_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                existing = {}

        payload = dict(existing)
        payload.update({
            "count": int(max(0, count)),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        })
        if content_hash:
            payload["content_hash"] = content_hash
            payload["hash_algo"] = hash_algo or cls.HASH_ALGO
        if sorted is not None:
            payload["sorted"] = bool(sorted)
            if not sorted and sort_key is None:
                payload.pop("sort_key", None)
        if sort_key is not None:
            payload["sort_key"] = sort_key
        if source_json_mtime_ns is not None:
            payload["source_json_mtime_ns"] = int(source_json_mtime_ns)
        tmp_path = f"{sidecar_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        os.replace(tmp_path, sidecar_path)

    @classmethod
    def update_sidecar_metadata_for_jsonl(cls, jsonl_path: str, **metadata: Any) -> bool:
        sidecar_path = cls.sidecar_path_for_jsonl(jsonl_path)
        if not os.path.exists(sidecar_path):
            return False
        try:
            with open(sidecar_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                return False
            payload.update(metadata)
            tmp_path = f"{sidecar_path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            os.replace(tmp_path, sidecar_path)
            return True
        except Exception:
            return False

    def _ensure_items_access(self) -> list[T]:
        if self._items is None:
            raise NotImplementedError(
                "This DiskBackedList is append-only and does not retain items in memory."
            )
        return self._items

    def _count_lines(self) -> int:
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return 0
        sidecar = self._read_validated_sidecar()
        if sidecar is not None:
            return sidecar["count"]
        sort_hints = self._read_sidecar_sort_hints()
        count, content_hash = self._scan_count_and_hash_from_file()
        hinted_sorted = bool(sort_hints.get("sorted")) if sort_hints else False
        hinted_sort_key = sort_hints.get("sort_key") if sort_hints else None
        self._write_sidecar(
            count=count,
            content_hash=content_hash,
            hash_algo=self.HASH_ALGO,
            sorted=hinted_sorted if hinted_sort_key else None,
            sort_key=hinted_sort_key if hinted_sorted else None,
        )
        return count

    def _count_lines_by_scan(self) -> int:
        count, _ = self._scan_count_and_hash_from_file()
        return count

    def _scan_count_and_hash_from_file(self) -> tuple[int, str]:
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return 0, self.EMPTY_CONTENT_HASH
        count = 0
        content_hash = self.EMPTY_CONTENT_HASH
        with open(self._jsonl_path, "rb") as fh:
            for line in fh:
                if not line.strip():
                    continue
                count += 1
                content_hash = self._hash_line(content_hash, line)
        return count, content_hash

    def _read_validated_sidecar(self) -> Optional[dict]:
        if not self._jsonl_path:
            return None
        sidecar_path = self.sidecar_path_for_jsonl(self._jsonl_path)
        if not os.path.exists(sidecar_path):
            return None
        try:
            with open(sidecar_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            count = int(payload.get("count"))
            size = int(payload.get("size"))
            mtime_ns = int(payload.get("mtime_ns"))
            stat = os.stat(self._jsonl_path)
            if stat.st_size != size or int(stat.st_mtime_ns) != mtime_ns or count < 0:
                return None
            content_hash = payload.get("content_hash")
            hash_algo = payload.get("hash_algo")
            if content_hash is not None:
                if not isinstance(content_hash, str):
                    return None
                if not isinstance(hash_algo, str):
                    return None
            return {
                "count": count,
                "size": size,
                "mtime_ns": mtime_ns,
                "content_hash": content_hash,
                "hash_algo": hash_algo,
                "sorted": bool(payload.get("sorted", False)),
                "sort_key": payload.get("sort_key"),
            }
        except Exception:
            return None

    def _read_sidecar_sort_hints(self) -> Optional[dict]:
        if not self._jsonl_path:
            return None
        sidecar_path = self.sidecar_path_for_jsonl(self._jsonl_path)
        if not os.path.exists(sidecar_path):
            return None
        try:
            with open(sidecar_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            sort_key = payload.get("sort_key")
            sorted_flag = bool(payload.get("sorted", False))
            if sorted_flag and isinstance(sort_key, str) and sort_key:
                return {"sorted": True, "sort_key": sort_key}
        except Exception:
            return None
        return None

    def _write_sidecar(
        self,
        *,
        count: int,
        content_hash: Optional[str],
        hash_algo: Optional[str],
        sorted: Optional[bool] = None,
        sort_key: Optional[str] = None,
    ) -> None:
        if not self._jsonl_path:
            return
        self.write_count_sidecar_for_jsonl(
            self._jsonl_path,
            count,
            content_hash=content_hash,
            hash_algo=hash_algo,
            sorted=sorted,
            sort_key=sort_key,
        )

    @staticmethod
    def _payload_to_jsonl_line(payload: dict) -> str:
        return f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"

    def sidecar_content_signature(self) -> Optional[tuple[int, str]]:
        sidecar = self._read_validated_sidecar()
        if not sidecar:
            return None
        content_hash = sidecar.get("content_hash")
        hash_algo = sidecar.get("hash_algo")
        if not content_hash or hash_algo != self.HASH_ALGO:
            return None
        return sidecar["count"], content_hash

    def sidecar_is_sorted_for(self, sort_key: str) -> bool:
        sidecar = self._read_validated_sidecar()
        if not sidecar:
            return False
        return bool(sidecar.get("sorted")) and sidecar.get("sort_key") == sort_key

    def _iter_file_payloads(self) -> Iterator[dict]:
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return
        with open(self._jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                yield json.loads(raw)

    def _iter_file_items(self) -> Iterator[T]:
        for payload in self._iter_file_payloads():
            yield self._from_dict(payload)

    def _append_payloads_to_file(self, payload_batch: list[dict]) -> list[bytes]:
        if not self._jsonl_path:
            return []
        line_bytes_batch: list[bytes] = []
        with open(self._jsonl_path, "a", encoding="utf-8") as fh:
            for payload in payload_batch:
                line = self._payload_to_jsonl_line(payload)
                fh.write(line)
                line_bytes_batch.append(line.encode("utf-8"))
        return line_bytes_batch

    def __len__(self) -> int:
        return self._count

    def __iter__(self) -> Iterator[T]:
        if self._items is not None:
            return iter(self._items)
        if self._jsonl_path:
            return self._iter_file_items()
        return iter(self._ensure_items_access())

    def __getitem__(self, index):
        if self._items is not None:
            if isinstance(index, slice):
                return self._items[index]
            return self._items[index]

        if not self._jsonl_path:
            data = self._ensure_items_access()
            if isinstance(index, slice):
                return data[index]
            return data[index]

        if isinstance(index, slice):
            start, stop, step = index.indices(self._count)
            wanted = set(range(start, stop, step))
            result: list[T] = []
            for pos, item in enumerate(self._iter_file_items()):
                if pos in wanted:
                    result.append(item)
            return result

        idx = index
        if idx < 0:
            idx += self._count
        if idx < 0 or idx >= self._count:
            raise IndexError("DiskBackedList index out of range")
        for pos, item in enumerate(self._iter_file_items()):
            if pos == idx:
                return item
        raise IndexError("DiskBackedList index out of range")

    def __setitem__(self, index, value) -> None:
        if not self._allow_random_ops:
            raise NotImplementedError("Random mutation is disabled for append-only DiskBackedList.")
        data = self._ensure_items_access()
        if isinstance(index, slice):
            replacement = list(value)
            previous_len = len(data[index])
            data[index] = replacement
            self._count += len(replacement) - previous_len
        else:
            data[index] = value

    def __delitem__(self, index) -> None:
        if not self._allow_random_ops:
            raise NotImplementedError("Random mutation is disabled for append-only DiskBackedList.")
        data = self._ensure_items_access()
        removed_count = len(data[index]) if isinstance(index, slice) else 1
        del data[index]
        self._count -= removed_count

    def insert(self, index: int, value: T) -> None:
        if not self._allow_random_ops:
            raise NotImplementedError("Random mutation is disabled for append-only DiskBackedList.")
        data = self._ensure_items_access()
        data.insert(index, value)
        self._count += 1

    def extend(self, items: Iterable[T]) -> None:  # type: ignore[override]
        object_batch: list[T] = []
        payload_batch: list[dict] = []
        for item in items:
            object_batch.append(item)
            payload_batch.append(self._to_dict(item))
        if not payload_batch:
            return
        old_count = self._count
        old_sidecar = self._read_validated_sidecar() if self._jsonl_path else None
        if self._items is not None:
            self._items.extend(object_batch)
        line_bytes_batch = self._append_payloads_to_file(payload_batch)
        if self._on_append:
            self._on_append(payload_batch)
        self._count += len(payload_batch)
        if self._jsonl_path:
            content_hash: Optional[str] = None
            hash_algo: Optional[str] = None
            if (
                old_sidecar
                and old_sidecar.get("count") == old_count
                and old_sidecar.get("hash_algo") == self.HASH_ALGO
                and isinstance(old_sidecar.get("content_hash"), str)
            ):
                content_hash = old_sidecar["content_hash"]
                for line_bytes in line_bytes_batch:
                    content_hash = self._hash_line(content_hash, line_bytes)
                hash_algo = self.HASH_ALGO
            elif old_count == 0:
                content_hash = self.EMPTY_CONTENT_HASH
                for line_bytes in line_bytes_batch:
                    content_hash = self._hash_line(content_hash, line_bytes)
                hash_algo = self.HASH_ALGO
            else:
                recomputed_count, recomputed_hash = self._scan_count_and_hash_from_file()
                self._count = recomputed_count
                content_hash = recomputed_hash
                hash_algo = self.HASH_ALGO
            self._write_sidecar(
                count=self._count,
                content_hash=content_hash,
                hash_algo=hash_algo,
                sorted=False,
                sort_key=None,
            )

    def append(self, item: T) -> None:  # type: ignore[override]
        self.extend([item])

    def iter_payloads(self) -> Iterator[dict]:
        if self._jsonl_path:
            return self._iter_file_payloads()
        def _payload_iter() -> Iterator[dict]:
            for item in self:
                yield self._to_dict(item)
        return _payload_iter()

    def item_from_payload(self, payload: dict) -> T:
        return self._from_dict(payload)

    def replace_with_payloads(self, payloads: Iterable[dict]) -> None:
        if self._jsonl_path:
            tmp_path = f"{self._jsonl_path}.tmp"
            kept_count = 0
            content_hash = self.EMPTY_CONTENT_HASH
            kept_items: Optional[list[T]] = [] if self._items is not None else None
            with open(tmp_path, "w", encoding="utf-8") as out:
                for payload in payloads:
                    line = self._payload_to_jsonl_line(payload)
                    out.write(line)
                    kept_count += 1
                    content_hash = self._hash_line(content_hash, line.encode("utf-8"))
                    if kept_items is not None:
                        kept_items.append(self._from_dict(payload))
            os.replace(tmp_path, self._jsonl_path)
            self._count = kept_count
            self._write_sidecar(
                count=self._count,
                content_hash=content_hash,
                hash_algo=self.HASH_ALGO,
                sorted=False,
                sort_key=None,
            )
            if kept_items is not None:
                self._items = kept_items
            return

        replaced_items = [self._from_dict(payload) for payload in payloads]
        self._items = replaced_items
        self._count = len(replaced_items)

    def filter_in_place(self, predicate: Callable[[T], bool]) -> None:
        if self._jsonl_path:
            tmp_path = f"{self._jsonl_path}.tmp"
            kept_count = 0
            content_hash = self.EMPTY_CONTENT_HASH
            kept_items: Optional[list[T]] = [] if self._items is not None else None
            with open(tmp_path, "w", encoding="utf-8") as out:
                for payload in self._iter_file_payloads():
                    item = self._from_dict(payload)
                    if not predicate(item):
                        continue
                    line = self._payload_to_jsonl_line(payload)
                    out.write(line)
                    kept_count += 1
                    content_hash = self._hash_line(content_hash, line.encode("utf-8"))
                    if kept_items is not None:
                        kept_items.append(item)
            os.replace(tmp_path, self._jsonl_path)
            self._count = kept_count
            self._write_sidecar(
                count=self._count,
                content_hash=content_hash,
                hash_algo=self.HASH_ALGO,
                sorted=False,
                sort_key=None,
            )
            if kept_items is not None:
                self._items = kept_items
            return

        if self._items is not None:
            self._items = [item for item in self._items if predicate(item)]
            self._count = len(self._items)

    def sort_external_in_place(
        self,
        key_fn: Callable[[dict], Any],
        *,
        sort_key: str,
        chunk_size: int = 100_000,
        tmp_dir: Optional[str] = None,
    ) -> bool:
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return False
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")
        if self.sidecar_is_sorted_for(sort_key):
            return False

        run_root = tmp_dir or os.path.dirname(self._jsonl_path)
        work_dir = tempfile.mkdtemp(prefix=".disk_sort_", dir=run_root)
        run_files: list[str] = []

        def _spill_chunk(chunk_rows: list[tuple[Any, str]]) -> None:
            if not chunk_rows:
                return
            chunk_rows.sort(key=lambda x: x[0])
            run_path = os.path.join(work_dir, f"run_{len(run_files):06d}.jsonl")
            with open(run_path, "w", encoding="utf-8") as run_fh:
                for _, line in chunk_rows:
                    run_fh.write(line)
            run_files.append(run_path)
            chunk_rows.clear()

        def _read_run_line(run_fh) -> Optional[tuple[Any, str]]:
            while True:
                line = run_fh.readline()
                if not line:
                    return None
                raw = line.strip()
                if not raw:
                    continue
                payload = json.loads(raw)
                canonical_line = self._payload_to_jsonl_line(payload)
                return key_fn(payload), canonical_line

        try:
            chunk_rows: list[tuple[Any, str]] = []
            with open(self._jsonl_path, "r", encoding="utf-8") as src:
                for line in src:
                    raw = line.strip()
                    if not raw:
                        continue
                    payload = json.loads(raw)
                    chunk_rows.append((key_fn(payload), self._payload_to_jsonl_line(payload)))
                    if len(chunk_rows) >= chunk_size:
                        _spill_chunk(chunk_rows)
                _spill_chunk(chunk_rows)

            out_tmp = f"{self._jsonl_path}.sort.tmp"
            count = 0
            content_hash = self.EMPTY_CONTENT_HASH
            with open(out_tmp, "w", encoding="utf-8") as out_fh:
                if not run_files:
                    pass
                elif len(run_files) == 1:
                    with open(run_files[0], "r", encoding="utf-8") as run_fh:
                        for line in run_fh:
                            raw = line.strip()
                            if not raw:
                                continue
                            out_fh.write(line)
                            count += 1
                            content_hash = self._hash_line(content_hash, line.encode("utf-8"))
                else:
                    heap: list[tuple[Any, int, str]] = []
                    run_handles = [open(path, "r", encoding="utf-8") for path in run_files]
                    try:
                        for idx, run_fh in enumerate(run_handles):
                            next_row = _read_run_line(run_fh)
                            if next_row is not None:
                                row_key, row_line = next_row
                                heapq.heappush(heap, (row_key, idx, row_line))

                        while heap:
                            _, idx, row_line = heapq.heappop(heap)
                            out_fh.write(row_line)
                            count += 1
                            content_hash = self._hash_line(content_hash, row_line.encode("utf-8"))
                            next_row = _read_run_line(run_handles[idx])
                            if next_row is not None:
                                row_key, row_line = next_row
                                heapq.heappush(heap, (row_key, idx, row_line))
                    finally:
                        for run_fh in run_handles:
                            run_fh.close()

            os.replace(out_tmp, self._jsonl_path)
            self._count = count
            if self._items is not None:
                self._items = list(self._iter_file_items())
            self._write_sidecar(
                count=count,
                content_hash=content_hash,
                hash_algo=self.HASH_ALGO,
                sorted=True,
                sort_key=sort_key,
            )
            return True
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @classmethod
    def for_elements_sink(
        cls,
        on_append: Optional[Callable[[list[dict]], None]] = None,
        *,
        store_items: bool = False,
        jsonl_path: Optional[str] = None,
        truncate: bool = False,
    ) -> "DiskBackedList":
        from tm1_git_py.model.element import Element

        return cls(
            to_dict=lambda elem: elem.to_dict(),
            from_dict=Element.from_dict,
            jsonl_path=jsonl_path,
            truncate=truncate,
            on_append=on_append,
            store_items=store_items,
            allow_random_ops=False,
        )

    @classmethod
    def for_edges_sink(
        cls,
        on_append: Optional[Callable[[list[dict]], None]] = None,
        *,
        store_items: bool = False,
        jsonl_path: Optional[str] = None,
        truncate: bool = False,
    ) -> "DiskBackedList":
        from tm1_git_py.model.edge import Edge

        return cls(
            to_dict=lambda edge: edge.to_dict(),
            from_dict=Edge.from_dict,
            jsonl_path=jsonl_path,
            truncate=truncate,
            on_append=on_append,
            store_items=store_items,
            allow_random_ops=False,
        )

    @classmethod
    def for_subsets_sink(
        cls,
        *,
        on_append: Optional[Callable[[list[dict]], None]] = None,
        store_items: bool = False,
        jsonl_path: Optional[str] = None,
        truncate: bool = False,
    ) -> "DiskBackedList":
        from tm1_git_py.model.subset import Subset

        return cls(
            to_dict=lambda subset: subset.to_dict(),
            from_dict=Subset.from_dict,
            jsonl_path=jsonl_path,
            truncate=truncate,
            on_append=on_append,
            store_items=store_items,
            allow_random_ops=False,
        )
