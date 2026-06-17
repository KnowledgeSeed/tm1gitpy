import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WorkerCounts:
    max_workers: int
    cpu_workers: int
    io_workers: int


def resolve_worker_counts(max_workers: Optional[int], *, io_ratio: int = 3) -> WorkerCounts:
    """Resolve CPU and IO worker counts from the CLI/API worker hint.

    ``io_ratio`` is the IO-worker count per CPU worker (IO:CPU = io_ratio:1).

    When provided, ``max_workers`` represents the total CPU + IO worker budget.
    The total is split as close as possible to that ratio.

    When the caller does not define ``max_workers``, CPU workers use
    ``(os.cpu_count() // 2) + 1`` (at least one) and IO workers are
    ``cpu_workers * io_ratio``. ``max_workers`` on the result is their sum.
    """
    r = max(1, int(io_ratio))
    if max_workers is not None:
        total_workers = max(1, int(max_workers))
        cpu_workers = max(1, (total_workers + r - 1) // (r + 1))
        io_workers = max(0, total_workers - cpu_workers)
        return WorkerCounts(max_workers=total_workers, cpu_workers=cpu_workers, io_workers=io_workers)

    cores = max(1, os.cpu_count() or 1)
    cpu_workers = max(1, (cores // 2) + 1)
    io_workers = cpu_workers * r
    total_workers = cpu_workers + io_workers
    return WorkerCounts(
        max_workers=total_workers,
        cpu_workers=cpu_workers,
        io_workers=io_workers,
    )


__all__ = ["WorkerCounts", "resolve_worker_counts"]
