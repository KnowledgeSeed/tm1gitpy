import json
import io
import logging
import os
import re
from typing import List, Any, Dict, Optional, Tuple, MutableSequence, Iterator
import TM1py
from TM1py import TM1Service
from TM1py.Utils import format_url
from requests import Response

from .element import Element, create_element, delete_element, update_element
from .edge import Edge
from .subset import Subset
from .disk_backed_list import DiskBackedList

# {
# 	"@type": "Hierarchy",
# 	"Name": "Capex Balance Sheet Assignment Measure",
# 	"Elements": [
# 		{
# 			"Name": "Assignment",
# 			"Type": "Numeric"
# 		},
# 		{
# 			"Name": "Comment",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "CapexName",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "BalanceSheetName",
# 			"Type": "String"
# 		},
# 		{
# 			"Name": "Value",
# 			"Type": "Numeric"
# 		}
# 	],
# 	"Subsets@Code.links": []
# }


class _HierarchyStagedWriter:
    """JSONL-backed hierarchy writer with streaming finalize."""

    def __init__(self, model_output_dir: str, dimension_name: str, hierarchy_name: str):
        final_parent_dir = os.path.join(model_output_dir, "dimensions", f"{dimension_name}.hierarchies")
        internal_parent_dir = os.path.join(model_output_dir, ".dimensions", f"{dimension_name}.hierarchies")
        os.makedirs(final_parent_dir, exist_ok=True)
        os.makedirs(internal_parent_dir, exist_ok=True)
        self.final_path = os.path.join(final_parent_dir, f"{hierarchy_name}.json")
        self.inprogress_path = os.path.join(internal_parent_dir, f".{hierarchy_name}.json.inprogress")
        self.hierarchy_name = hierarchy_name
        self.elements_jsonl_path = os.path.join(internal_parent_dir, f".{hierarchy_name}.elements.jsonl")
        self.edges_jsonl_path = os.path.join(internal_parent_dir, f".{hierarchy_name}.edges.jsonl")
        self.subsets_jsonl_path = os.path.join(internal_parent_dir, f".{hierarchy_name}.subsets.jsonl")
        for path in (
            self.inprogress_path,
            self.elements_jsonl_path,
            self.edges_jsonl_path,
            self.subsets_jsonl_path,
        ):
            if os.path.exists(path):
                os.remove(path)
        self._elements_count = 0
        self._edges_count = 0
        self._subsets_count = 0
        self._finalized = False

    @staticmethod
    def _iter_jsonl(path: str) -> Iterator[dict]:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                yield json.loads(raw)

    def _append_jsonl(self, path: str, payloads: list[dict]) -> None:
        if self._finalized:
            raise RuntimeError("Cannot append to finalized staged hierarchy writer.")
        if not payloads:
            return
        with open(path, "a", encoding="utf-8") as fh:
            for payload in payloads:
                fh.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
                fh.write("\n")

    def append_elements(self, payloads: list[dict]) -> None:
        self._append_jsonl(self.elements_jsonl_path, payloads)
        self._elements_count += len(payloads)

    def append_edges(self, payloads: list[dict]) -> None:
        self._append_jsonl(self.edges_jsonl_path, payloads)
        self._edges_count += len(payloads)

    def append_subsets(self, payloads: list[dict]) -> None:
        self._append_jsonl(self.subsets_jsonl_path, payloads)
        self._subsets_count += len(payloads)

    def _write_payload_array(self, fh, key: str, payloads: Iterator[dict]) -> None:
        fh.write(f'\t"{key}":[')
        first = True
        for payload in payloads:
            if first:
                fh.write("\n")
                first = False
            else:
                fh.write(",\n")
            pretty_payload = json.dumps(payload, ensure_ascii=False, indent="\t")
            fh.write("\t\t")
            fh.write(pretty_payload.replace("\n", "\n\t\t"))
        if first:
            fh.write("]")
        else:
            fh.write("\n\t]")

    def _build_subset_links(self) -> list[str]:
        links: list[str] = []
        for payload in self._iter_jsonl(self.subsets_jsonl_path):
            subset_name = payload.get("name") or payload.get("Name")
            if not subset_name:
                continue
            links.append(format_url("{}.subsets/{}.json", self.hierarchy_name, subset_name))
        return links

    def finalize(self) -> str:
        if self._finalized:
            return self.final_path
        with open(self.inprogress_path, "w", encoding="utf-8") as fh:
            fh.write("{\n")
            fh.write('\t"@type":"Hierarchy",\n')
            fh.write(f'\t"Name":{json.dumps(self.hierarchy_name, ensure_ascii=False)},\n')
            self._write_payload_array(fh, "Elements", self._iter_jsonl(self.elements_jsonl_path))
            fh.write(",\n")
            self._write_payload_array(fh, "Edges", self._iter_jsonl(self.edges_jsonl_path))
            fh.write(",\n")
            fh.write('\t"Subsets@Code.links":')
            fh.write(json.dumps(self._build_subset_links(), ensure_ascii=False))
            fh.write("\n}")
        os.replace(self.inprogress_path, self.final_path)
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
        internal_model_dir: Optional[str] = None,
    ):
        self._staged_writer: Optional[_HierarchyStagedWriter] = None
        if internal_model_dir:
            if not dimension_name:
                raise ValueError("Hierarchy with internal_model_dir requires dimension_name.")
            if elements is not None or edges is not None or subsets is not None:
                raise ValueError(
                    "Hierarchy with internal_model_dir should not provide explicit elements/edges/subsets."
                )
            self._staged_writer = _HierarchyStagedWriter(
                model_output_dir=internal_model_dir,
                dimension_name=dimension_name,
                hierarchy_name=name,
            )
            elements = DiskBackedList.for_elements_sink(
                store_items=False,
                jsonl_path=self._staged_writer.elements_jsonl_path,
            )
            edges = DiskBackedList.for_edges_sink(
                store_items=False,
                jsonl_path=self._staged_writer.edges_jsonl_path,
            )
            subsets = DiskBackedList.for_subsets_sink(
                store_items=False,
                jsonl_path=self._staged_writer.subsets_jsonl_path,
            )

        self.type = 'Hierarchy'
        self.name = name
        self.elements = elements if elements is not None else []
        self.edges = edges if edges is not None else []
        self.subsets = subsets if subsets is not None else []

    def as_json(self):
        buf = io.StringIO()
        self.write_json(buf)
        return buf.getvalue()

    def finalize_staged_json(self) -> Optional[str]:
        if not self._staged_writer:
            return None
        return self._staged_writer.finalize()

    def _iter_collection_json_items(self, collection: MutableSequence[Any]) -> Iterator[str]:
        for item in collection:
            yield json.dumps(item.to_dict(), ensure_ascii=False, separators=(",", ":"))

    def _write_array(self, fh, key: str, values: Iterator[str], *, indent: str = "\t") -> None:
        fh.write(f'{indent}"{key}":[')
        first = True
        for raw_json in values:
            if first:
                fh.write("\n")
                first = False
            else:
                fh.write(",\n")
            fh.write(f"{indent}{indent}{raw_json}")
        if not first:
            fh.write(f"\n{indent}]")
        else:
            fh.write("]")

    def write_json(self, fh) -> None:
        if self._staged_writer:
            final_path = self.finalize_staged_json()
            target_path = getattr(fh, "name", None)
            if isinstance(target_path, str):
                try:
                    if os.path.abspath(target_path) == os.path.abspath(final_path):
                        return
                except Exception:
                    pass
            with open(final_path, "r", encoding="utf-8") as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
            return
        fh.write("{\n")
        fh.write(f'\t"@type":{json.dumps(self.type, ensure_ascii=False)},\n')
        fh.write(f'\t"Name":{json.dumps(self.name, ensure_ascii=False)},\n')
        self._write_array(fh, "Elements", self._iter_collection_json_items(self.elements))
        fh.write(",\n")
        self._write_array(fh, "Edges", self._iter_collection_json_items(self.edges))
        fh.write(",\n")
        subset_links = [format_url("{}.subsets/{}.json", self.name, s.name) for s in self.subsets]
        fh.write(f'\t"Subsets@Code.links":{json.dumps(subset_links, ensure_ascii=False)}\n')
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

