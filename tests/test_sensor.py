"""Tests for the SHT30 sensor: poll(), status, and its polling thread"""

import math

import pytest

import freezer_monitor as fm
from freezer_monitor import BUSY
from helpers import FakeSHT31D, raise_os_error, wait_until


@pytest.fixture
def sensor(make_sensor):
    return make_sensor(interval=0.2)


# --------------------------------------------------------------------------
# poll()
# --------------------------------------------------------------------------
class TestPoll:

    def test_returns_temperature_and_humidity(self, sensor):
        temp, rh = sensor.poll()
        assert temp == pytest.approx(-18.0)
        assert rh == pytest.approx(55.0)

    def test_returns_nan_tuple_when_lock_times_out(self, sensor, hold_lock):
        """Callers unpack the result, so it must never be None"""

        hold_lock(fm.I2C_LOCK, 1.0)

        temp, rh = sensor.poll()
        assert math.isnan(temp) and math.isnan(rh)

    def test_returns_nan_tuple_when_both_reads_fail(self, sensor, monkeypatch):
        monkeypatch.setattr(
            FakeSHT31D, "temperature", property(raise_os_error),
        )
        monkeypatch.setattr(
            FakeSHT31D, "relative_humidity", property(raise_os_error),
        )

        temp, rh = sensor.poll()
        assert math.isnan(temp) and math.isnan(rh)

    def test_failed_temperature_read_keeps_good_humidity(
        self, sensor, monkeypatch,
    ):
        """The two values are read (and fail) independently"""

        monkeypatch.setattr(
            FakeSHT31D, "temperature", property(raise_os_error),
        )

        temp, rh = sensor.poll()
        assert math.isnan(temp)
        assert rh == pytest.approx(55.0)

    def test_lock_released_after_failed_read(self, sensor, monkeypatch):
        monkeypatch.setattr(
            FakeSHT31D, "temperature", property(raise_os_error),
        )
        sensor.poll()

        assert fm.I2C_LOCK.acquire(timeout=0.5)
        fm.I2C_LOCK.release()


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
class TestStatus:

    def test_returns_status_register(self, sensor):
        assert sensor.status == 0x8010

    def test_busy_when_bus_lock_not_acquired(self, sensor, hold_lock):
        hold_lock(fm.I2C_LOCK, 1.0)
        assert sensor.status is BUSY

    def test_none_when_read_fails(self, sensor):
        FakeSHT31D.status_failures = 99
        assert sensor.status is None

    def test_busy_and_none_are_distinguishable(self, sensor, hold_lock):
        """'Bus busy' must not look like 'sensor dead'"""

        FakeSHT31D.status_failures = 99
        assert sensor.status is None
        FakeSHT31D.status_failures = 0
        hold_lock(fm.I2C_LOCK, 1.0)
        assert sensor.status is BUSY
        assert BUSY is not None


# --------------------------------------------------------------------------
# polling thread
# --------------------------------------------------------------------------
class TestPollingThread:

    def test_polls_repeatedly(self, sensor):
        sensor.start()
        assert wait_until(lambda: sensor.nn >= 3, timeout=5)

    def test_survives_bus_lock_timeout(self, sensor, hold_lock):
        """
        One missed lock must not end the thread

        (poll() used to return None here, and the unpack in run() killed the
        thread, which the rescan could not see because status still worked.)

        """

        sensor.start()
        assert wait_until(lambda: sensor.nn >= 1)

        hold_lock(fm.I2C_LOCK, 1.0)  # several poll intervals, > LOCK_TIMEOUT
        wait_until(lambda: False, timeout=1.2)  # just let time pass
        assert sensor.is_alive()

        polled = sensor.nn  # polling resumes once the bus is free again
        assert wait_until(lambda: sensor.nn > polled, timeout=5)

    def test_survives_unexpected_error_in_loop(self, sensor, monkeypatch):
        def broken_write(*args, **kwargs):
            raise ValueError("unexpected")

        monkeypatch.setattr(sensor.data_log, "write", broken_write)
        sensor.start()

        assert wait_until(lambda: sensor.nn >= 3, timeout=5)
        assert sensor.is_alive()

    def test_stops_when_stop_event_set(self, sensor):
        sensor.start()
        assert wait_until(lambda: sensor.nn >= 1)

        fm.STOP_EVENT.set()
        sensor.join(timeout=3)
        assert not sensor.is_alive()

    def test_remove_wakes_thread_promptly(self, make_sensor):
        """remove() must not wait out the (long) polling interval"""

        sensor = make_sensor(interval=10.0)
        sensor.start()
        assert wait_until(lambda: sensor.nn >= 1)

        sensor.remove()
        sensor.join(timeout=2)
        assert not sensor.is_alive()
