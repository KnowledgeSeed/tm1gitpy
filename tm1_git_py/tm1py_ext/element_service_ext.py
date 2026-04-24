"""Element-related utilities using TM1py, including paginated element retrieval."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional, MutableSequence, Callable

from TM1py.Utils import format_url

from tm1_git_py.model.element import Element
from tm1_git_py.tm1py_ext._paginator import paginate_by_pages

if TYPE_CHECKING:
    from TM1py import TM1Service


@dataclass
class PaginatedElementsResult:
    """Result of a paginated get_elements call."""

    objects: List[Element]
    """Element objects in this page."""

    count: Optional[int]
    """Total number of elements (when $count=true). None if not requested."""

    skip: int
    """Number of elements skipped for this page."""

    top: int
    """Maximum number of elements requested for this page."""

    raw_rows: List[dict[str, Any]]
    """OData ``value`` entries for this page (same dict refs as parsed JSON)."""


def _get_elements_page(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: str,
    *,
    filter: Optional[str] = None,
    skip: int = 0,
    top: int = 1000,
    count: bool = False,
    **kwargs,
) -> PaginatedElementsResult:
    """Get one page of elements with OData pagination (skip, top, count).

    Uses the TM1 REST API OData parameters:
    - $filter: Optional OData filter expression
    - $skip: Number of elements to skip
    - $top: Maximum number of elements to return
    - $count: Include total count in response when True

    :param tm1_conn: TM1Service connection
    :param dimension_name: Name of the dimension
    :param hierarchy_name: Name of the hierarchy
    :param filter: Optional OData filter expression (without \"$filter=\" prefix)
    :param skip: Number of elements to skip (default 0)
    :param top: Maximum number of elements to return (default 1000)
    :param count: If True, request total count in response (default False)
    :param kwargs: Passed through to REST GET (e.g. timeout)
    :return: PaginatedElementsResult with elements, count (if requested), skip, top
    """
    base_url = format_url(
        "/Dimensions('{}')/Hierarchies('{}')/Elements?$select=Name,Type",
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

    response = tm1_conn.connection.GET(url, **kwargs)
    data = response.json()

    raw_rows = list(data.get("value", []))
    elements = [Element.from_dict(e) for e in raw_rows]
    total_count = data.get("@odata.count")
    if total_count is not None:
        total_count = int(total_count)

    return PaginatedElementsResult(
        objects=elements,
        count=total_count if count else None,
        skip=skip,
        top=top,
        raw_rows=raw_rows,
    )


def get_elements(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: str,
    *,
    filter: Optional[str] = None,
    page_size: int = 100000,
    collector: Optional[MutableSequence[Element]] = None,
    on_page_loaded: Optional[Callable[[int, Optional[int]], None]] = None,
    **kwargs,
) -> MutableSequence[Element]:
    """Fetch all elements page-by-page.

    :param tm1_conn: TM1Service connection
    :param dimension_name: Name of the dimension
    :param hierarchy_name: Name of the hierarchy
    :param filter: Optional OData filter expression (without \"$filter=\" prefix)
    :param page_size: Number of elements per page (default 1000)
    :param kwargs: Passed through to REST GET (e.g. timeout)
    :return: All fetched Element objects
    """
    collector_or_list: MutableSequence[Element] = [] if collector is None else collector

    def _fetcher(
        conn: "TM1Service",
        filter: Optional[str],
        skip: int,
        top: int,
        **kw,
    ) -> tuple[List[Element], Optional[int]]:
        kw.pop("dimension_name", None)
        kw.pop("hierarchy_name", None)
        result = _get_elements_page(
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
        entity_type="element",
        **kwargs,
    )
    return collector_or_list


def get_elements_count(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: str,
    *,
    filter: Optional[str] = None,
    **kwargs,
) -> int:
    """Return element count for a hierarchy, with optional filter."""
    url = format_url(
        "/Dimensions('{}')/Hierarchies('{}')/Elements/$count",
        dimension_name,
        hierarchy_name,
    )
    if filter:
        url = f"{url}?$filter={filter}"
    response = tm1_conn.connection.GET(url, **kwargs)
    return int(response.text.strip())