logger = logging.getLogger(__name__)

def _hierarchy_context_from_path(source_path: str) -> Tuple[str, str]:
    dimension_name = re.search(r'/([\w}]*)(.hierarchies)', source_path).group(1)
    hierarchy_name = re.search(r"/([^/]+)\.json$", source_path).group(1)
    return dimension_name, hierarchy_name


def create_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy, source_path: Optional[str] = None) -> Response:
    dimension_name, _ = _hierarchy_context_from_path(source_path)
    hierarchy_object = TM1py.Hierarchy(name=hierarchy.name, dimension_name=dimension_name)
    response = tm1_service.hierarchies.create(hierarchy_object)
    logger.info(f"Created Hierarchy: {hierarchy.name}.")

    return response


def update_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy, source_path: Optional[str] = None) -> Response:
    dimension_name, _ = _hierarchy_context_from_path(source_path)
    logger.info("Skipping direct Hierarchy update for '%s'; updates are handled by child changes.", hierarchy.name)
    return _build_noop_update_response(
        resource_url=format_url("/api/v1/Dimensions('{}')/Hierarchies('{}')", dimension_name, hierarchy.name),
        message=f"No-op Hierarchy update for '{hierarchy.name}'."
    )


def delete_hierarchy(tm1_service: TM1Service, hierarchy: Hierarchy, source_path: Optional[str] = None) -> Response:
    dimension_name, _ = _hierarchy_context_from_path(source_path)
    logger.info(f"Deleting Hierarchy: {hierarchy.name} of Dimension: {dimension_name}.")
    return tm1_service.hierarchies.delete(dimension_name=dimension_name, hierarchy_name=hierarchy.name)


def _build_noop_update_response(resource_url: str, message: str) -> Response:
    response = Response()
    response.status_code = 200
    response.url = resource_url
    response._content = message.encode("utf-8")
    response.encoding = "utf-8"
    return response
