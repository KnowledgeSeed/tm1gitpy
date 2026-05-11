from concurrent.futures import ProcessPoolExecutor

from tm1_git_py.internal.process_pool import dispose_process_pool, ignore_sigint_in_worker


def _worker_return_42() -> int:
    return 42


def test_dispose_process_pool_is_idempotent():
    pool = ProcessPoolExecutor(max_workers=1, initializer=ignore_sigint_in_worker)
    try:
        assert pool.submit(_worker_return_42).result() == 42
    finally:
        dispose_process_pool(pool, mode="aggressive", log=False)
        dispose_process_pool(pool, mode="aggressive", log=False)


def test_dispose_process_pool_graceful_bounded_idle_pool():
    pool = ProcessPoolExecutor(max_workers=1, initializer=ignore_sigint_in_worker)
    try:
        assert pool.submit(_worker_return_42).result() == 42
    finally:
        dispose_process_pool(pool, mode="graceful_bounded", graceful_timeout_sec=5.0, log=False)
