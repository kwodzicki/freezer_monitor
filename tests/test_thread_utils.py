"""Tests for freezer_monitor.thread_utils: FairLock, locker, LinkedEvent"""

import threading
import time

import pytest

from freezer_monitor.thread_utils import FairLock, LinkedEvent, locker


# --------------------------------------------------------------------------
# FairLock
# --------------------------------------------------------------------------
class TestFairLock:

    def test_mutual_exclusion(self):
        lock = FairLock("t")
        state = {"inside": 0, "max_inside": 0}

        def worker():
            for _ in range(15):
                assert lock.acquire(timeout=10)
                state["inside"] += 1
                state["max_inside"] = max(state["max_inside"], state["inside"])
                time.sleep(0.002)
                state["inside"] -= 1
                lock.release()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)

        assert state["max_inside"] == 1

    def test_waiters_served_in_arrival_order(self):
        lock = FairLock("t")
        order = []
        lock.acquire()

        def waiter(index):
            lock.acquire()
            order.append(index)
            lock.release()

        threads = []
        for index in range(6):
            thread = threading.Thread(target=waiter, args=(index,))
            thread.start()
            threads.append(thread)
            time.sleep(0.05)  # make sure this one is queued before the next

        lock.release()
        for thread in threads:
            thread.join(10)

        assert order == list(range(6))

    def test_acquire_times_out(self, hold_lock):
        lock = FairLock("t")
        hold_lock(lock, 0.6)

        start = time.monotonic()
        assert lock.acquire(timeout=0.1) is False
        assert 0.08 <= time.monotonic() - start < 0.5

    def test_timed_out_waiter_does_not_block_the_queue(self, hold_lock):
        """A waiter that gave up must not stay at the head of the queue"""

        lock = FairLock("t")
        hold_lock(lock, 0.4)

        assert lock.acquire(timeout=0.05) is False  # gives up
        # Once the holder lets go, a new waiter must get the lock; if the
        # abandoned waiter were still queued this would time out
        assert lock.acquire(timeout=2.0) is True
        lock.release()

    def test_acquire_without_timeout_waits_for_release(self, hold_lock):
        lock = FairLock("t")
        hold_lock(lock, 0.3)

        start = time.monotonic()
        assert lock.acquire() is True
        assert time.monotonic() - start >= 0.2
        lock.release()

    def test_context_manager(self):
        lock = FairLock("t")
        with lock:
            assert lock.acquire(timeout=0.05) is False
        assert lock.acquire(timeout=0.5) is True
        lock.release()


# --------------------------------------------------------------------------
# locker
# --------------------------------------------------------------------------
class TestLocker:

    def test_returns_function_result(self):
        lock = FairLock("t")

        @locker(lock)
        def func(a, b=2):
            return a + b

        assert func(1, b=5) == 6

    def test_preserves_function_metadata(self):
        @locker(FairLock("t"))
        def func():
            """docstring"""

        assert func.__name__ == "func"
        assert func.__doc__ == "docstring"

    def test_default_returned_when_lock_not_acquired(self, hold_lock):
        lock = FairLock("t")
        ran = []

        @locker(lock, default="fallback")
        def func():
            ran.append(True)
            return "real"

        hold_lock(lock, 1.0)
        assert func() == "fallback"
        assert ran == []  # body never ran

    def test_default_is_none_if_not_given(self, hold_lock):
        lock = FairLock("t")

        @locker(lock)
        def func():
            return "real"

        hold_lock(lock, 1.0)
        assert func() is None

    def test_tuple_default_can_be_unpacked(self, hold_lock):
        """The reason `default` exists: callers unpack the result"""

        lock = FairLock("t")

        @locker(lock, default=(float("nan"), float("nan")))
        def func():
            return 1.0, 2.0

        hold_lock(lock, 1.0)
        first, second = func()  # would raise TypeError if None
        assert first != first and second != second  # both NaN

    def test_default_returned_and_lock_released_when_function_raises(self):
        lock = FairLock("t")

        @locker(lock, default="fallback")
        def func():
            raise OSError(121, "Remote I/O error")

        assert func() == "fallback"
        assert lock.acquire(timeout=0.5) is True  # lock was released
        lock.release()

    def test_lock_released_after_normal_call(self):
        lock = FairLock("t")

        @locker(lock)
        def func():
            return 1

        func()
        assert lock.acquire(timeout=0.5) is True
        lock.release()


# --------------------------------------------------------------------------
# LinkedEvent
# --------------------------------------------------------------------------
class TestLinkedEvent:

    def test_wait_any_true_immediately_if_already_set(self):
        first, second = LinkedEvent(), LinkedEvent()
        second.set()

        start = time.monotonic()
        assert first.wait_any(second, timeout=5) is True
        assert time.monotonic() - start < 0.1

    @pytest.mark.parametrize("which", ["self", "other"])
    def test_wait_any_wakes_promptly_when_either_is_set(self, which):
        first, second = LinkedEvent(), LinkedEvent()
        result = []

        def waiter():
            result.append((first.wait_any(second, timeout=5), time.monotonic()))

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)

        set_at = time.monotonic()
        (first if which == "self" else second).set()
        thread.join(5)

        assert result[0][0] is True
        assert result[0][1] - set_at < 0.2

    def test_wait_any_times_out(self):
        first, second = LinkedEvent(), LinkedEvent()

        start = time.monotonic()
        assert first.wait_any(second, timeout=0.3) is False
        assert 0.25 <= time.monotonic() - start < 0.8

    def test_wait_any_without_timeout_waits_for_event(self):
        event = LinkedEvent()
        threading.Timer(0.2, event.set).start()

        start = time.monotonic()
        assert event.wait_any() is True
        assert 0.15 <= time.monotonic() - start < 1.0

    def test_no_lost_wakeups_when_set_races_with_wait(self):
        for _ in range(200):
            first, second = LinkedEvent(), LinkedEvent()
            result = []
            thread = threading.Thread(
                target=lambda: result.append(first.wait_any(second, timeout=3)),
            )
            thread.start()
            first.set()  # races with the thread entering wait_any
            thread.join(5)
            assert result == [True]

    def test_timeout_not_affected_by_wall_clock_step(self, monkeypatch):
        """
        An NTP step backwards must not stretch the wait

        (wait_any used time.time() for its deadline; a clock that steps back
        by N seconds stalled the wait by N seconds.)

        """

        offset = [0.0]
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + offset[0])

        event = LinkedEvent()
        threading.Timer(0.3, lambda: offset.__setitem__(0, -5.0)).start()

        start = time.monotonic()
        assert event.wait_any(timeout=1.0) is False
        assert time.monotonic() - start < 2.0

    def test_still_works_as_a_normal_event(self):
        event = LinkedEvent()
        assert event.wait(0.05) is False
        event.set()
        assert event.is_set()
        assert event.wait(0.05) is True
        event.clear()
        assert not event.is_set()
