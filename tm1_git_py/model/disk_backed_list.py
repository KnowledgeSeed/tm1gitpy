import json
import os
from collections.abc import Iterable, Iterator, MutableSequence
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class DiskBackedList(MutableSequence[T], Generic[T]):
    """Append-optimized list-like collection with optional JSONL backing."""

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
            self._count = self._count_lines()
            if self._items is not None and self._count:
                self._items.extend(list(self._iter_file_items()))

    def _ensure_items_access(self) -> list[T]:
        if self._items is None:
            raise NotImplementedError(
                "This DiskBackedList is append-only and does not retain items in memory."
            )
        return self._items

    def _count_lines(self) -> int:
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return 0
        count = 0
        with open(self._jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return count

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

    def _append_payloads_to_file(self, payload_batch: list[dict]) -> None:
        if not self._jsonl_path:
            return
        with open(self._jsonl_path, "a", encoding="utf-8") as fh:
            for payload in payload_batch:
                fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                fh.write("\n")

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
        if self._items is not None:
            self._items.extend(object_batch)
        self._append_payloads_to_file(payload_batch)
        if self._on_append:
            self._on_append(payload_batch)
        self._count += len(payload_batch)

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
            kept_items: Optional[list[T]] = [] if self._items is not None else None
            with open(tmp_path, "w", encoding="utf-8") as out:
                for payload in payloads:
                    out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                    out.write("\n")
                    kept_count += 1
                    if kept_items is not None:
                        kept_items.append(self._from_dict(payload))
            os.replace(tmp_path, self._jsonl_path)
            self._count = kept_count
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
            kept_items: Optional[list[T]] = [] if self._items is not None else None
            with open(tmp_path, "w", encoding="utf-8") as out:
                for payload in self._iter_file_payloads():
                    item = self._from_dict(payload)
                    if not predicate(item):
                        continue
                    out.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                    out.write("\n")
                    kept_count += 1
                    if kept_items is not None:
                        kept_items.append(item)
            os.replace(tmp_path, self._jsonl_path)
            self._count = kept_count
            if kept_items is not None:
                self._items = kept_items
            return

        if self._items is not None:
            self._items = [item for item in self._items if predicate(item)]
            self._count = len(self._items)

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
