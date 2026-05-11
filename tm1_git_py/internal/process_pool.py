import logging
import multiprocessing
import signal
import sys
import threading
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Literal, Optional, Tuple

logger = logging.getLogger(__name__)

PoolShutdownMode = Literal["aggressive", "graceful_blocking", "graceful_bounded"]

# Used by ``graceful_bounded`` when callers want workers to finish voluntarily first.
DEFAULT_GRACEFUL_POOL_SHUTDOWN_TIMEOUT_SEC = 120.0


def ignore_sigint_in_worker() -> None:
    """Let the parent process handle Ctrl+C for process-pool workers."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def process_pool_executor_kwargs(
    *,
    max_workers: int,
    initializer: Callable[..., Any],
    initargs: Tuple[Any, ...] = (),
) -> dict[str, Any]:
    """Keyword arguments for :class:`ProcessPoolExecutor`.

    On Python 3.11+, sets ``mp_context`` to ``spawn`` so workers are not forked
    from a multi-threaded parent (avoids common Linux stalls/deadlocks when a
    progress thread or ``Manager`` thread is already running).
    """
    kwargs: dict[str, Any] = {
        "max_workers": max_workers,
        "initializer": initializer,
        "initargs": initargs,
    }
    if sys.version_info >= (3, 11):
        kwargs["mp_context"] = multiprocessing.get_context("spawn")
    return kwargs


def _shutdown_process_pool_aggressive(pool: ProcessPoolExecutor, *, log: bool) -> None:
    try:
        terminate_workers = getattr(pool, "terminate_workers", None)
        if callable(terminate_workers):
            try:
                terminate_workers()
            except Exception as exc:
                if log:
                    logger.warning(
                        "ProcessPoolExecutor terminate_workers failed: %s",
                        exc,
                        exc_info=True,
                    )
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception as exc:
        if log:
            logger.debug(
                "ProcessPoolExecutor aggressive shutdown (idempotent): %s",
                exc,
                exc_info=True,
            )


def _shutdown_process_pool_graceful_blocking(pool: ProcessPoolExecutor, *, log: bool) -> None:
    try:
        pool.shutdown(wait=True, cancel_futures=False)
    except Exception as exc:
        if log:
            logger.warning(
                "ProcessPoolExecutor graceful shutdown failed: %s",
                exc,
                exc_info=True,
            )
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            if log:
                logger.debug(
                    "ProcessPoolExecutor fallback shutdown(wait=False) failed",
                    exc_info=True,
                )


def _shutdown_process_pool_graceful_bounded(
    pool: ProcessPoolExecutor,
    *,
    graceful_timeout_sec: float,
    log: bool,
) -> None:
    def _graceful_shutdown() -> None:
        try:
            pool.shutdown(wait=True, cancel_futures=False)
        except Exception:
            if log:
                logger.debug(
                    "ProcessPoolExecutor graceful shutdown (bounded wait) failed",
                    exc_info=True,
                )

    waiter = threading.Thread(
        target=_graceful_shutdown,
        name="process_pool_graceful_shutdown",
        daemon=True,
    )
    waiter.start()
    timeout = max(0.01, float(graceful_timeout_sec))
    waiter.join(timeout=timeout)
    if waiter.is_alive():
        if log:
            logger.warning(
                "ProcessPoolExecutor did not shut down within %.0fs; forcing termination",
                timeout,
            )
        _shutdown_process_pool_aggressive(pool, log=log)


def dispose_process_pool(
    pool: Optional[ProcessPoolExecutor],
    *,
    mode: PoolShutdownMode = "aggressive",
    graceful_timeout_sec: float = DEFAULT_GRACEFUL_POOL_SHUTDOWN_TIMEOUT_SEC,
    log: bool = True,
) -> None:
    """Tear down a :class:`ProcessPoolExecutor` in a single, policy-driven way.

    Idempotent: safe to call more than once on the same pool.

    Modes:

    * ``aggressive`` — Terminate workers (Python 3.12+ ``terminate_workers()`` when
      available) and ``shutdown(wait=False, cancel_futures=True)``. Use in
      ``finally`` blocks, interrupt paths, and after bounded grace expires.

    * ``graceful_blocking`` — ``shutdown(wait=True, cancel_futures=False)`` in the
      current thread; on failure, fall back to non-blocking cancel. Use only when
      workers are already known idle.

    * ``graceful_bounded`` — Start ``shutdown(wait=True)`` on a daemon thread, wait
      up to *graceful_timeout_sec*, then apply *aggressive* if still running. Avoids
      hanging the caller forever on a wedged worker.

    Stuck native code inside a worker cannot be guaranteed to exit without OS-level
    signals; this applies the strongest cleanup the stdlib executor exposes.
    """
    if pool is None:
        return

    if mode == "aggressive":
        _shutdown_process_pool_aggressive(pool, log=log)
        return
    if mode == "graceful_blocking":
        _shutdown_process_pool_graceful_blocking(pool, log=log)
        return
    if mode == "graceful_bounded":
        _shutdown_process_pool_graceful_bounded(
            pool,
            graceful_timeout_sec=graceful_timeout_sec,
            log=log,
        )
        return
    raise ValueError(f"Unknown pool shutdown mode: {mode!r}")


def shutdown_process_pool_now(pool: ProcessPoolExecutor) -> None:
    """Cancel queued work and stop workers without waiting during interrupt shutdown."""
    dispose_process_pool(pool, mode="aggressive", log=True)
