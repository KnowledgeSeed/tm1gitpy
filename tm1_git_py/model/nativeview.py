import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import TM1py
from TM1py.Services import TM1Service
from requests import Response

# {
# 	"@type":"NativeView",
# 	"Name":"TestCube3WithView_view2",
# 	"Columns":
# 	[
# 		{
# 			"Subset":
# 			{
# 				"Hierarchy":
# 				{
# 					"@id":"Dimensions('TestDim2')/Hierarchies('TestDim2')"
# 				},
# 				"Expression":"{[TestDim2].[TestDim2].Members}"
# 			}
# 		}
# 	],
# 	"Rows":
# 	[
# 		{
# 			"Subset":
# 			{
# 				"Hierarchy":
# 				{
# 					"@id":"Dimensions('TestDim1')/Hierarchies('TestDim1')"
# 				},
# 				"Expression":"{[TestDim1].[TestDim1].Members}"
# 			}
# 		}
# 	],
# 	"Titles":[],
# 	"SuppressEmptyColumns":true,
# 	"SuppressEmptyRows":true,
# 	"FormatString":"0.#########"
# }


class NativeView:
    def __init__(self, name, columns, rows, titles, suppress_empty_columns, suppress_empty_rows, format_string, source_path: str):
        self.type = 'NativeView'
        self.name = name
        self.columns = [view_axis_selection_to_dict(item) for item in columns]
        self.rows = [view_axis_selection_to_dict(item) for item in rows]
        self.titles = titles
        self.suppress_empty_columns = suppress_empty_columns
        self.suppress_empty_rows = suppress_empty_rows
        self.format_string = format_string
        self.source_path = source_path

    def as_json(self):
        return json.dumps({
            "@type": self.type,
            "Name": self.name,
            "Columns": self.columns,
            "Rows": self.rows,
            "Titles": self.titles,
            "SuppressEmptyColumns": self.suppress_empty_columns,
            "SuppressEmptyRows": self.suppress_empty_rows,
            "FormatString": self.format_string,
        }, indent='\t')
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, NativeView):
            return NotImplemented
        return (
            self.name == other.name
            and self.columns == other.columns
            and self.rows == other.rows
            and self.titles == other.titles
            and self.suppress_empty_columns == other.suppress_empty_columns
            and self.suppress_empty_rows == other.suppress_empty_rows
            and self.format_string == other.format_string
        )

    def __hash__(self) -> int:
        return hash((
            self.name,
            json.dumps(self.columns, sort_keys=True),
            json.dumps(self.rows, sort_keys=True),
            json.dumps(self.titles, sort_keys=True),
            self.suppress_empty_columns,
            self.suppress_empty_rows,
            self.format_string,
        ))

    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            'name': self.name,
            'columns': self.columns,
            'rows': self.rows,
            'titles': self.titles,
            'suppress_empty_columns': self.suppress_empty_columns,
            'suppress_empty_rows': self.suppress_empty_rows,
            'format_string': self.format_string,
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        source_path: Optional[str] = None,
        cube_name: Optional[str] = None,
    ) -> "NativeView":
        name = data.get("name") or data.get("Name")
        columns = data.get("columns") or data.get("Columns") or []
        rows = data.get("rows") or data.get("Rows") or []
        titles = data.get("titles") or data.get("Titles") or []
        suppress_empty_columns = data.get("suppress_empty_columns")
        if suppress_empty_columns is None:
            suppress_empty_columns = data.get("SuppressEmptyColumns", False)
        suppress_empty_rows = data.get("suppress_empty_rows")
        if suppress_empty_rows is None:
            suppress_empty_rows = data.get("SuppressEmptyRows", False)
        format_string = data.get("format_string") or data.get("FormatString") or "0.#########"

        resolved_path = source_path
        if resolved_path is None and cube_name and name:
            resolved_path = f"cubes/{cube_name}.views/{name}.json"
        if resolved_path is None:
            raise ValueError("NativeView.from_dict requires a source_path or cube context.")

        return cls(
            name=name,
            columns=columns,
            rows=rows,
            titles=titles,
            suppress_empty_columns=suppress_empty_columns,
            suppress_empty_rows=suppress_empty_rows,
            format_string=format_string,
            source_path=resolved_path,
        )

def view_axis_selection_to_dict(axis_selection) -> Dict[str, Any]:
    if isinstance(axis_selection, dict):
        body = dict(axis_selection)
    else:
        body = dict(axis_selection.body_as_dict)
    subset = body.get("Subset")

    if isinstance(subset, dict):
        subset_dict = dict(subset)
        hierarchy_bind = subset_dict.pop("Hierarchy@odata.bind", None)

        if hierarchy_bind:
            subset_dict["Hierarchy"] = {"@id": hierarchy_bind}

        body["Subset"] = subset_dict

    return body


def _native_view_context_from_path(source_path: str) -> Tuple[str, str]:
    cube_name = re.search(r'/([^/]+)\.views', source_path).group(1)
    view_name = re.search(r"/([^/]+)\.json$", source_path).group(1)
    return cube_name, view_name


def _to_tm1py_native_view_dict(native_view: NativeView) -> Dict[str, Any]:
    payload = {
        "Name": native_view.name,
        "Columns": json.loads(json.dumps(native_view.columns)),
        "Rows": json.loads(json.dumps(native_view.rows)),
        "Titles": json.loads(json.dumps(native_view.titles)),
        "SuppressEmptyColumns": native_view.suppress_empty_columns,
        "SuppressEmptyRows": native_view.suppress_empty_rows,
        "FormatString": native_view.format_string,
    }

    for axis_name in ("Columns", "Rows", "Titles"):
        for axis in payload.get(axis_name, []) or []:
            subset = axis.get("Subset") if isinstance(axis, dict) else None
            if isinstance(subset, dict):
                hierarchy = subset.get("Hierarchy")
                if isinstance(hierarchy, dict) and hierarchy.get("@id"):
                    subset["Hierarchy@odata.bind"] = hierarchy["@id"]
                    subset.pop("Hierarchy", None)
    return payload


logger = logging.getLogger(__name__)


def create_native_view(tm1_service: TM1Service, native_view: NativeView) -> Response:
    cube_name, _ = _native_view_context_from_path(native_view.source_path)
    native_view_object = TM1py.NativeView.from_dict(
        view_as_dict=_to_tm1py_native_view_dict(native_view),
        cube_name=cube_name,
    )
    logger.info(f"Creating NativeView: {native_view.name} for Cube: {cube_name}.")
    return tm1_service.views.create(native_view_object)


def update_native_view(tm1_service: TM1Service, native_view: Dict[str, Any]) -> Response:
    native_view_new = native_view.get('new')
    cube_name, _ = _native_view_context_from_path(native_view_new.source_path)
    native_view_object = TM1py.NativeView.from_dict(
        view_as_dict=_to_tm1py_native_view_dict(native_view_new),
        cube_name=cube_name,
    )
    logger.info(f"Updating NativeView: {native_view_new.name} for Cube: {cube_name}.")
    return tm1_service.views.update(native_view_object)


def delete_native_view(tm1_service: TM1Service, native_view: NativeView) -> Response:
    cube_name, _ = _native_view_context_from_path(native_view.source_path)
    logger.info(f"Deleting NativeView: {native_view.name} from Cube: {cube_name}.")
    return tm1_service.views.delete(view_name=native_view.name, cube_name=cube_name)
