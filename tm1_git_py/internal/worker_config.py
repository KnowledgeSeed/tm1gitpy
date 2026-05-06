import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WorkerCounts:
    cpu_workers: int
    io_workers: int


def resolve_worker_counts(max_workers: Optional[int], *, cpu_cores: Optional[int] = None) -> WorkerCounts:
    """Resolve CPU and IO worker counts from the CLI/API worker hint.

    When provided, ``max_workers`` represents the total CPU + IO worker budget.
    The total is split as close as possible to a 1:3 CPU/IO ratio.

    When the caller does not define ``max_workers``, CPU workers keep the
    historical default of ``cpu_cores // 2 + 1`` and IO workers are derived from
    that count using the same 1:3 ratio.
    """
    if max_workers is not None:
        total_workers = max(1, int(max_workers))
        cpu_workers = max(1, (total_workers + 2) // 4)
        io_workers = max(0, total_workers - cpu_workers)
        return WorkerCounts(cpu_workers=cpu_workers, io_workers=io_workers)

    cores = max(1, int(cpu_cores if cpu_cores is not None else (os.cpu_count() or 1)))
    cpu_workers = max(1, (cores // 2) + 1)
    return WorkerCounts(
        cpu_workers=cpu_workers,
        io_workers=cpu_workers * 3,
    )


__all__ = ["WorkerCounts", "resolve_worker_counts"]
