from typing import List, Any, Optional

from TM1py import TM1Service

from tm1_git_py.model.hierarchy import _normalize_hierarchy_sort_type, _normalize_hierarchy_sort_sense

_DIMENSION_PROPERTY_SORT_FIELDS = (
    "SORTELEMENTSTYPE",
    "SORTELEMENTSSENSE",
    "SORTCOMPONENTSTYPE",
    "SORTCOMPONENTSSENSE",
)
_DIMENSION_PROPERTY_TO_HIERARCHY_SORT_FIELD = {
    "SORTELEMENTSTYPE": "ElementsSortType",
    "SORTELEMENTSSENSE": "ElementsSortSense",
    "SORTCOMPONENTSTYPE": "ComponentsSortType",
    "SORTCOMPONENTSSENSE": "ComponentsSortSense",
}


def _escape_mdx_member_name(name: str) -> str:
    return str(name).replace("]", "]]")


def _dimension_properties_member_name(
    dimension_name: str,
    hierarchy_name: str,
) -> str:
    if str(hierarchy_name).casefold() == str(dimension_name).casefold():
        return dimension_name
    return f"{dimension_name}:{hierarchy_name}"


def _hierarchy_sort_metadata_mdx(
    dimension_name: str,
    hierarchy_name: str,
) -> str:
    properties = ",".join(
        f"[}}DimensionProperties].[{property_name}]"
        for property_name in _DIMENSION_PROPERTY_SORT_FIELDS
    )
    member_name = _escape_mdx_member_name(
        _dimension_properties_member_name(
            dimension_name,
            hierarchy_name,
        )
    )
    return (
        "SELECT \n"
        f"   {{{properties}}} * {{[}}Dimensions].[}}Dimensions].[{member_name}]}} \n"
        "  ON 0 \n"
        "FROM [}DimensionProperties] \n"
    )


def _sort_property_from_mdx_cell_key(key: Any) -> Optional[str]:
    normalized_key = str(key).upper()
    for property_name in _DIMENSION_PROPERTY_SORT_FIELDS:
        if property_name in normalized_key:
            return property_name
    return None


def _normalize_hierarchy_sort_metadata_value(property_name: str, value: Any) -> Optional[str]:
    if value is None:
        return None
    if str(value).strip() == "":
        return None
    if property_name in ("SORTELEMENTSTYPE", "SORTCOMPONENTSTYPE"):
        return _normalize_hierarchy_sort_type(str(value))
    return _normalize_hierarchy_sort_sense(str(value))


def _parse_hierarchy_sort_metadata_response(response: Any) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key, value in dict(response or {}).items():
        property_name = _sort_property_from_mdx_cell_key(key)
        if property_name is None:
            continue
        normalized_value = _normalize_hierarchy_sort_metadata_value(property_name, value)
        if normalized_value is None:
            continue
        metadata[_DIMENSION_PROPERTY_TO_HIERARCHY_SORT_FIELD[property_name]] = normalized_value
    return metadata


def get_hierarchy_sort_metadata(
    tm1_conn: TM1Service,
    dimension_name: str,
    hierarchy_names: List[str],
) -> dict[tuple[str, str], dict[str, str]]:
    hierarchy_names = list(hierarchy_names)
    result: dict[tuple[str, str], dict[str, str]] = {}
    for hierarchy_name in hierarchy_names:
        mdx = _hierarchy_sort_metadata_mdx(
            dimension_name,
            hierarchy_name,
        )
        response = tm1_conn.cells.execute_mdx_elements_value_dict(mdx)
        metadata = _parse_hierarchy_sort_metadata_response(response)
        if metadata:
            result[(dimension_name, hierarchy_name)] = metadata
    return result
