"""View-related utilities extending TM1py ViewService behavior."""

import logging
from copy import deepcopy
from typing import TYPE_CHECKING, List, Optional, Tuple

from TM1py.Objects.MDXView import MDXView
from TM1py.Objects.NativeView import NativeView
from TM1py.Utils import format_url

if TYPE_CHECKING:
    from TM1py import TM1Service
    from TM1py.Objects import View


logger = logging.getLogger(__name__)


def _normalize_native_view_title_selection(view_as_dict: dict) -> dict:
    """Patch a copy of the TM1 payload just enough for TM1py to parse it."""
    for axis_selection in view_as_dict.get("Titles", []) or []:
        if not isinstance(axis_selection, dict):
            continue
        if axis_selection.get("Selected") is not None:
            continue

        subset = axis_selection.get("Subset")
        elements = subset.get("Elements") if isinstance(subset, dict) else None
        fallback_name = ""
        if isinstance(elements, list) and elements:
            first_element = elements[0]
            if isinstance(first_element, dict):
                fallback_name = first_element.get("Name") or ""

        axis_selection["Selected"] = {"Name": fallback_name}
        logger.debug(
            "Native view '%s' has a title selection with Selected=null; using temporary TM1py parse fallback '%s'",
            view_as_dict.get("Name"),
            fallback_name,
        )
    return view_as_dict


def get_all(
    tm1_conn: "TM1Service",
    cube_name: str,
    *,
    filter: Optional[str] = None,
    include_elements: bool = True,
    **kwargs,
) -> Tuple[List["View"], List["View"]]:
    """Get all public and private views from a cube with optional OData filter."""
    element_filter = ";$top=0" if not include_elements else ""

    private_views: List["View"] = []
    public_views: List["View"] = []
    for view_type in ("PrivateViews", "Views"):
        base_url = format_url(
            "/Cubes('{}')/{}?$expand="
            "tm1.NativeView/Rows/Subset($expand=Hierarchy($select=Name;"
            "$expand=Dimension($select=Name)),Elements($select=Name{});"
            "$select=Expression,UniqueName,Name, Alias),  "
            "tm1.NativeView/Columns/Subset($expand=Hierarchy($select=Name;"
            "$expand=Dimension($select=Name)),Elements($select=Name{});"
            "$select=Expression,UniqueName,Name,Alias), "
            "tm1.NativeView/Titles/Subset($expand=Hierarchy($select=Name;"
            "$expand=Dimension($select=Name)),Elements($select=Name{});"
            "$select=Expression,UniqueName,Name,Alias), "
            "tm1.NativeView/Titles/Selected($select=Name)",
            cube_name,
            view_type,
            element_filter,
            element_filter,
            element_filter,
        )
        url = f"{base_url}&$filter={filter}" if filter else base_url

        response = tm1_conn.connection.GET(url, **kwargs)
        response_as_list = response.json().get("value", [])
        for view_as_dict in response_as_list:
            if view_as_dict.get("@odata.type") == "#ibm.tm1.api.v1.MDXView":
                view = MDXView.from_dict(view_as_dict, cube_name)
            else:
              view = NativeView.from_dict(
                    _normalize_native_view_title_selection(deepcopy(view_as_dict)),
                    cube_name,
                )
            if view_type == "PrivateViews":
                private_views.append(view)
            else:
                public_views.append(view)

    return private_views, public_views
