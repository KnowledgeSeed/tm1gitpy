"""Hierarchy-related utilities extending TM1py HierarchyService behavior."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from TM1py.Utils import format_url

from tm1_git_py.tm1py_ext._paginator import paginate_by_pages

if TYPE_CHECKING:
    from TM1py import TM1Service


@dataclass
class HierarchyNamesResult:
    """Result of an extended hierarchy get_all_names call."""

    names: List[str]
    """Hierarchy names in this page."""

    count: Optional[int]
    """Total number of hierarchies (when $count=true). None if not requested."""

    skip: int
    """Number of hierarchies skipped for this page."""

    top: Optional[int]
    """Maximum number of hierarchies requested for this page."""


def _get_all_names_page(
    tm1_conn: "TM1Service",
    dimension_name: str,
    *,
    filter: Optional[str] = None,
    skip: int = 0,
    top: Optional[int] = None,
    count: bool = False,
    **kwargs,
) -> HierarchyNamesResult:
    """Get one page of hierarchy names with optional filter and pagination controls."""
    base_url = format_url("/Dimensions('{}')/Hierarchies?$select=Name", dimension_name)

    params: List[str] = []
    if filter:
        params.append(f"$filter={filter}")
    if skip > 0:
        params.append(f"$skip={skip}")
    if top is not None and top > 0:
        params.append(f"$top={top}")
    if count:
        params.append("$count=true")

    url = base_url
    if params:
        url = f"{base_url}&{'&'.join(params)}"

    response = tm1_conn.connection.GET(url, **kwargs)
    data = response.json()

    names = [entry["Name"] for entry in data.get("value", [])]
    total_count = data.get("@odata.count")
    if total_count is not None:
        total_count = int(total_count)

    return HierarchyNamesResult(
        names=names,
        count=total_count if count else None,
        skip=skip,
        top=top,
    )


def get_all_names(
    tm1_conn: "TM1Service",
    dimension_name: str,
    *,
    filter: Optional[str] = None,
    page_size: int = 1000,
    **kwargs,
) -> List[str]:
    """Fetch all hierarchy names page-by-page.

    Requests pages with $skip / $top until all rows are fetched. The first page
    requests $count=true to determine total row count and terminate reliably when
    count is reached.

    :param tm1_conn: TM1Service connection
    :param dimension_name: Name of the dimension
    :param filter: Optional OData filter expression (without \"$filter=\" prefix)
    :param page_size: Number of hierarchies per page (default 1000)
    :param kwargs: Passed through to REST GET (e.g. timeout)
    :return: All fetched hierarchy names
    """

    all_names: List[str] = []

    def _fetcher(
        conn: "TM1Service",
        filter: Optional[str],
        skip: int,
        top: int,
        **kw,
    ) -> tuple[List[str], Optional[int]]:
        kw.pop("dimension_name", None)  # use closure value
        result = _get_all_names_page(
            conn,
            dimension_name,
            filter=filter,
            skip=skip,
            top=top,
            count=(skip == 0),
            **kw,
        )
        all_names.extend(result.names)
        return (result.names, result.count)

    paginate_by_pages(
        tm1_conn,
        _fetcher,
        filter=filter,
        page_size=page_size,
        dimension_name=dimension_name,
        entity_type="hierarchy",
        **kwargs,
    )
    return all_names
