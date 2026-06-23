"""Subset-related utilities using TM1py, including paginated subset retrieval."""

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, List, MutableSequence, Optional

from TM1py.Utils import format_url

from tm1_git_py.model.subset import Subset
from tm1_git_py.tm1_api._paginator import paginate_by_pages


if TYPE_CHECKING:
    from TM1py import TM1Service


@dataclass
class PaginatedSubsetsResult:
    """Result of a paginated get_subsets call."""

    objects: List[Subset]
    """Subsets in this page."""

    count: Optional[int]
    """Total number of subsets (when $count=true). None if not requested."""

    skip: int
    """Number of subsets skipped for this page."""

    top: int
    """Maximum number of subsets requested for this page."""

    raw_rows: List[dict[str, Any]]
    """OData ``value`` entries for this page (same dict refs as parsed JSON)."""


def _get_subsets_page(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: Optional[str],
    *,
    filter: Optional[str] = None,
    skip: int = 0,
    top: int = 1000,
    count: bool = False,
    private: bool = False,
    **kwargs,
) -> PaginatedSubsetsResult:
    """Get one page of subsets with OData pagination (skip, top, count).

    Uses the TM1 REST API OData parameters:
    - $filter: Optional OData filter expression
    - $skip: Number of subsets to skip
    - $top: Maximum number of subsets to return
    - $count: Include total count in response when True

    :param tm1_conn: TM1Service connection
    :param dimension_name: Name of the dimension
    :param hierarchy_name: Name of the hierarchy (defaults to dimension_name if None)
    :param filter: Optional OData filter expression (without \"$filter=\" prefix)
    :param skip: Number of subsets to skip (default 0)
    :param top: Maximum number of subsets to return (default 1000)
    :param count: If True, request total count in response (default False)
    :param private: If True, fetch private subsets; otherwise public subsets (default False)
    :param kwargs: Passed through to REST GET (e.g. timeout)
    :return: PaginatedSubsetsResult with subsets, count (if requested), skip, top
    """
    hierarchy_name = hierarchy_name if hierarchy_name else dimension_name
    subsets_resource = "PrivateSubsets" if private else "Subsets"

    base_url = format_url(
        "/Dimensions('{}')/Hierarchies('{}')/{}?$select=Name,Expression&$expand=Elements/$ref",
        dimension_name,
        hierarchy_name,
        subsets_resource,
    )
    # Append pagination after the subset fields and element-reference expansion.
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

    raw_rows = list(data.get("value", []))
    subsets = [Subset.from_dict(item) for item in raw_rows]
    total_count = data.get("@odata.count")
    if total_count is not None:
        total_count = int(total_count)

    return PaginatedSubsetsResult(
        objects=subsets,
        count=total_count if count else None,
        skip=skip,
        top=top,
        raw_rows=raw_rows,
    )


def get_subsets(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: Optional[str] = None,
    *,
    filter: Optional[str] = None,
    page_size: int = 100000,
    private: bool = False,
    collector: Optional[MutableSequence[Subset]] = None,
    on_page_loaded: Optional[Callable[[int, Optional[int]], None]] = None,
    **kwargs,
) -> MutableSequence[Subset]:
    """Fetch all subsets page-by-page.

    :param tm1_conn: TM1Service connection
    :param dimension_name: Name of the dimension
    :param hierarchy_name: Name of the hierarchy (defaults to dimension_name if None)
    :param filter: Optional OData filter expression (without \"$filter=\" prefix)
    :param page_size: Number of subsets per page (default 1000)
    :param private: If True, fetch private subsets; otherwise public subsets (default False)
    :param kwargs: Passed through to REST GET (e.g. timeout)
    :return: All fetched Subset objects
    """
    hierarchy_name = hierarchy_name if hierarchy_name else dimension_name
    collector_or_list: MutableSequence[Subset] = [] if collector is None else collector

    def _fetcher(
        conn: "TM1Service",
        filter: Optional[str],
        skip: int,
        top: int,
        **kw,
    ) -> tuple[List[Subset], Optional[int]]:
        kw.pop("dimension_name", None)
        kw.pop("hierarchy_name", None)
        kw.pop("private", None)
        result = _get_subsets_page(
            conn,
            dimension_name,
            hierarchy_name,
            filter=filter,
            skip=skip,
            top=top,
            count=(skip == 0),
            private=private,
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
        entity_type="subset",
        private=private,
        **kwargs,
    )
    return collector_or_list


def get_subsets_count(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: Optional[str] = None,
    *,
    filter: Optional[str] = None,
    private: bool = False,
    **kwargs,
) -> int:
    """Return subset count for a hierarchy, with optional filter."""
    hierarchy_name = hierarchy_name if hierarchy_name else dimension_name
    subsets_resource = "PrivateSubsets" if private else "Subsets"
    url = format_url(
        "/Dimensions('{}')/Hierarchies('{}')/{}/$count",
        dimension_name,
        hierarchy_name,
        subsets_resource,
    )
    if filter:
        url = f"{url}?$filter={filter}"
    response = tm1_conn.connection.GET(url, **kwargs, async_requests_mode=True)
    return int(response.text.strip())


def get_subsets_identity_etag(
    tm1_conn: "TM1Service",
    dimension_name: str,
    hierarchy_name: Optional[str] = None,
    *,
    filter: Optional[str] = None,
    private: bool = False,
    page_size: int = 100000,
    **kwargs,
) -> str:
    """Return a stable ETag-like digest for subset identities in a hierarchy."""
    hierarchy_name = hierarchy_name if hierarchy_name else dimension_name
    subsets_resource = "PrivateSubsets" if private else "Subsets"
    identities: list[tuple[str, str]] = []
    skip = 0
    total_count: Optional[int] = None

    while total_count is None or skip < total_count:
        base_url = format_url(
            "/Dimensions('{}')/Hierarchies('{}')/{}?$select=Name",
            dimension_name,
            hierarchy_name,
            subsets_resource,
        )
        params: List[str] = []
        if filter:
            params.append(f"$filter={filter}")
        if skip > 0:
            params.append(f"$skip={skip}")
        if page_size > 0:
            params.append(f"$top={page_size}")
        if skip == 0:
            params.append("$count=true")

        url = base_url
        if params:
            url = f"{base_url}&{'&'.join(params)}"

        request_kwargs = dict(kwargs)
        request_headers = dict(request_kwargs.get("headers") or {})
        request_headers["Accept"] = "application/json;odata.metadata=minimal"
        request_kwargs["headers"] = request_headers

        response = tm1_conn.connection.GET(url, **request_kwargs, async_requests_mode=True)
        data = response.json()
        rows = list(data.get("value", []))
        if total_count is None:
            count_value = data.get("@odata.count")
            total_count = int(count_value) if count_value is not None else len(rows)

        for row in rows:
            name = row.get("Name")
            if name is not None:
                identities.append((str(name), str(row.get("@odata.etag") or "")))

        if not rows:
            break
        skip += len(rows)

    payload = json.dumps(sorted(identities), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
