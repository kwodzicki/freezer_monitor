import logging
import time
from functools import wraps
from threading import Lock, Event, Condition
from queue import Queue

LOCK_TIMEOUT = 1.0


class LinkedEvent(Event):
    # Class-level variables: Shared by EVERY instance of LinkedEvent
    # automatically
    _shared_lock = Lock()
    _shared_cond = Condition(_shared_lock)

    def __init__(self):
        super().__init__()

    def set(self):
        with LinkedEvent._shared_cond:
            super().set()
            # Wakes up anyone waiting on the shared lock
            LinkedEvent._shared_cond.notify_all()

    def clear(self):
        with LinkedEvent._shared_cond:
            super().clear()

    def wait_any(self, *other_events, timeout=None):
        all_events = [self] + list(other_events)

        if any(e.is_set() for e in all_events):
            return True

        with LinkedEvent._shared_cond:
            end_time = (
                time.monotonic() + timeout
                if timeout is not None else
                float('inf')
            )

            while not any(e.is_set() for e in all_events):
                remaining = None
                if timeout is not None:
                    remaining = end_time - time.monotonic()
                    if remaining <= 0:
                        return False

                # Everyone sleeps on the exact same global condition variable
                LinkedEvent._shared_cond.wait(timeout=remaining)

        return any(e.is_set() for e in all_events)


class FairLock:
    def __init__(self, name: str):
        self._lock = Lock()
        self._waiters = Queue()
        self._name = name

    def __repr__(self):

        return f"<{self._name}>"

    def __enter__(self):
        self.acquire()

    def __exit__(self, type, value, traceback):
        self.release()

    def acquire(self, timeout: float | None = None):
        event = Event()
        self._waiters.put(event)
        timeout = float('inf') if timeout is None else timeout
        start_time = time.monotonic()

        while True:
            if self._waiters.queue[0] is event and self._lock.acquire(False):
                self._waiters.get()
                return True

            if time.monotonic() - start_time >= timeout:
                # Timeout: remove event from queue
                with self._waiters.mutex:
                    try:
                        self._waiters.queue.remove(event)
                    except ValueError:
                        pass
                return False
            event.wait(0.01)  # Short sleep to avoid spinning

    def release(self):
        self._lock.release()


class _Busy:
    """Sentinel returned when a locked call was skipped (lock not acquired)"""

    def __repr__(self):
        return "BUSY"


def locker(lock, default=None):
    """
    Decorator to run a function while holding a lock

    Arguments:
        lock: Lock to hold (must have acquire(timeout=) and release())

    Keyword arguments:
        default: Value returned if the lock cannot be acquired within
            LOCK_TIMEOUT, or if the wrapped function raises an Exception.
            Choose something the caller can use directly (e.g., a
            (nan, nan) tuple for a function that returns two values), since
            callers will otherwise get None. Should be immutable.

    """

    log = logging.getLogger(__name__)

    def locker_decorator(func):

        @wraps(func)
        def wrapped(*args, **kwargs):
            if not lock.acquire(timeout=LOCK_TIMEOUT):
                log.warning(
                    "Failed to acquire lock '%s' for '%s'",
                    lock,
                    func.__qualname__,
                )
                return default

            log.log(5, "Acquired lock: %s", lock)
            try:
                return func(*args, **kwargs)
            except Exception as err:
                log.exception(
                    "Failed to run '%s': %s",
                    func.__qualname__,
                    err,
                )
                return default
            finally:
                lock.release()
                log.log(5, "Released lock: %s", lock)

        return wrapped
    return locker_decorator
