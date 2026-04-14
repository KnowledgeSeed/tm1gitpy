"""Process-related utilities extending TM1py ProcessService behavior."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from tm1_git_py.tm1py_ext._paginator import paginate_by_pages

if TYPE_CHECKING:
    from TM1py import TM1Service


@dataclass
class ProcessNamesResult:
    """Result of an extended process get_all_names call."""

    names: List[str]
    """Process names in this page."""

    count: Optional[int]
    """Total number of processes (when $count=true). None if not requested."""

    skip: int
    """Number of processes skipped for this page."""

    top: Optional[int]
    """Maximum number of processes requested for this page."""


def _get_all_names_page(
    tm1_conn: "TM1Service",
    *,
    filter: Optional[str] = None,
    skip: int = 0,
    top: Optional[int] = None,
    count: bool = False,
    **kwargs,
) -> ProcessNamesResult:
    """Get one page of process names with optional filter and pagination controls."""
    base_url = "/Processes?$select=Name"

    filters: List[str] = [filter] if filter else []

    params: List[str] = []
    if filters:
        params.append(f"$filter={' and '.join(filters)}")
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

    return ProcessNamesResult(
        names=names,
        count=total_count if count else None,
        skip=skip,
        top=top,
    )


def get_all_names(
    tm1_conn: "TM1Service",
    *,
    filter: Optional[str] = None,
    page_size: int = 1000,
    **kwargs,
) -> List[str]:
    """Fetch all process names page-by-page.

    Requests pages with $skip / $top until all rows are fetched. The first page
    requests $count=true to determine total row count and terminate reliably when
    count is reached.
    """

    all_names: List[str] = []

    def _fetcher(
        conn: "TM1Service",
        filter: Optional[str],
        skip: int,
        top: int,
        **kw,
    ) -> tuple[List[str], Optional[int]]:
        result = _get_all_names_page(
            conn,
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
        entity_type="process",
        **kwargs,
    )
    return all_names
