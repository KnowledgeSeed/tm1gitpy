"""Edge-related utilities using TM1py, including paginated edge retrieval."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional, MutableSequence, Callable

from TM1py.Utils import format_url

from tm1_git_py.model.edge import Edge
from tm1_git_py.tm1py_ext._paginator import paginate_by_pages


if TYPE_CHECKING:
    from TM1py import TM1Service


@dataclass
class PaginatedEdgesResult:
    """Result of a paginated get_edges call."""

    objects: List[Edge]
    """Edges in this page."""

    count: Optional[int]
    """Total number of edges (when $count=true). None if not requested."""

    skip: int
    """Number of edges skipped for this page."""

    top: int
    """Maximum number of edges requested for this page."""

    raw_rows: List[dict[str, Any]]
    """OData ``value`` entries for this page (same dict refs as parsed JSON)."""


def _get_edges_page(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: str,
    *,
    filter: Optional[str] = None,
    skip: int = 0,
    top: int = 1000,
    count: bool = False,
    **kwargs,
) -> PaginatedEdgesResult:
    """Get one page of edges with OData pagination (skip, top, count).

    Uses the TM1 REST API OData parameters:
    - $filter: Optional OData filter expression (e.g. ParentName, ComponentName)
    - $skip: Number of edges to skip
    - $top: Maximum number of edges to return
    - $count: Include total count in response when True

    :param tm1_conn: TM1Service connection
    :param dimension_name: Name of the dimension
    :param hierarchy_name: Name of the hierarchy
    :param filter: Optional OData filter expression (without \"$filter=\" prefix)
    :param skip: Number of edges to skip (default 0)
    :param top: Maximum number of edges to return (default 1000)
    :param count: If True, request total count in response (default False)
    :param kwargs: Passed through to REST GET (e.g. timeout)
    :return: PaginatedEdgesResult with edges, count (if requested), skip, top
    """
    base_url = format_url(
        "/Dimensions('{}')/Hierarchies('{}')/Edges?$select=ParentName,ComponentName,Weight",
        dimension_name,
        hierarchy_name,
    )
    params: List[str] = []
    if filter:
        params.append(f"$filter={filter}")
    if skip > 0:
        params.append(f"$skip={skip}")
    if top > 0:
        params.append(f"$top={top}")
    if count:
        params.append("$count=true")

    url = base_url
    if params:
        url = f"{base_url}&{'&'.join(params)}"

    response = tm1_conn.connection.GET(url, **kwargs, async_requests_mode=True)
    data = response.json()

    total_count = data.get("@odata.count")
    if total_count is not None:
        total_count = int(total_count)

    raw_rows = list(data.get("value", []))
    edges = [Edge.from_dict(item) for item in raw_rows]

    return PaginatedEdgesResult(
        objects=edges,
        count=total_count if count else None,
        skip=skip,
        top=top,
        raw_rows=raw_rows,
    )


def get_edges(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: str,
    *,
    filter: Optional[str] = None,
    page_size: int = 100000,
    collector: Optional[MutableSequence[Edge]] = None,
    on_page_loaded: Optional[Callable[[int, Optional[int]], None]] = None,
    **kwargs,
) -> MutableSequence[Edge]:
    """Fetch all edges page-by-page.

    :param tm1_conn: TM1Service connection
    :param dimension_name: Name of the dimension
    :param hierarchy_name: Name of the hierarchy
    :param filter: Optional OData filter expression (without \"$filter=\" prefix)
    :param page_size: Number of edges per page (default 1000)
    :param kwargs: Passed through to REST GET (e.g. timeout)
    :return: All fetched edges as list of Edge objects
    """
    collector_or_list: MutableSequence[Edge] = [] if collector is None else collector

    def _fetcher(
        conn: "TM1Service",
        filter: Optional[str],
        skip: int,
        top: int,
        **kw,
    ) -> tuple[List[Edge], Optional[int]]:
        kw.pop("dimension_name", None)
        kw.pop("hierarchy_name", None)
        result = _get_edges_page(
            conn,
            dimension_name,
            hierarchy_name,
            filter=filter,
            skip=skip,
            top=top,
            count=(skip == 0),
            **kw,
        )
        if collector is not None and hasattr(collector, "extend_payloads"):
            collector.extend_payloads(result.raw_rows)
        else:
            collector_or_list.extend(result.objects)
        if on_page_loaded is not None:
            on_page_loaded(len(result.objects), result.count)
        return (result.objects, result.count)

    paginate_by_pages(
        tm1_conn,
        _fetcher,
        filter=filter,
        page_size=page_size,
        dimension_name=dimension_name,
        hierarchy_name=hierarchy_name,
        entity_type="edge",
        **kwargs,
    )
    return collector_or_list


def get_edges_count(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: str,
    *,
    filter: Optional[str] = None,
    **kwargs,
) -> int:
    """Return edge count for a hierarchy, with optional filter."""
    url = format_url(
        "/Dimensions('{}')/Hierarchies('{}')/Edges/$count",
        dimension_name,
        hierarchy_name,
    )
    if filter:
        url = f"{url}?$filter={filter}"
    response = tm1_conn.connection.GET(url, **kwargs)
    return int(response.text.strip())