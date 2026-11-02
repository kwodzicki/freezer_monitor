"""Tests for Sensors: scanning, the rescan timer, and sensor health checks"""

import time

import pytest

import freezer_monitor as fm
from freezer_monitor.sensors import Sensors
from helpers import FakeSHT31D, wait_until

KEY = "sensor.0.SHT30"


class TestInitialScan:

    def test_creates_and_starts_configured_sensor(self, make_sensors):
        sensors = make_sensors()

        assert len(sensors) == 1
        assert sensors._sensors[KEY].is_alive()
        assert FakeSHT31D.instances == 1

    def test_ignores_non_sensor_settings(self, make_sensors, scan_env):
        scan_env.settings["email.host"] = {"name": "not a sensor"}
        sensors = make_sensors()

        assert len(sensors) == 1

    def test_no_sensors_found_sends_alert(self, make_sensors, scan_env, mail_log):
        scan_env.on_channel = lambda channel: []  # nothing on the mux
        sensors = make_sensors()

        assert len(sensors) == 0
        assert mail_log == ["No Sensors!!!"]


class TestRescanTimer:

    def test_rescans_keep_happening_after_a_failed_lock_grab(
        self, monkeypatch, make_sensors, scan_env, hold_lock,
    ):
        """
        A scan that cannot get SCAN_LOCK must not end the rescan chain

        (The next Timer used to be created inside the locked function, so one
        missed lock meant no more rescans, ever.)

        """

        monkeypatch.setattr(Sensors, "RESCAN", 0.4)
        sensors = make_sensors()
        assert scan_env.load_calls == 1

        hold_lock(fm.SCAN_LOCK, 0.9)  # the scan due at ~0.4 s will miss it
        time.sleep(3.0)
        assert sensors._rescan is not None

        assert scan_env.load_calls >= 4  # initial + at least 3 more

    def test_rescan_timer_is_daemon(self, make_sensors):
        """A forgotten timer must never keep the process alive"""

        sensors = make_sensors()
        assert sensors._rescan.daemon is True

    def test_no_rescan_scheduled_after_stop_event(self, make_sensors):
        sensors = make_sensors()
        timer = sensors._rescan

        fm.STOP_EVENT.set()
        sensors._scan_and_rearm()

        assert sensors._rescan is timer  # no new Timer created


class TestHealthChecks:

    def test_healthy_sensor_is_kept(self, make_sensors):
        sensors = make_sensors()
        original = sensors._sensors[KEY]

        sensors.scan_for_sensors()

        assert sensors._sensors[KEY] is original
        assert FakeSHT31D.instances == 1

    def test_busy_bus_does_not_tear_down_live_sensor(
        self, make_sensors, hold_lock,
    ):
        sensors = make_sensors()
        original = sensors._sensors[KEY]

        hold_lock(fm.I2C_LOCK, 1.0)
        sensors.scan_for_sensors()  # status comes back BUSY

        assert sensors._sensors[KEY] is original
        assert original.is_alive()

    def test_dead_thread_is_rebuilt_even_though_status_answers(
        self, make_sensors,
    ):
        """
        status only proves the hardware answers, not that our thread is
        polling it; a dead thread has to be replaced.

        """

        sensors = make_sensors()
        original = sensors._sensors[KEY]
        original.remove()
        original.join(timeout=3)
        assert not original.is_alive()

        sensors.scan_for_sensors()

        replacement = sensors._sensors[KEY]
        assert replacement is not original
        assert replacement.is_alive()
        assert FakeSHT31D.instances == 2

    def test_unresponsive_sensor_rebuilt_without_duplicate_threads(
        self, make_sensors,
    ):
        sensors = make_sensors()
        original = sensors._sensors[KEY]

        FakeSHT31D.status_failures = 99
        sensors.scan_for_sensors()

        assert not original.is_alive()  # old thread was stopped...
        assert sensors._sensors[KEY] is not original  # ...and replaced
        assert len(sensors._sensors) == 1

    def test_sensor_that_vanished_is_dropped_and_alert_sent(
        self, make_sensors, scan_env, mail_log,
    ):
        sensors = make_sensors()
        original = sensors._sensors[KEY]

        FakeSHT31D.status_failures = 99
        scan_env.on_channel = lambda channel: []  # gone from the mux too
        sensors.scan_for_sensors()

        assert KEY not in sensors._sensors
        assert not original.is_alive()
        assert "No Sensors!!!" in mail_log

    @pytest.mark.xfail(
        strict=True,
        reason="Known gap: status has no retry, so a single transient I2C "
        "read error tears down and rebuilds a healthy sensor. Remove this "
        "marker once status retries before giving up.",
    )
    def test_single_transient_status_error_does_not_rebuild_sensor(
        self, make_sensors,
    ):
        sensors = make_sensors()
        original = sensors._sensors[KEY]

        FakeSHT31D.status_failures = 1  # one glitch, then fine
        sensors.scan_for_sensors()

        assert sensors._sensors[KEY] is original


class TestRemove:

    def test_remove_wakes_sensor_thread_promptly(self, make_sensors):
        """join() holds SCAN_LOCK, so it must not wait out a 10 s interval"""

        sensors = make_sensors()  # sensor interval is 10 s
        sensor = sensors._sensors[KEY]
        assert wait_until(lambda: sensor.nn >= 1)

        start = time.monotonic()
        sensor.remove()
        sensor.join(timeout=5)

        assert not sensor.is_alive()
        assert time.monotonic() - start < 1.0
