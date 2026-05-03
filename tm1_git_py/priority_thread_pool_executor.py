import sys
import queue
import random
import atexit
import weakref
import threading
from itertools import count
from concurrent.futures.thread import ThreadPoolExecutor, _base, _WorkItem, _python_exit, _threads_queues

NULL_ENTRY = (sys.maxsize, sys.maxsize, None)
_shutdown = False


class _PriorityWorkQueue(queue.PriorityQueue):
    def put(self, item, *args, **kwargs):
        if item is None:
            item = NULL_ENTRY
        return super().put(item, *args, **kwargs)


def python_exit():
    """

    Cleanup before system exit

    """
    global _shutdown
    _shutdown = True
    items = list(_threads_queues.items())
    for t, q in items:
        q.put(NULL_ENTRY)
    for t, q in items:
        t.join(timeout=0.2)

atexit.unregister(_python_exit)
atexit.register(python_exit)

def _worker(executor_reference, ctx, work_queue):
    """

    Worker

    :param executor_reference: executor function
    :type executor_reference: callable
    :param work_queue: work queue
    :type work_queue: queue.PriorityQueue

    """
    try:
        if ctx is not None:
            ctx.initialize()
    except BaseException:
        _base.LOGGER.critical('Exception in initializer:', exc_info=True)
        executor = executor_reference()
        if executor is not None:
            executor._initializer_failed()
        return
    try:
        while True:
            try:
                work_item = work_queue.get_nowait()
            except queue.Empty:
                executor = executor_reference()
                if executor is not None and hasattr(executor, "_idle_semaphore"):
                    executor._idle_semaphore.release()
                del executor
                work_item = work_queue.get(block=True)

            if work_item[0] != sys.maxsize:
                work_item = work_item[2]
                if ctx is None:
                    work_item.run()
                else:
                    work_item.run(ctx)
                del work_item
                continue
            executor = executor_reference()
            if _shutdown or executor is None or executor._shutdown:
                if executor is not None:
                    executor._shutdown = True
                work_queue.put(NULL_ENTRY)
                return
            del executor
    except BaseException:
        _base.LOGGER.critical('Exception in worker', exc_info=True)
    finally:
        if ctx is not None:
            ctx.finalize()



class PriorityThreadPoolExecutor(ThreadPoolExecutor):
    """

    Thread pool executor with priority queue (lowest priority value runs first).

    """
    def __init__(self, max_workers=None, thread_name_prefix=''):
        """

        Initializes a new PriorityThreadPoolExecutor instance

        :param max_workers: the maximum number of threads that can be used to execute the given calls
        :type max_workers: int

        """
        super(PriorityThreadPoolExecutor, self).__init__(max_workers, thread_name_prefix)

        # change work queue type to queue.PriorityQueue

        self._work_queue = _PriorityWorkQueue()
        self._sequence = count()

    # ------------------------------------------------------------------------------------------------------------------

    def submit(self, fn, /, *args, **kwargs):
        """

        Sending the function to the execution queue

        :param fn: function being executed
        :type fn: callable
        :param args: function's positional arguments
        :param kwargs: function's keywords arguments
        :return: future instance
        :rtype: _base.Future

        Added keyword:

        - priority (integer, lower runs earlier)

        """
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError('cannot schedule new futures after shutdown')

            priority = kwargs.get('priority', random.randint(0, sys.maxsize-1))
            if 'priority' in kwargs:
                del kwargs['priority']

            f = _base.Future()
            task = (
                self._resolve_work_item_task(fn, args, kwargs)
                if hasattr(self, "_resolve_work_item_task")
                else None
            )
            try:
                w = _WorkItem(f, task)
            except TypeError:
                w = _WorkItem(f, fn, args, kwargs)

            self._work_queue.put((priority, next(self._sequence), w))
            self._adjust_thread_count()
            return f

    # ------------------------------------------------------------------------------------------------------------------

    def _adjust_thread_count(self):
        """

        Attempt to start a new thread

        """
        if hasattr(self, "_idle_semaphore") and self._idle_semaphore.acquire(timeout=0):
            return

        def weak_ref_cb(_, q=self._work_queue):
            q.put(NULL_ENTRY)
        if len(self._threads) < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self, len(self._threads))
            ctx = self._create_worker_context() if hasattr(self, "_create_worker_context") else None
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(weakref.ref(self, weak_ref_cb), ctx, self._work_queue),
            )
            t.daemon = True
            t.start()
            self._threads.add(t)
            _threads_queues[t] = self._work_queue

    # ------------------------------------------------------------------------------------------------------------------

    def shutdown(self, wait=True):
        """

        Pool shutdown

        :param wait: if True wait for all threads to complete
        :type wait: bool

        """
        with self._shutdown_lock:
            self._shutdown = True
            self._work_queue.put(NULL_ENTRY)
        if wait:
            for t in self._threads:
                t.join()
        else:
            for t in list(self._threads):
                _threads_queues.pop(t, None)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is KeyboardInterrupt:
            self.shutdown(wait=False)
            return False
        self.shutdown(wait=True)
        return False