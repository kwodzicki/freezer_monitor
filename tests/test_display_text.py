"""Tests that display text comes from cached readings, never from the bus"""

import math
import time

import pytest

import freezer_monitor as fm
from freezer_monitor.sensors.basesensor import Reading
from helpers import wait_until


@pytest.fixture
def sensor(make_sensor):
    return make_sensor(interval=0.2)


class TestDisplayText:

    def test_placeholder_before_first_poll(self, sensor):
        assert sensor.latest is None
        assert sensor.display_text() == ["T", "T :    --- C"]

    def test_never_touches_the_bus(self, sensor, hold_lock):
        """A busy I2C bus must not slow down (or blank) the display"""

        sensor._publish(-18.0, 55.0)
        hold_lock(fm.I2C_LOCK, 2.0)

        t0 = time.monotonic()
        text = sensor.display_text()
        assert time.monotonic() - t0 < 0.1  # no 0.3 s lock wait
        assert text == ["T", "T :  -18.0 C"]

    def test_does_not_inflate_poll_counter(self, sensor):
        sensor._publish(-18.0, 55.0)
        for _ in range(25):
            sensor.display_text()
        assert sensor.nn == 0

    def test_stale_reading_shows_placeholder(self, sensor):
        sensor._reading = Reading(
            -18.0, 55.0, time.monotonic() - 10 * sensor.interval,
        )
        assert sensor.display_text()[1] == "T :    --- C"

    def test_nan_reading_shows_placeholder(self, sensor):
        sensor._publish(math.nan, math.nan)
        assert sensor.display_text()[1] == "T :    --- C"

    def test_fahrenheit_unit_label(self, make_sensor):
        sensor = make_sensor(units="degF")
        sensor._publish(0.0, 50.0)
        assert sensor.display_text()[1] == "T :    0.0 F"

    def test_polling_thread_publishes_readings(self, sensor):
        sensor.start()
        assert wait_until(lambda: sensor.latest is not None)
        assert sensor.latest.temp == pytest.approx(-18.0)
        assert sensor.latest.rh == pytest.approx(55.0)
        assert not sensor.is_stale(sensor.latest)


class TestSensorInfo:

    def test_does_not_hold_scan_lock_while_building_text(
        self, make_sensors, monkeypatch,
    ):
        sensors = make_sensors()
        sensor = sensors._sensors["sensor.0.SHT30"]
        held = []

        def spy():
            # Lock is free iff sensor_info released it before building text
            got = fm.SCAN_LOCK.acquire(timeout=0.5)
            held.append(not got)
            if got:
                fm.SCAN_LOCK.release()
            return ["x", "y"]

        monkeypatch.setattr(sensor, "display_text", spy)
        assert sensors.sensor_info() == {sensor.name: ["x", "y"]}
        assert held == [False]

    def test_no_i2c_access_while_bus_busy(self, make_sensors, hold_lock):
        sensors = make_sensors()
        sensor = sensors._sensors["sensor.0.SHT30"]
        assert wait_until(lambda: sensor.latest is not None)

        hold_lock(fm.I2C_LOCK, 2.0)
        t0 = time.monotonic()
        info = sensors.sensor_info()
        assert time.monotonic() - t0 < 0.1
        assert info[sensor.name][1].startswith("T :")

    def test_returns_none_when_scan_lock_busy(self, make_sensors, hold_lock):
        sensors = make_sensors()
        hold_lock(fm.SCAN_LOCK, 1.0)
        assert sensors.sensor_info() is None
