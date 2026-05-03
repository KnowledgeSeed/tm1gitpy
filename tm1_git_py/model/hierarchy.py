import json
import io
import logging
import os
import re
import time
import uuid
from typing import List, Any, Dict, Optional, Tuple, MutableSequence, Iterator
import TM1py
from TM1py import TM1Service
from TM1py.Utils import format_url
from requests import Response

from tm1_git_py.progress_reporting import ProgressEvent, ProgressSink

# Keep CRUD helpers imported in module namespace for compatibility with existing patches/tests.
from .element import Element, create_element, delete_element, update_element
from .edge import Edge
from .subset import Subset
from .model_store import ModelStore
from .store_backed_sequence import StoreBackedSequence
from .tm1git_json import dump_as_tm1git
import orjson


logger = logging.getLogger(__name__)


def _loads_json(payload_json: str) -> dict:
    return dict(orjson.loads(payload_json))


def _write_json_object_block(
    fh,
    obj: dict,
    *,
    item_line_prefix: str,
) -> None:
    """Write one flat JSON object (array element) with tabs and compact ``\"Key\":value`` (tm1git-style)."""
    if not obj:
        fh.write(item_line_prefix + "{}")
        return
    inner = item_line_prefix + "\t"
    fh.write(item_line_prefix + "{\n")
    items = list(obj.items())
    for i, (k, v) in enumerate(items):
        if isinstance(v, (dict, list)):
            raise TypeError(
                f"Hierarchy JSON item must be flat; got {type(v).__name__} for key {k!r}"
            )
        kj = json.dumps(k, ensure_ascii=False)
        fh.write(inner + kj + ":" + json.dumps(v, ensure_ascii=False))
        if i != len(items) - 1:
            fh.write(",\n")
        else:
            fh.write("\n")
    fh.write(item_line_prefix + "}")


def _write_hierarchy_subset_links_field(fh, subset_links: list[str]) -> None:
    fh.write('\t"Subsets@Code.links":')
    if subset_links:
        fh.write("\n\t")
    dump_as_tm1git(subset_links, fh, level=1)
    fh.write("\n")


