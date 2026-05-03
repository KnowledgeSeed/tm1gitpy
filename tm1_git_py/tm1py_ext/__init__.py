"""Fetchers for TM1 data using TM1py."""

from tm1_git_py.tm1py_ext._paginator import paginate_by_pages
from tm1_git_py.tm1py_ext.cube_service_ext import (
    CubeNamesResult,
    get_all_names as get_cube_names,
)
from tm1_git_py.tm1py_ext.dimension_service_ext import (
    DimensionNamesResult,
    get_names,
    get_names as get_dimension_names,
)
from tm1_git_py.tm1py_ext.edge_service_ext import (
    _get_edges_page,
    PaginatedEdgesResult,
    get_edges,
    get_edges_count,
)
from tm1_git_py.tm1py_ext.element_service_ext import (
    _get_elements_page,
    PaginatedElementsResult,
    get_elements,
    get_elements_count,
)
from tm1_git_py.tm1py_ext.hierarchy_service_ext import (
    HierarchyNamesResult,
    get_all_names as get_hierarchy_names,
)
from tm1_git_py.tm1py_ext.process_service_ext import (
    ProcessNamesResult,
    get_all_names as get_process_names,
)
from tm1_git_py.tm1py_ext.subset_service_ext import (
    _get_subsets_page,
    PaginatedSubsetsResult,
    get_subsets,
    get_subsets_count,
)
from tm1_git_py.tm1py_ext.view_service_ext import (
    get_all as get_views,
)

__all__ = [
    "_get_edges_page",
    "_get_elements_page",
    "_get_subsets_page",
    "CubeNamesResult",
    "DimensionNamesResult",
    "HierarchyNamesResult",
    "PaginatedEdgesResult",
    "PaginatedElementsResult",
    "PaginatedSubsetsResult",
    "ProcessNamesResult",
    "get_cube_names",
    "get_dimension_names",
    "get_edges",
    "get_edges_count",
    "get_elements",
    "get_elements_count",
    "get_hierarchy_names",
    "get_names",
    "get_process_names",
    "get_subsets",
    "get_subsets_count",
    "get_views",
    "paginate_by_pages",
]
