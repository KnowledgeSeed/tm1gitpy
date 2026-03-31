"""Fetchers for TM1 data using TM1py."""

from tm1_git_py.tm1py_ext._paginator import paginate_by_pages
from tm1_git_py.tm1py_ext.element_service_ext import (
    PaginatedElementsResult,
    get_elements,
)
from tm1_git_py.tm1py_ext.edge_service_ext import (
    PaginatedEdgesResult,
    get_edges
)
from tm1_git_py.tm1py_ext.subset_service_ext import (
    PaginatedSubsetsResult,
    get_subsets,
)
from tm1_git_py.tm1py_ext.dimension_service_ext import (
    DimensionNamesResult,
    get_names,
)
from tm1_git_py.tm1py_ext.hierarchy_service_ext import (
    HierarchyNamesResult,
    get_all_names as get_hierarchy_names,
)
from tm1_git_py.tm1py_ext.process_service_ext import (
    ProcessNamesResult,
    get_all_names as get_process_names,
)
from tm1_git_py.tm1py_ext.cube_service_ext import (
    CubeNamesResult,
    get_all_names as get_cube_names,
)
from tm1_git_py.tm1py_ext.view_service_ext import (
    get_all as get_views,
)

__all__ = [
    "paginate_by_pages",
    "PaginatedElementsResult",
    "get_elements",
    "PaginatedEdgesResult",
    "get_edges",
    "PaginatedSubsetsResult",
    "get_subsets",
    "DimensionNamesResult",
    "get_names",
    "HierarchyNamesResult",
    "get_hierarchy_names",
    "ProcessNamesResult",
    "get_process_names",
    "CubeNamesResult",
    "get_cube_names",
    "get_views",
]