class _HierarchyStagedWriter:
    """SQLite-backed hierarchy writer with streaming finalize."""
    JSON_DUMP_PROGRESS_EVERY = 100_000

    def __init__(
        self,
        model_output_dir: str,
        dimension_name: str,
        hierarchy: Any,
    ):
        self.hierarchy = hierarchy
        self.hierarchy_name = hierarchy.name
        final_parent_dir = os.path.join(model_output_dir, "dimensions", f"{dimension_name}.hierarchies")
        os.makedirs(final_parent_dir, exist_ok=True)
        self.final_path = os.path.join(final_parent_dir, f"{self.hierarchy_name}.json")
        self.inprogress_path = os.path.join(
            final_parent_dir,
            f".{self.hierarchy_name}.{uuid.uuid4().hex}.json.inprogress",
        )
        self.elements_ref: Any = None
        self.edges_ref: Any = None
        self.subsets_ref: Any = None
        self._finalized = False

    def bind_collections(self) -> None:
        self.elements_ref = self.hierarchy.elements
        self.edges_ref = self.hierarchy.edges
        self.subsets_ref = self.hierarchy.subsets

    def _write_payload_array_from_json_strings(
        self,
        fh,
        key: str,
        payload_json_iter: Iterator[str],
        *,
        progress_sink: Optional[ProgressSink] = None,
        progress_message: Optional[str] = None,
        progress_total: Optional[int] = None,
    ) -> int:
        fh.write(f'\t"{key}":\n\t[')
        first = True
        emitted = 0
        progress_every = max(1, self.JSON_DUMP_PROGRESS_EVERY)
        for payload_json in payload_json_iter:
            if first:
                fh.write("\n")
                first = False
            else:
                fh.write(",\n")
            emitted += 1
            fh.write("\t\t")
            fh.write(payload_json)
            if progress_sink is not None and emitted % progress_every == 0:
                progress_sink.on_event(
                    ProgressEvent.worker_line(
                        current=emitted,
                        total=progress_total,
                        message=progress_message,
                        path=self.final_path,
                    )
                )
        if first:
            fh.write("]")
        else:
            fh.write("\n\t]")
        if progress_sink is not None:
            progress_sink.on_event(
                ProgressEvent.worker_line(
                    current=emitted,
                    total=progress_total,
                    message=progress_message,
                    path=self.final_path,
                )
            )
        return emitted

    def _build_subset_links(
        self,
        *,
        progress_sink: Optional[ProgressSink] = None,
        progress_total: Optional[int] = None,
    ) -> list[str]:
        links: list[str] = []
        if self.subsets_ref is None:
            return links
        progress_every = max(1, self.JSON_DUMP_PROGRESS_EVERY)
        scanned = 0
        for payload_json in self.subsets_ref.iter_payload_json_strings(ordered_by_identity=True):
            scanned += 1
            payload = _loads_json(payload_json)
            subset_name = payload.get("name") or payload.get("Name")
            if not subset_name:
                continue
            links.append(format_url("{}.subsets/{}.json", self.hierarchy_name, subset_name))
            if progress_sink is not None and scanned % progress_every == 0:
                progress_sink.on_event(
                    ProgressEvent.worker_line(
                        current=scanned,
                        total=progress_total,
                        message=f"Building subset links ({self.hierarchy_name})",
                        path=self.final_path,
                    )
                )
        if progress_sink is not None:
            progress_sink.on_event(
                ProgressEvent.worker_line(
                    current=scanned,
                    total=progress_total,
                    message=f"Building subset links ({self.hierarchy_name})",
                    path=self.final_path,
                )
            )
        return links

    def _sort_staged_jsonls(self) -> None:
        if self.elements_ref is None or self.edges_ref is None or self.subsets_ref is None:
            return
        for collection_name, collection_ref in (
            ("Elements", self.elements_ref),
            ("Edges", self.edges_ref),
            ("Subsets", self.subsets_ref),
        ):
            count = len(collection_ref)
            logger.info(
                "Finalization order check hierarchy='%s' collection=%s count=%d",
                self.hierarchy_name,
                collection_name,
                count,
            )
            logger.info(
                "Finalization order done hierarchy='%s' collection=%s count=%d",
                self.hierarchy_name,
                collection_name,
                len(collection_ref),
            )

    @staticmethod
    def _sorted_element_payload_json_strings(payload_json_iter: Iterator[str]) -> Iterator[str]:
        payloads = [_loads_json(payload_json) for payload_json in payload_json_iter]
        # payloads.sort(key=lambda payload: ((payload.get("Name") or payload.get("name") or ""), (payload.get("Type") or payload.get("type") or "")))
        for payload in payloads:
            yield json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _set_final_serialized_path_mtime(
        sequence: Any,
        *,
        final_mtime_ns: int,
    ) -> None:
        if hasattr(sequence, "set_source_json_mtime_ns"):
            sequence.set_source_json_mtime_ns(final_mtime_ns)

    def _finalize_sequence(
        self,
        sequence: Any,
        *,
        final_mtime_ns: int,
        signature: Optional[tuple[int, str]] = None,
    ) -> None:
        self._set_final_serialized_path_mtime(sequence, final_mtime_ns=final_mtime_ns)
        if signature is None or not hasattr(sequence, "_store"):
            return
        row_count, content_hash = signature
        sequence._store.commit_group_content_signature(
            sequence.group_id,
            row_count=row_count,
            content_hash=content_hash,
        )

    def finalize(
        self,
        *,
        content_signatures: Optional[dict[str, tuple[int, str]]] = None,
        assume_serialized: bool = False,
    ) -> str:
        if not os.path.exists(self.final_path):
            self.serialize_hierarchy_json()
        elif not assume_serialized and not self._finalized:
            self.serialize_hierarchy_json()
        if not os.path.exists(self.final_path):
            raise FileNotFoundError(self.final_path)
        final_mtime_ns = int(os.stat(self.final_path).st_mtime_ns)
        signatures = content_signatures or {}
        if self.elements_ref is not None:
            self._finalize_sequence(
                self.elements_ref,
                final_mtime_ns=final_mtime_ns,
                signature=signatures.get("elements"),
            )
        
        if self.edges_ref is not None:
            self._finalize_sequence(
                self.edges_ref,
                final_mtime_ns=final_mtime_ns,
                signature=signatures.get("edges"),
            )
        if self.subsets_ref is not None:
            self._finalize_sequence(
                self.subsets_ref,
                final_mtime_ns=final_mtime_ns,
                signature=signatures.get("subsets"),
            )
        return self.final_path

    def serialize_hierarchy_json(self, progress_sink: Optional[ProgressSink] = None) -> str:
        if self._finalized and os.path.exists(self.final_path):
            logger.debug("Skipping finalize for hierarchy='%s' (already finalized)", self.hierarchy_name)
            return self.final_path
        total_started = time.perf_counter()
        elements_count = len(self.elements_ref) if self.elements_ref is not None else 0
        edges_count = len(self.edges_ref) if self.edges_ref is not None else 0
        subsets_count = len(self.subsets_ref) if self.subsets_ref is not None else 0
        total_rows = elements_count + edges_count + subsets_count
        logger.info(
            "Starting hierarchy finalization hierarchy='%s' target='%s'",
            self.hierarchy_name,
            self.final_path,
        )
        if progress_sink is not None:
            progress_sink.on_event(
                ProgressEvent.worker_line(
                    current=0,
                    total=max(1, total_rows),
                    message=f"Serializing hierarchy ({self.hierarchy_name})",
                    path=self.final_path,
                )
            )
        # self._sort_staged_jsonls()
        with open(self.inprogress_path, "w", encoding="utf-8") as fh:
            fh.write("{\n")
            fh.write('\t"@type":' + json.dumps("Hierarchy", ensure_ascii=False) + ",\n")
            fh.write("\t\"Name\":" + json.dumps(self.hierarchy_name, ensure_ascii=False) + ",\n")
            elements_empty = elements_count == 0
            if elements_empty:
                fh.write('\t"Elements":[],\n')
            else:
                elements_started = time.perf_counter()
                self._write_payload_array_from_json_strings(
                    fh,
                    "Elements",
                    self.elements_ref.iter_payload_json_strings(
                        ordered_by_identity=True,
                        progress_label=f"{self.hierarchy_name}:Elements",
                        progress_every=self.JSON_DUMP_PROGRESS_EVERY,
                    ),
                    progress_sink=progress_sink,
                    progress_message=f"Writing elements ({self.hierarchy_name})",
                    progress_total=elements_count,
                )
                logger.info(
                    "Hierarchy serialize elements write duration hierarchy='%s' duration_ms=%.3f",
                    self.hierarchy_name,
                    (time.perf_counter() - elements_started) * 1000,
                )
                fh.write(",\n")
            edges_nonempty = edges_count > 0
            if edges_nonempty:
                edges_started = time.perf_counter()
                self._write_payload_array_from_json_strings(
                    fh,
                    "Edges",
                    self.edges_ref.iter_payload_json_strings(
                        ordered_by_identity=True,
                        progress_label=f"{self.hierarchy_name}:Edges",
                        progress_every=self.JSON_DUMP_PROGRESS_EVERY,
                    ),
                    progress_sink=progress_sink,
                    progress_message=f"Writing edges ({self.hierarchy_name})",
                    progress_total=edges_count,
                )
                logger.info(
                    "Hierarchy serialize edges write duration hierarchy='%s' duration_ms=%.3f",
                    self.hierarchy_name,
                    (time.perf_counter() - edges_started) * 1000,
                )
                fh.write(",\n")
            subset_links_started = time.perf_counter()
            subset_links = self._build_subset_links(
                progress_sink=progress_sink,
                progress_total=subsets_count,
            )
            logger.info(
                "Hierarchy serialize subset-links build duration hierarchy='%s' duration_ms=%.3f",
                self.hierarchy_name,
                (time.perf_counter() - subset_links_started) * 1000,
            )
            _write_hierarchy_subset_links_field(fh, subset_links)
            fh.write("}")
        os.replace(self.inprogress_path, self.final_path)
        
        logger.info(
            "Completed hierarchy finalization hierarchy='%s' target='%s'",
            self.hierarchy_name,
            self.final_path,
        )
        logger.info(
            "Hierarchy serialize total duration hierarchy='%s' duration_ms=%.3f",
            self.hierarchy_name,
            (time.perf_counter() - total_started) * 1000,
        )
        if progress_sink is not None:
            progress_sink.on_event(
                ProgressEvent.worker_line(
                    current=max(1, total_rows),
                    total=max(1, total_rows),
                    message=f"Serialized hierarchy ({self.hierarchy_name})",
                    path=self.final_path,
                )
            )
        self._finalized = True
        return self.final_path


