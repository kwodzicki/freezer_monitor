"""Tests for shutting down Sensors: join() and scans that race with it"""

import threading
import time

import freezer_monitor as fm
from freezer_monitor.sensors import Sensors
from helpers import FakeMux, FakeSHT31D, wait_until

KEY = "sensor.0.SHT30"


class StuckSensor:
    """A sensor thread that never stops"""

    name = "Stuck"

    def remove(self):
        pass

    def join(self, timeout=None):
        time.sleep(timeout if timeout is not None else 60)

    def is_alive(self):
        return True


class BrokenJoinSensor(StuckSensor):
    name = "Broken"

    def join(self, timeout=None):
        raise RuntimeError("cannot join")


def run_in_thread(func):
    """Run func() in a thread; returns (thread, list of elapsed seconds)"""

    elapsed = []

    def target():
        start = time.monotonic()
        func()
        elapsed.append(time.monotonic() - start)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, elapsed


class TestJoin:

    def test_stops_sensors_and_cancels_rescan(self, make_sensors):
        sensors = make_sensors()
        sensor = sensors._sensors[KEY]
        timer = sensors._rescan

        fm.STOP_EVENT.set()
        sensors.join()

        assert not sensor.is_alive()
        assert timer.finished.is_set()  # cancelled
        assert len(sensors._sensors) == 0

    def test_works_without_stop_event(self, make_sensors):
        """join() itself must be enough to stop the sensors"""

        sensors = make_sensors()
        sensor = sensors._sensors[KEY]

        sensors.join()

        assert not sensor.is_alive()

    def test_not_skipped_when_scan_lock_is_busy(
        self, make_sensors, hold_lock,
    ):
        """
        join() used to be wrapped in a 1 s lock-or-skip decorator, so a scan
        in progress turned it into a silent no-op.

        """

        sensors = make_sensors()
        sensor = sensors._sensors[KEY]
        timer = sensors._rescan

        hold_lock(fm.SCAN_LOCK, 1.5)  # longer than the 0.3 s LOCK_TIMEOUT
        fm.STOP_EVENT.set()
        start = time.monotonic()
        sensors.join()

        assert time.monotonic() - start >= 1.0  # it waited for the scan
        assert not sensor.is_alive()
        assert timer.finished.is_set()
        assert len(sensors._sensors) == 0

    def test_gives_up_waiting_for_lock_but_still_stops_sensors(
        self, monkeypatch, make_sensors, hold_lock,
    ):
        monkeypatch.setattr(Sensors, "JOIN_LOCK_TIMEOUT", 0.3)
        sensors = make_sensors()
        sensor = sensors._sensors[KEY]

        hold_lock(fm.SCAN_LOCK, 2.0)
        sensors.join()

        assert not sensor.is_alive()

    def test_lock_released_afterwards(self, make_sensors):
        sensors = make_sensors()
        sensors.join()

        assert fm.SCAN_LOCK.acquire(timeout=0.5)
        fm.SCAN_LOCK.release()

    def test_bounded_when_a_sensor_will_not_stop(
        self, monkeypatch, make_sensors,
    ):
        monkeypatch.setattr(Sensors, "JOIN_TIMEOUT", 0.5)
        sensors = make_sensors()
        sensors._sensors["stuck"] = StuckSensor()

        thread, elapsed = run_in_thread(sensors.join)
        thread.join(timeout=5)

        assert elapsed, "join() blocked on a sensor that never stops"
        assert elapsed[0] < 3.0

    def test_one_broken_sensor_does_not_stop_the_others(self, make_sensors):
        sensors = make_sensors()
        good = sensors._sensors[KEY]
        sensors._sensors["broken"] = BrokenJoinSensor()

        sensors.join()  # must not raise

        assert not good.is_alive()

    def test_safe_before_first_rescan_was_scheduled(self, make_sensors):
        fm.STOP_EVENT.set()  # _scan_and_rearm returns before creating a Timer
        sensors = make_sensors()
        assert sensors._rescan is None

        sensors.join()  # must not raise AttributeError


class TestScanRacingShutdown:

    def test_scan_in_flight_does_not_start_sensors_after_shutdown_began(
        self, make_sensors, scan_env,
    ):
        """
        The old sensor threads exit on STOP_EVENT, so an in-flight scan used
        to find them dead and happily start new ones mid-shutdown.

        """

        sensors = make_sensors()
        assert FakeSHT31D.instances == 1

        scan_env.load_delay = 1.0  # makes the scan slow
        scan, _ = run_in_thread(sensors.scan_for_sensors)
        time.sleep(0.2)  # the scan is now inside its (slow) settings load

        fm.STOP_EVENT.set()
        sensors.join()
        scan.join(timeout=5)

        assert FakeSHT31D.instances == 1  # nothing new was built

    def test_scan_does_nothing_once_shutdown_began(
        self, make_sensors, scan_env, mail_log,
    ):
        sensors = make_sensors()
        sensors.join()
        calls_before = FakeSHT31D.instances

        sensors.scan_for_sensors()

        assert FakeSHT31D.instances == calls_before
        assert len(sensors._sensors) == 0
        assert mail_log == []

    def test_no_false_alarm_when_shutdown_begins_mid_scan(
        self, make_sensors, scan_env, mail_log,
    ):
        sensors = make_sensors()
        sensors.join()  # clears sensors; sets sensors._closing
        sensors._closing.clear()  # re-open so the scan starts normally
        mail_log.clear()

        def not_found_and_shutdown(channel):
            sensors._closing.set()  # shutdown begins during this pass
            return []

        scan_env.on_channel = not_found_and_shutdown
        sensors.scan_for_sensors()

        assert mail_log == []
