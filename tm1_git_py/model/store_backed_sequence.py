from typing import Any, Callable, Generic, Iterable, Iterator, MutableSequence, Optional, TypeVar

from tm1_git_py.model.edge import Edge
from tm1_git_py.model.element import Element
from tm1_git_py.model.model_store import ModelStore
from tm1_git_py.model.subset import Subset

T = TypeVar("T")


def _element_identity_from_payload(payload: dict[str, Any]) -> str:
    return "|".join(
        (
            str(payload.get("Name") or payload.get("name") or ""),
            str(payload.get("Type") or payload.get("type") or ""),
        )
    )


def _edge_identity_from_payload(payload: dict[str, Any]) -> str:
    weight = payload.get("Weight")
    if weight is None:
        weight = payload.get("weight")
    return "|".join(
        (
            str(payload.get("ParentName") or payload.get("parentName") or payload.get("parent") or ""),
            str(payload.get("ComponentName") or payload.get("componentName") or payload.get("name") or ""),
            str(weight if weight is not None else ""),
        )
    )


def _subset_identity_from_payload(payload: dict[str, Any]) -> str:
    return "|".join(
        (
            str(payload.get("name") or payload.get("Name") or ""),
            str(payload.get("expression") or payload.get("Expression") or ""),
        )
    )


class StoreBackedSequence(MutableSequence[T], Generic[T]):
    def __init__(
        self,
        *,
        store: ModelStore,
        model_id: Optional[int] = None,
        dimension_name: str,
        hierarchy_name: str,
        object_type: str,
        item_from_payload: Callable[[dict[str, Any]], T],
        payload_from_item: Callable[[T], dict[str, Any]],
        identity_from_payload: Callable[[dict[str, Any]], str],
        on_append: Optional[Callable[[list[dict[str, Any]]], None]] = None,
    ):
        self._store = store
        self._dimension_name = dimension_name
        self._hierarchy_name = hierarchy_name
        self._object_type = object_type
        self._item_from_payload = item_from_payload
        self._payload_from_item = payload_from_item
        self._identity_from_payload = identity_from_payload
        self._on_append = on_append
        self.group_id = self._store.ensure_group(
            dimension_name,
            hierarchy_name,
            object_type,
            model_id=model_id,
        )

    @classmethod
    def for_elements_sink(
        cls,
        *,
        store: ModelStore,
        model_id: Optional[int] = None,
        dimension_name: str,
        hierarchy_name: str,
        on_append: Optional[Callable[[list[dict[str, Any]]], None]] = None,
    ) -> "StoreBackedSequence[Element]":
        return cls(
            store=store,
            model_id=model_id,
            dimension_name=dimension_name,
            hierarchy_name=hierarchy_name,
            object_type="elements",
            item_from_payload=Element.from_dict,
            payload_from_item=lambda item: item.to_dict(),
            identity_from_payload=_element_identity_from_payload,
            on_append=on_append,
        )

    @classmethod
    def for_edges_sink(
        cls,
        *,
        store: ModelStore,
        model_id: Optional[int] = None,
        dimension_name: str,
        hierarchy_name: str,
        on_append: Optional[Callable[[list[dict[str, Any]]], None]] = None,
    ) -> "StoreBackedSequence[Edge]":
        return cls(
            store=store,
            model_id=model_id,
            dimension_name=dimension_name,
            hierarchy_name=hierarchy_name,
            object_type="edges",
            item_from_payload=Edge.from_dict,
            payload_from_item=lambda item: item.to_dict(),
            identity_from_payload=_edge_identity_from_payload,
            on_append=on_append,
        )

    @classmethod
    def for_subsets_sink(
        cls,
        *,
        store: ModelStore,
        model_id: Optional[int] = None,
        dimension_name: str,
        hierarchy_name: str,
        on_append: Optional[Callable[[list[dict[str, Any]]], None]] = None,
    ) -> "StoreBackedSequence[Subset]":
        return cls(
            store=store,
            model_id=model_id,
            dimension_name=dimension_name,
            hierarchy_name=hierarchy_name,
            object_type="subsets",
            item_from_payload=Subset.from_dict,
            payload_from_item=lambda item: item.to_dict(),
            identity_from_payload=_subset_identity_from_payload,
            on_append=on_append,
        )

    def __len__(self) -> int:
        return self._store.row_count(self.group_id)

    def __iter__(self) -> Iterator[T]:
        for payload in self.iter_payloads():
            yield self._item_from_payload(payload)

    def __getitem__(self, index: int) -> T:
        if not isinstance(index, int):
            raise TypeError("StoreBackedSequence supports integer indexes only.")
        if index < 0:
            index = len(self) + index
        rows = list(self.iter_payloads())
        return self._item_from_payload(rows[index])

    def __setitem__(self, index: int, value: T) -> None:
        raise NotImplementedError("Index assignment is not supported for StoreBackedSequence.")

    def __delitem__(self, index: int) -> None:
        raise NotImplementedError("Index deletion is not supported for StoreBackedSequence.")

    def insert(self, index: int, value: T) -> None:
        raise NotImplementedError("Insert is not supported for StoreBackedSequence.")

    def append(self, value: T) -> None:  # type: ignore[override]
        self.extend([value])

    def extend(self, values: Iterable[T]) -> None:  # type: ignore[override]
        payloads = [self._payload_from_item(item) for item in values]
        if not payloads:
            return
        self._store.append_payloads(
            self.group_id,
            payloads,
            self._identity_from_payload,
        )
        if self._on_append:
            self._on_append(payloads)

    def iter_payloads(self, *, ordered_by_identity: bool = False) -> Iterator[dict[str, Any]]:
        yield from self._store.iter_payloads(self.group_id, ordered_by_identity=ordered_by_identity)

    def iter_payload_json_strings(
        self,
        *,
        ordered_by_identity: bool = False,
        progress_label: Optional[str] = None,
        progress_every: int = 10_000,
    ) -> Iterator[str]:
        yield from self._store.iter_payload_json_strings(
            self.group_id,
            ordered_by_identity=ordered_by_identity,
            progress_label=progress_label,
            progress_every=progress_every,
        )

    def item_from_payload(self, payload: dict[str, Any]) -> T:
        return self._item_from_payload(payload)

    def replace_with_payloads(
        self,
        payloads: Iterable[dict[str, Any]],
    ) -> None:
        self._store.replace_group_payloads(
            self.group_id,
            payloads,
            self._identity_from_payload,
        )

    def filter_in_place(self, predicate: Callable[[T], bool]) -> int:
        kept_payloads = []
        for payload in self.iter_payloads():
            item = self._item_from_payload(payload)
            if predicate(item):
                kept_payloads.append(payload)
        self.replace_with_payloads(kept_payloads)
        return len(kept_payloads)

    def sidecar_content_signature(self) -> Optional[tuple[int, str]]:
        return self._store.content_signature(self.group_id)

    def set_source_json_mtime_ns(self, source_json_mtime_ns: int) -> None:
        self._store.set_source_json_mtime_ns(self.group_id, source_json_mtime_ns)

    def source_json_mtime_ns(self) -> Optional[int]:
        return self._store.source_json_mtime_ns(self.group_id)

    def set_etag(self, etag: Optional[str]) -> None:
        self._store.set_group_etag(self.group_id, etag)

    def etag(self) -> Optional[str]:
        return self._store.group_etag(self.group_id)

    def set_filter_rules(self, filter_rules: list[str]) -> None:
        self._store.set_group_filter_rules(self.group_id, filter_rules)

    def filter_rules(self) -> list[str]:
        return self._store.group_filter_rules(self.group_id)
