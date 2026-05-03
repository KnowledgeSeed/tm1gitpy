import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WorkerCounts:
    cpu_workers: int
    io_workers: int


def resolve_worker_counts(max_workers: Optional[int], *, cpu_cores: Optional[int] = None) -> WorkerCounts:
    """Resolve CPU and IO worker counts from the CLI/API worker hint.

    ``max_workers`` represents CPU workers. When the caller does not define it,
    CPU workers default to ``cpu_cores // 2 + 1`` and IO workers default to
    ``cpu_cores * 2``. When defined, IO workers are twice the requested CPU
    workers.
    """
    if max_workers is not None:
        cpu_workers = max(1, int(max_workers))
        return WorkerCounts(cpu_workers=cpu_workers, io_workers=cpu_workers * 2)

    cores = max(1, int(cpu_cores if cpu_cores is not None else (os.cpu_count() or 1)))
    return WorkerCounts(
        cpu_workers=max(1, (cores // 2) + 1),
        io_workers=max(1, cores * 2),
    )


__all__ = ["WorkerCounts", "resolve_worker_counts"]
