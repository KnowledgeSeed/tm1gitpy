import io
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

from tm1_git_py.model.tm1git_json import dump_as_tm1git

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

# Keys (at any object depth) that use ``"key" : value`` instead of ``"key":value``.
# Native views follow tm1git compact colons throughout (see fixture_model_tm1git).
NATIVE_VIEW_JSON_SPACED_COLON_KEYS: frozenset[str] = frozenset()


class NativeView:
    def __init__(self, name, columns, rows, titles, suppress_empty_columns, suppress_empty_rows, format_string):
        self.type = 'NativeView'
        self.name = name
        self.columns = [view_axis_selection_to_dict(item) for item in columns]
        self.rows = [view_axis_selection_to_dict(item) for item in rows]
        self.titles = [view_title_selection_to_dict(item) for item in titles]
        self.suppress_empty_columns = suppress_empty_columns
        self.suppress_empty_rows = suppress_empty_rows
        self.format_string = format_string

    def as_json(self):
        payload: Dict[str, Any] = {
            "@type": self.type,
            "Name": self.name,
            "Columns": self.columns,
            "Rows": self.rows,
            "Titles": self.titles,
            "SuppressEmptyColumns": self.suppress_empty_columns,
            "SuppressEmptyRows": self.suppress_empty_rows,
            "FormatString": self.format_string,
        }
        buf = io.StringIO()
        dump_as_tm1git(
            payload, buf, spaced_colon_keys=NATIVE_VIEW_JSON_SPACED_COLON_KEYS
        )
        return buf.getvalue()

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
        return hash(
            (
                self.name,
                json.dumps(self.columns, sort_keys=True),
                json.dumps(self.rows, sort_keys=True),
                json.dumps(self.titles, sort_keys=True),
                self.suppress_empty_columns,
                self.suppress_empty_rows,
                self.format_string,
            )
        )

    def __repr__(self):
        return f"{self.type}('{self.name}')"

    def to_dict(self):
        return {
            "name": self.name,
            "columns": self.columns,
            "rows": self.rows,
            "titles": self.titles,
            "suppress_empty_columns": self.suppress_empty_columns,
            "suppress_empty_rows": self.suppress_empty_rows,
            "format_string": self.format_string,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NativeView":
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
        format_string = (
            data.get("format_string") or data.get("FormatString") or "0.#########"
        )

        return cls(
            name=name,
            columns=columns,
            rows=rows,
            titles=titles,
            suppress_empty_columns=suppress_empty_columns,
            suppress_empty_rows=suppress_empty_rows,
            format_string=format_string,
        )

    @classmethod
    def from_tm1py(cls, view: Any) -> "NativeView":
        raw_view = getattr(view, "_tm1git_raw_view_dict", None)
        if isinstance(raw_view, dict):
            return cls(
                name=raw_view.get("Name") or view.name,
                columns=raw_view.get("Columns") or [],
                rows=raw_view.get("Rows") or [],
                titles=raw_view.get("Titles") or [],
                suppress_empty_columns=raw_view.get("SuppressEmptyColumns", view.suppress_empty_columns),
                suppress_empty_rows=raw_view.get("SuppressEmptyRows", view.suppress_empty_rows),
                format_string=raw_view.get("FormatString", view.format_string),
            )
        return cls(
            name=view.name,
            columns=view.columns,
            rows=view.rows,
            titles=view.titles,
            suppress_empty_columns=view.suppress_empty_columns,
            suppress_empty_rows=view.suppress_empty_rows,
            format_string=view.format_string,
        )

    @staticmethod
    def uri_for(cube_name: str, view_name: str) -> str:
        return f"Cubes('{cube_name}')/Views('{view_name}')"

    def uri(self, cube_name: str) -> Optional[str]:
        if not cube_name or not self.name:
            return None
        return self.uri_for(cube_name, self.name)


def _subset_name_from_reference(subset: Any) -> Optional[str]:
    if isinstance(subset, str):
        subset_ref = subset
    elif isinstance(subset, dict):
        expression = subset.get("Expression") or subset.get("expression")
        if expression not in (None, ""):
            return None
        subset_name = subset.get("Name") or subset.get("name")
        if subset_name:
            return subset_name
        subset_ref = subset.get("@id") or subset.get("@odata.id") or subset.get("Href") or subset.get("href")
    else:
        return None

    if not isinstance(subset_ref, str):
        return None

    match = re.search(r"Subsets\('((?:''|[^'])*)'\)$", subset_ref)
    if match:
        return match.group(1).replace("''", "'")

    match = re.search(r"[/\\]subsets[/\\]([^/\\]+)\.json$", subset_ref, re.IGNORECASE)
    if match:
        return match.group(1)

    return None


def _get_subset_name(axis) -> Optional[str]:
    if isinstance(axis, dict):
        subset = axis.get("Subset")
    else:
        subset = getattr(axis, "Subset", None) or getattr(axis, "subset", None)
    return _subset_name_from_reference(subset)


def view_axis_selection_to_dict(axis_selection) -> Dict[str, Any]:
    if isinstance(axis_selection, dict):
        body = dict(axis_selection)
    else:
        body = dict(axis_selection.body_as_dict)
    subset = body.get("Subset") or body.get("Subset@odata.bind")

    if isinstance(subset, dict):
        subset_dict = dict(subset)
        hierarchy_bind = subset_dict.pop("Hierarchy@odata.bind", None)
        expression = subset_dict.get("Expression", None)

        if hierarchy_bind:
            if expression:
                subset_dict["Hierarchy"] = {"@id": hierarchy_bind}
            else:
                subset_dict = {"@id": hierarchy_bind}
        elif isinstance(subset_dict.get("Hierarchy"), dict):
            hierarchy = subset_dict["Hierarchy"]
            dimension = hierarchy.get("Dimension")
            dimension_name = dimension.get("Name") if isinstance(dimension, dict) else None
            hierarchy_name = hierarchy.get("Name")
            if dimension_name and hierarchy_name:
                if expression:
                    subset_dict["Hierarchy"] = {
                        "@id": f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')"
                    }
                else:
                    subset_dict = {"@id": f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')"}

        body["Subset"] = subset_dict

    elif isinstance(subset, str):
        subset_bind = body.pop("Subset@odata.bind", None)
        body["Subset"] = {"@id": subset_bind}

    return body


def view_title_selection_to_dict(title_selection) -> Dict[str, Any]:
    if isinstance(title_selection, dict):
        body = dict(title_selection)
    else:
        body = dict(title_selection._construct_body())
    subset = body.get("Subset")

    if isinstance(subset, dict):
        subset_dict = dict(subset)
        hierarchy_bind = subset_dict.pop("Hierarchy@odata.bind", None)
        expression = subset_dict.get("Expression", None)

        if hierarchy_bind:
            if expression:
                subset_dict["Hierarchy"] = {"@id": hierarchy_bind}
            else:
                subset_dict = {"@id": hierarchy_bind}
        elif isinstance(subset_dict.get("Hierarchy"), dict):
            hierarchy = subset_dict["Hierarchy"]
            dimension = hierarchy.get("Dimension")
            dimension_name = dimension.get("Name") if isinstance(dimension, dict) else None
            hierarchy_name = hierarchy.get("Name")
            if dimension_name and hierarchy_name:
                if expression:
                    subset_dict["Hierarchy"] = {
                        "@id": f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')"
                    }
                else:
                    subset_dict = {"@id": f"Dimensions('{dimension_name}')/Hierarchies('{hierarchy_name}')"}

        body["Subset"] = subset_dict

    elif isinstance(subset, str):
        subset_bind = body.pop("Subset@odata.bind", None)
        body["Subset"] = {"@id": subset_bind}

    return body


def _native_view_context_from_uri(uri: str) -> Tuple[str, str]:
    match = re.search(r"^Cubes\('([^']+)'\)/Views\('([^']+)'\)$", uri or "")
    if not match:
        raise ValueError(f"Invalid native view uri format: '{uri}'")
    cube_name, view_name = match.groups()
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
                subset_odata = subset.get("@id")
                if isinstance(subset_odata, str):
                    axis["Subset@odata.bind"] = subset_odata
                    #subset.pop("@id", None)
                    axis.pop("Subset")
    return payload


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between TM1py and tm1_git_py for CRUD operations
# ------------------------------------------------------------------------------------------------------------

logger = logging.getLogger(__name__)


def create_native_view(tm1_service: TM1Service, native_view: NativeView, uri: Optional[str] = None) -> Response:
    cube_name, _ = _native_view_context_from_uri(uri)
    native_view_object = TM1py.NativeView.from_dict(
        view_as_dict=_to_tm1py_native_view_dict(native_view),
        cube_name=cube_name,
    )
    logger.info(f"Creating NativeView: {native_view.name} for Cube: {cube_name}.")
    return tm1_service.views.create(native_view_object)


def update_native_view(tm1_service: TM1Service, native_view: NativeView, uri: Optional[str] = None) -> Response:
    cube_name, _ = _native_view_context_from_uri(uri)
    native_view_object = TM1py.NativeView.from_dict(
        view_as_dict=_to_tm1py_native_view_dict(native_view),
        cube_name=cube_name,
    )
    logger.info(f"Updating NativeView: {native_view.name} for Cube: {cube_name}.")
    return tm1_service.views.update(native_view_object)


def delete_native_view(tm1_service: TM1Service, native_view: NativeView, uri: Optional[str] = None) -> Response:
    cube_name, _ = _native_view_context_from_uri(uri)
    logger.info(f"Deleting NativeView: {native_view.name} from Cube: {cube_name}.")
    return tm1_service.views.delete(view_name=native_view.name, cube_name=cube_name)


# ------------------------------------------------------------------------------------------------------------
# Utility: interface between tm1_git_py and TI processes for CRUD operations
# ------------------------------------------------------------------------------------------------------------

def _escape_ti(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).replace("'", "''")


def _native_view_axis_context(axis) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if hasattr(axis, "dimension_name"):
        dim_name = axis.dimension_name
        return dim_name, getattr(axis, "hierarchy_name", dim_name), getattr(axis, "subset_name", None)
    if isinstance(axis, dict):
        subset = axis.get("Subset") or {}
        if isinstance(subset, dict):
            subset_name = _subset_name_from_reference(subset)
            hierarchy = subset.get("Hierarchy") or {}
            hierarchy_id = ""
            if isinstance(hierarchy, dict):
                hierarchy_id = hierarchy.get("@id") or hierarchy.get("@odata.bind") or ""
            if not hierarchy_id:
                hierarchy_id = subset.get("@id") or subset.get("@odata.id") or subset.get("Href") or subset.get("href") or ""
            match = re.search(r"Dimensions\('([^']+)'\)/Hierarchies\('([^']+)'\)", hierarchy_id)
            if match:
                dim_name, hier_name = match.groups()
                return dim_name, hier_name, subset_name
    return None, None, None


def _native_view_axis_expression(axis) -> Optional[str]:
    if hasattr(axis, "expression"):
        return getattr(axis, "expression", None)
    if isinstance(axis, dict):
        subset = axis.get("Subset") or {}
        if isinstance(subset, dict):
            return subset.get("Expression") or subset.get("expression")
    return None


def _native_view_temp_subset_ti_lines(view_clean: str, dim_name: str, hier_name: str, expression: str) -> list[str]:
    subset_clean = view_clean

    lines = [
        f"    IF( HierarchySubsetExists('{dim_name}', '{hier_name}', '{subset_clean}') = 0 );",
        f"        HierarchySubsetCreate('{dim_name}', '{hier_name}', '{subset_clean}', 0);",
        f"        HierarchySubsetMDXSet('{dim_name}', '{hier_name}', '{subset_clean}', '{expression}');",
        f"        HierarchySubsetMDXSet('{dim_name}', '{hier_name}', '{subset_clean}', '');",
        "    ENDIF;"
    ]
    return lines


def build_native_view_create_ti(native_view: NativeView, uri: Optional[str] = None) -> str:
    """
    Generates TI code to create a Native View, assign dimensions to axes,
    and assign subsets.
    """
    cube_name, _ = _native_view_context_from_uri(uri)

    cube_clean = _escape_ti(cube_name)
    view_clean = _escape_ti(native_view.name)

    lines = []
    lines.append(f"# --- Create Native View: {view_clean} in Cube: {cube_clean} ---")

    # 1. Create the base View container (0 = Permanent view)
    lines.append(f"IF( ViewExists('{cube_clean}', '{view_clean}') = 0 );")
    lines.append(f"    ViewCreate('{cube_clean}', '{view_clean}', 0);")

    # 2. Assign Columns
    # TI Stack Positions are 1-based, so we use enumerate(..., start=1)
    if hasattr(native_view, 'columns') and native_view.columns:
        lines.append("    # -- Setup Columns --")
        for i, axis in enumerate(native_view.columns, start=1):
            dim_name, hier_name, subset_name = _native_view_axis_context(axis)
            expression = _native_view_axis_expression(axis)
            if not dim_name:
                continue
            dim_clean = _escape_ti(dim_name)
            hier_clean = _escape_ti(hier_name)
            expr_clean = _escape_ti(expression)

            if subset_name:
                sub_clean = _escape_ti(subset_name)
                #lines.append(f"    ViewColumnDimensionSet('{cube_clean}', '{view_clean}', '{dim_clean}', {i});")
                lines += [
                    #f"    HierarchySubsetMDXSet('{dim_clean}', '{hier_clean}', '{sub_clean}', '{expr_clean}');",
                    #f"    HierarchySubsetMDXSet('{dim_clean}', '{hier_clean}', '{sub_clean}', '');",
                    f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{sub_clean}');"
                ]
                #lines.append(f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{sub_clean}');")

            elif expression:
                temp_lines = _native_view_temp_subset_ti_lines(view_clean, dim_clean, hier_clean, expr_clean)
                lines.extend(temp_lines)
                lines.append(f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{view_clean}');")
            
    # 3. Assign Rows
    if hasattr(native_view, 'rows') and native_view.rows:
        lines.append("    # -- Setup Rows --")
        for i, axis in enumerate(native_view.rows, start=1):
            dim_name, hier_name, subset_name = _native_view_axis_context(axis)
            expression = _native_view_axis_expression(axis)
            if not dim_name:
                continue
            dim_clean = _escape_ti(dim_name)
            hier_clean = _escape_ti(hier_name)
            expr_clean = _escape_ti(expression)
            
            if subset_name:
                sub_clean = _escape_ti(subset_name)
                #lines.append(f"    ViewRowDimensionSet('{cube_clean}', '{view_clean}', '{dim_clean}', {i});")
                lines += [
                    #f"    HierarchySubsetMDXSet('{dim_clean}', '{hier_clean}', '{sub_clean}', '{expr_clean}');",
                    #f"    HierarchySubsetMDXSet('{dim_clean}', '{hier_clean}', '{sub_clean}', '');",
                    f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{sub_clean}');"
                ]
                #lines.append(f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{sub_clean}');")

            elif expression:
                temp_lines = _native_view_temp_subset_ti_lines(view_clean, dim_name, hier_name, expression)
                lines.extend(temp_lines)
                lines.append(f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{view_clean}');")

    # 4. Assign Titles (Context/Filters)
    if hasattr(native_view, 'titles') and native_view.titles:
        lines.append("    # -- Setup Titles --")
        for i, axis in enumerate(native_view.titles, start=1):
            dim_name, hier_name, subset_name = _native_view_axis_context(axis)
            expression = _native_view_axis_expression(axis)
            if not dim_name:
                continue
            dim_clean = _escape_ti(dim_name)
            hier_clean = _escape_ti(hier_name)
            expr_clean = _escape_ti(expression)
            
            if subset_name:
                sub_clean = _escape_ti(subset_name)
                #lines.append(f"    ViewTitleDimensionSet('{cube_clean}', '{view_clean}', '{dim_clean}');")
                #lines.append(f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{sub_clean}');")
                lines += [
                    #f"    HierarchySubsetMDXSet('{dim_clean}', '{hier_clean}', '{sub_clean}', '{expr_clean}');",
                    #f"    HierarchySubsetMDXSet('{dim_clean}', '{hier_clean}', '{sub_clean}', '');",
                    f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{sub_clean}');",
                ]
            elif expression:
                temp_lines = _native_view_temp_subset_ti_lines(view_clean, dim_name, hier_name, expression)
                lines.extend(temp_lines)
                lines.append(f"    ViewSubsetAssign('{cube_clean}', '{view_clean}', '{dim_clean}', '{view_clean}');")

    # 5. Optional Properties (Suppress Zeroes, formatting, etc.)
    # TI uses ViewExtractSkipZeroesSet (which affects rows and columns)
    lines.append("    # -- Suppress Zeros --")
    if hasattr(native_view, 'suppress_empty_rows'):
        flag = "1" if native_view.suppress_empty_rows else "0"
        lines.append(f"    ViewSuppressZeroesSet('{cube_clean}', '{view_clean}', {flag});")

    # TI uses ViewColumnSuppressZeroesSet (which affects only columns)
    if hasattr(native_view, 'suppress_empty_columns'):
        flag = "1" if native_view.suppress_empty_columns else "0"
        lines.append(f"    ViewColumnSuppressZeroesSet('{cube_clean}', '{view_clean}', {flag});")

    lines.append("ENDIF;")

    return "\r\n".join(lines)


def build_native_view_update_ti(native_view: NativeView, uri: Optional[str] = None) -> str:
    """
    Generates TI code to update a Native View.
    Strategy: Delete existing view -> Recreate with new definition.
    """
    cube_name, _ = _native_view_context_from_uri(uri)

    cube_clean = _escape_ti(cube_name)
    view_clean = _escape_ti(native_view.name)

    lines = [
        f"# --- Update (Recreate) Native View: {view_clean} for Cube: {cube_clean} ---",
        build_native_view_delete_ti(native_view, uri),
        build_native_view_create_ti(native_view, uri),
    ]

    return "\r\n".join(lines)


def build_native_view_delete_ti(native_view: NativeView, uri: Optional[str] = None) -> str:
    """
    Generates TI code to safely delete a Native View.
    """
    cube_name, _ = _native_view_context_from_uri(uri)

    cube_clean = _escape_ti(cube_name)
    view_clean = _escape_ti(native_view.name)

    lines = [
        f"# --- Delete Native View: {view_clean} from Cube: {cube_clean} ---",
        f"IF( ViewExists('{cube_clean}', '{view_clean}') = 1 );",
        f"    ViewDestroy('{cube_clean}', '{view_clean}');",
        "ENDIF;"
    ]

    return "\r\n".join(lines)
