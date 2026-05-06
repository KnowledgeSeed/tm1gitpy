import signal
from concurrent.futures import ProcessPoolExecutor


def ignore_sigint_in_worker() -> None:
    """Let the parent process handle Ctrl+C for process-pool workers."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def shutdown_process_pool_now(pool: ProcessPoolExecutor) -> None:
    """Cancel queued work and stop workers without waiting during interrupt shutdown."""
    terminate_workers = getattr(pool, "terminate_workers", None)
    if callable(terminate_workers):
        terminate_workers()
        return
    pool.shutdown(wait=False, cancel_futures=True)