class Hierarchy:
    def __init__(
        self,
        name,
        elements: Optional[MutableSequence[Element]] = None,
        edges: Optional[MutableSequence[Edge]] = None,
        subsets: Optional[MutableSequence[Subset]] = None,
        *,
        dimension_name: Optional[str] = None,
        model_id: Optional[str] = None,
        serialize: bool = False,
        hierarchy_etag: Optional[str] = None,
        reuse_existing_store: bool = False,
        elements_filter_rules: Optional[list[str]] = None,
        edges_filter_rules: Optional[list[str]] = None,
        subsets_filter_rules: Optional[list[str]] = None,
    ):
        _ = serialize, elements_filter_rules, edges_filter_rules, subsets_filter_rules
        if model_id:
            if not dimension_name:
                raise ValueError("Hierarchy with model_id requires dimension_name.")
            if elements is not None or edges is not None or subsets is not None:
                raise ValueError(
                    "Hierarchy with model_id should not provide explicit elements/edges/subsets."
                )
            store = ModelStore.for_model_id(model_id)
            elements = StoreBackedSequence.for_elements_sink(
                store=store,
                model_id=model_id,
                dimension_name=dimension_name,
                hierarchy_name=name,
            )
            edges = StoreBackedSequence.for_edges_sink(
                store=store,
                model_id=model_id,
                dimension_name=dimension_name,
                hierarchy_name=name,
            )
            subsets = StoreBackedSequence.for_subsets_sink(
                store=store,
                model_id=model_id,
                dimension_name=dimension_name,
                hierarchy_name=name,
            )
            if not reuse_existing_store:
                elements.replace_with_payloads(())
                edges.replace_with_payloads(())
                subsets.replace_with_payloads(())

        self.type = 'Hierarchy'
        self.name = name
        self.hierarchy_etag = hierarchy_etag
        self.elements = elements if elements is not None else []
        self.edges = edges if edges is not None else []
        self.subsets = subsets if subsets is not None else []

    def as_json(self):
        buf = io.StringIO()
        self.write_json(buf)
        return buf.getvalue()

    def persist_elements_etag(self) -> None:
        if hasattr(self.elements, "set_etag"):
            self.elements.set_etag(self.hierarchy_etag)

    def persist_edges_etag(self) -> None:
        if hasattr(self.edges, "set_etag"):
            self.edges.set_etag(self.hierarchy_etag)

    def persist_subsets_etag(self) -> None:
        if hasattr(self.subsets, "set_etag"):
            self.subsets.set_etag(self.hierarchy_etag)

    def _write_array(self, fh, key: str, collection: MutableSequence[Any], *, indent: str = "\t") -> None:
        item_prefix = indent * 2
        fh.write(f'{indent}"{key}":\n{indent}[')
        if key == "Elements":
            items = list(collection)
            items.sort(key=lambda item: ((getattr(item, "name", None) or ""), (getattr(item, "type", None) or "")))
        else:
            items = list(collection)
        first = True
        for item in items:
            if first:
                fh.write("\n")
                first = False
            else:
                fh.write(",\n")
            _write_json_object_block(fh, item.to_dict(), item_line_prefix=item_prefix)
        if not first:
            fh.write(f"\n{indent}]")
        else:
            fh.write("]")

    def write_json(self, fh) -> None:
        fh.write("{\n")
        fh.write("\t\"@type\":" + json.dumps(self.type, ensure_ascii=False) + ",\n")
        fh.write("\t\"Name\":" + json.dumps(self.name, ensure_ascii=False) + ",\n")
        if not self.elements:
            fh.write('\t"Elements":[],\n')
        else:
            self._write_array(fh, "Elements", self.elements)
            fh.write(",\n")
        if self.edges:
            self._write_array(fh, "Edges", self.edges)
            fh.write(",\n")
        subset_links = [format_url("{}.subsets/{}.json", self.name, s.name) for s in self.subsets]
        _write_hierarchy_subset_links_field(fh, subset_links)
        fh.write("}")

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Hierarchy):
            return NotImplemented
        
        if self.name != other.name:
            return False
        
        if set(self.elements) != set(other.elements):
            return False

        if set(self.edges) != set(other.edges):
            return False
        
        if set(self.subsets) != set(other.subsets):
            return False

        return True

    def __hash__(self) -> int:
        return hash((
            self.name,
            frozenset(self.elements),
            frozenset(self.edges),
            frozenset(self.subsets),
        ))
    
    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            'name': self.name,
            'elements': [e.to_dict() for e in self.elements],
            'edges': [e.to_dict() for e in self.edges],
            'subsets': [s.to_dict() for s in self.subsets],
        }

    @classmethod
    def from_dict(
            cls,
            data: Dict[str, Any]
    ) -> "Hierarchy":

        name = data.get("name") or data.get("Name")
     
        element_payloads = data.get("elements") or data.get("Elements") or []
        edge_payloads = data.get("edges") or data.get("Edges") or []
        subset_payloads = data.get("subsets") or data.get("Subsets") or []

        elements: List[Element] = []
        for payload in element_payloads:
            elements.append(Element.from_dict(payload))

        edges: List[Edge] = []
        for payload in edge_payloads:
            edges.append(Edge.from_dict(payload))

        subsets: List[Subset] = []
        for payload in subset_payloads:
            subsets.append(Subset.from_dict(payload))

        return cls(
            name=name,
            elements=elements,
            edges=edges,
            subsets=subsets,
        )

    @staticmethod
    def uri_for(dimension_name: str, hierarchy_name: str) -> str:
        return f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')"

    def uri(self, dimension_name: str) -> Optional[str]:
        if not dimension_name or not self.name:
            return None
        return self.uri_for(dimension_name, self.name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _hierarchy_context_from_uri(uri: str) -> Tuple[str, str]:
    match = re.search(r"^Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)$", uri or "")
    if not match:
        raise ValueError(f"Invalid hierarchy uri format: '{uri}'")
    dimension_name, hierarchy_name = match.groups()
    return dimension_name, hierarchy_name


def create_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy, uri: Optional[str] = None) -> Response:
    dimension_name, _ = _hierarchy_context_from_uri(uri)
    hierarchy_object = TM1py.Hierarchy(name=hierarchy.name, dimension_name=dimension_name)
    response = tm1_service.hierarchies.create(hierarchy_object)
    logger.info(f"Created Hierarchy: {hierarchy.name}.")

    return response


def update_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy, uri: Optional[str] = None) -> Response:
    dimension_name, _ = _hierarchy_context_from_uri(uri)
    logger.info("Skipping direct Hierarchy update for '%s'; updates are handled by child changes.", hierarchy.name)
    return _build_noop_update_response(
        resource_url=format_url("/api/v1/Dimensions('{}')/Hierarchies('{}')", dimension_name, hierarchy.name),
        message=f"No-op Hierarchy update for '{hierarchy.name}'."
    )


def delete_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy, uri: Optional[str] = None) -> Response:
    dimension_name, _ = _hierarchy_context_from_uri(uri)
    logger.info(f"Deleting Hierarchy: {hierarchy.name} of Dimension: {dimension_name}.")
    return tm1_service.hierarchies.delete(dimension_name=dimension_name, hierarchy_name=hierarchy.name)


def _build_noop_update_response(resource_url: str, message: str) -> Response:
    response = Response()
    response.status_code = 200
    response.url = resource_url
    response._content = message.encode("utf-8")
    response.encoding = "utf-8"
    return response
