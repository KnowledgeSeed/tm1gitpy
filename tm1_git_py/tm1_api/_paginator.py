"""Generic paginator for TM1 OData-style name collections."""

import logging
import time
from typing import TYPE_CHECKING, Callable, List, Optional, TypeVar

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from TM1py import TM1Service

T = TypeVar("T")


def paginate_by_pages(
    tm1_conn: "TM1Service",
    on_page_fetched: Callable[
        ...,
        tuple[List[T], Optional[int]],
    ],
    *,
    filter: Optional[str] = None,
    page_size: int = 1000,
    **kwargs,
) -> Optional[int]:
    if page_size <= 0:
        raise ValueError("page_size must be greater than 0")

    skip = 0
    total_count: Optional[int] = None

    while True:
        kwargs_for_fetch = {k: v for k, v in kwargs.items() if k != "entity_type"}
        t0 = time.perf_counter()

        items, total_count_val = on_page_fetched(
            tm1_conn,
            filter=filter,
            skip=skip,
            top=page_size,
            **kwargs_for_fetch,
        )
        elapsed = time.perf_counter() - t0

        if total_count is None:
            total_count = total_count_val

        ctx_parts = []
        if "entity_type" in kwargs:
            ctx_parts.append(f"entity_type={kwargs['entity_type']}")
        if "dimension_name" in kwargs:
            ctx_parts.append(f"dimension_name={kwargs['dimension_name']}")
        if "hierarchy_name" in kwargs:
            ctx_parts.append(f"hierarchy_name={kwargs['hierarchy_name']}")
        ctx = " ".join(ctx_parts) or "unknown"
        logger.info(
            "Page fetched %s skip=%d top=%d total_count=%s elapsed=%.3fs",
            ctx,
            skip,
            page_size,
            total_count if total_count is not None else "?",
            elapsed,
        )

        if not items:
            break

        skip += len(items)

        if total_count is not None and skip >= total_count:
            break

        if len(items) < page_size:
            break

    return total_count
