"""
Tests that a scan never holds SCAN_LOCK across slow work

SCAN_LOCK guards the dict of sensors only. Whatever a scan is busy with (a
slow mux scan, waiting for a sick sensor thread to stop, ...), anything that
needs the dict (len(), sensor_info(), join()) must stay responsive.

"""

import threading
import time

import pytest

import freezer_monitor as fm
from freezer_monitor import emailer
from freezer_monitor.emailer import EMailer
from freezer_monitor.sensors import sht
from helpers import FakeSHT31D, wait_until

KEY = "sensor.0.SHT30"
REAL_SEND_MAIL = EMailer.sendMail  # Captured before fixtures replace it

FAST = 0.15  # s; "did not wait for the slow thing"


def start(func):
    thread = threading.Thread(target=func, daemon=True)
    thread.start()
    return thread


def timed(func, *args):
    t0 = time.monotonic()
    result = func(*args)
    return result, time.monotonic() - t0


class TestScanDoesNotBlockReaders:

    def test_slow_mux_scan(self, make_sensors, scan_env):
        scan_env.on_channel = lambda channel: []
        sensors = make_sensors()  # Nothing found, no sensors yet
        entered = threading.Event()

        def slow_mux(channel):
            entered.set()
            time.sleep(1.0)
            return []

        scan_env.on_channel = slow_mux
        scan = start(sensors.scan_for_sensors)
        assert entered.wait(5)  # scan is now inside the mux scan

        n, took = timed(len, sensors)
        assert n == 0 and took < FAST
        info, took = timed(sensors.sensor_info)
        assert info == {} and took < FAST

        scan.join(timeout=5)

    def test_slow_stop_of_unhealthy_sensor(
        self, make_sensors, monkeypatch,
    ):
        sensors = make_sensors()
        old = sensors._sensors[KEY]
        assert wait_until(lambda: old.latest is not None)

        stopping = threading.Event()
        real_join = old.join

        def slow_join(timeout=None):
            stopping.set()
            time.sleep(1.0)
            real_join(timeout)

        monkeypatch.setattr(old, "join", slow_join)
        FakeSHT31D.status_failures = 1  # one bad status read: rebuild it

        scan = start(sensors.scan_for_sensors)
        assert stopping.wait(5)  # scan is inside its (slow) join

        n, took = timed(len, sensors)
        assert n == 1 and took < FAST
        info, took = timed(sensors.sensor_info)
        assert KEY not in info and old.name in info and took < FAST

        scan.join(timeout=10)
        assert sensors._sensors[KEY] is not old  # and it was replaced

    def test_slow_settings_load(self, make_sensors, scan_env):
        sensors = make_sensors()
        scan_env.load_delay = 1.0
        scan = start(sensors.scan_for_sensors)
        time.sleep(0.2)

        _, took = timed(len, sensors)
        assert took < FAST
        scan.join(timeout=5)

    def test_lock_free_between_steps_of_a_scan(
        self, make_sensors, monkeypatch,
    ):
        """Whatever the scan is doing, SCAN_LOCK is not what it holds"""

        sensors = make_sensors()
        old = sensors._sensors[KEY]
        free = []

        def check(*args, **kwargs):
            got = fm.SCAN_LOCK.acquire(timeout=0.2)
            free.append(got)
            if got:
                fm.SCAN_LOCK.release()
            return args and None

        # status is read in the health check
        monkeypatch.setattr(
            type(old), "status", property(lambda self: check() or 0x8010),
        )
        sensors.scan_for_sensors()
        assert free == [True]


class TestScansAreSerialized:

    def test_second_scan_is_skipped_not_queued(self, make_sensors, scan_env):
        sensors = make_sensors()
        assert scan_env.load_calls == 1

        scan_env.load_delay = 1.0
        first = start(sensors.scan_for_sensors)
        assert wait_until(lambda: scan_env.load_calls == 2)

        _, took = timed(sensors.scan_for_sensors)
        assert took < FAST
        assert scan_env.load_calls == 2  # second one never started

        first.join(timeout=5)
        sensors.scan_for_sensors()  # free again, so this one does run
        assert scan_env.load_calls == 3

    def test_flag_released_after_a_failed_scan(
        self, make_sensors, scan_env, monkeypatch,
    ):
        sensors = make_sensors()

        def boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(sensors, "_snapshot", boom)
        sensors.scan_for_sensors()  # must not raise
        assert sensors._scan_running.acquire(blocking=False)
        sensors._scan_running.release()


class TestShutdownDuringScan:

    def test_sensor_built_during_shutdown_is_stopped(
        self, make_sensors, scan_env,
    ):
        """
        join() empties the dict and cannot see a sensor a scan is still
        building, so the scan has to stop it itself.

        """

        scan_env.on_channel = lambda channel: []
        sensors = make_sensors()  # Nothing found, no sensors yet

        entered = threading.Event()

        def slow_then_found(channel):
            entered.set()
            time.sleep(0.5)
            return [channel]

        scan_env.on_channel = slow_then_found
        scan = start(sensors.scan_for_sensors)
        assert entered.wait(5)

        sensors.join()  # no STOP_EVENT: join alone has to be enough
        scan.join(timeout=5)

        assert len(sensors._sensors) == 0
        running = [
            t for t in threading.enumerate()
            if isinstance(t, sht.SHT30) and t.is_alive()
        ]
        assert running == []

    def test_join_is_not_held_up_by_slow_scan_work(
        self, make_sensors, scan_env,
    ):
        """SCAN_LOCK is free during a slow scan, so join() does not wait"""

        sensors = make_sensors()
        original = sensors._sensors[KEY]
        entered = threading.Event()

        def slow_mux(channel):
            entered.set()
            time.sleep(1.0)
            return []

        scan_env.on_channel = slow_mux
        FakeSHT31D.status_failures = 99  # so the scan goes to the mux
        scan = start(sensors.scan_for_sensors)
        assert entered.wait(5)

        # join() must stop the sensors without sitting out the scan's work
        # to take SCAN_LOCK; it only waits (briefly) for the scan to wrap up
        t0 = time.monotonic()
        sensors.join()
        scan.join(timeout=5)
        assert not original.is_alive()
        assert time.monotonic() - t0 < 3.0


class TestSensorBuildFailures:

    def test_one_failing_sensor_does_not_stop_the_others(
        self, make_sensors, scan_env, monkeypatch,
    ):
        scan_env.settings = {
            "sensor.0.SHT30": {"name": "A", "interval": 10.0},
            "sensor.1.SHT30": {"name": "B", "interval": 10.0},
        }
        real_init = FakeSHT31D.__init__
        calls = []

        def flaky_init(self, i2c_bus, address=0x44):
            calls.append(1)
            if len(calls) == 1:
                raise OSError(121, "Remote I/O error")
            real_init(self, i2c_bus, address)

        monkeypatch.setattr(FakeSHT31D, "__init__", flaky_init)
        sensors = make_sensors()

        assert list(sensors._sensors) == ["sensor.1.SHT30"]
        assert len(calls) == 2  # second sensor was still attempted

    def test_failed_init_does_not_leak_the_data_log_thread(
        self, make_sensors, scan_env, monkeypatch,
    ):
        """
        The data log thread is started by BaseSensor.__init__ and is not a
        daemon; if the sensor then fails to initialise it must be closed or
        the process can never exit.

        """

        def always_fail(self, i2c_bus, address=0x44):
            raise OSError(121, "Remote I/O error")

        monkeypatch.setattr(FakeSHT31D, "__init__", always_fail)
        before = set(threading.enumerate())
        sensors = make_sensors()
        assert len(sensors) == 0

        leaked = [
            t for t in threading.enumerate()
            if t not in before
            and t.name != "MainThread"
            and not t.daemon
            and t is not sensors._rescan
        ]
        for thread in leaked:
            thread.join(timeout=2)  # closing is asynchronous
        assert not [t for t in leaked if t.is_alive()]

    def test_failed_start_does_not_leak_the_data_log_thread(
        self, make_sensors, scan_env, monkeypatch,
    ):
        """
        The sensor was constructed (so its data log thread is running) but
        start() failed (e.g., "can't start new thread"); run() will never
        happen to close the log, so the scan has to.

        """

        scan_env.on_channel = lambda channel: []
        sensors = make_sensors()  # Nothing found, no sensors yet

        def cannot_start(self):
            raise RuntimeError("can't start new thread")

        scan_env.on_channel = lambda channel: [channel]
        monkeypatch.setattr(sht.SHT30, "start", cannot_start)
        before = set(threading.enumerate())
        sensors.scan_for_sensors()  # must not raise

        assert len(sensors) == 0
        leaked = [
            t for t in threading.enumerate()
            if t not in before and not t.daemon
        ]
        for thread in leaked:
            thread.join(timeout=2)  # closing is asynchronous
        assert not [t for t in leaked if t.is_alive()]


class TestScanFailingPartWay:
    """
    _scan() commits from a `finally`, so a scan that blows up part way must
    still account for everything it had already done: no sensor thread is
    left running that nothing tracks.

    """

    TWO = {
        "sensor.0.SHT30": {"name": "A", "interval": 10.0},
        "sensor.1.SHT30": {"name": "B", "interval": 10.0},
    }

    def test_sensor_started_before_the_failure_is_registered(
        self, make_sensors, scan_env, monkeypatch,
    ):
        scan_env.settings = dict(self.TWO)
        scan_env.on_channel = lambda channel: []
        sensors = make_sensors()  # Nothing found, no sensors yet
        assert len(sensors) == 0

        real_build = sensors._build_sensor

        def build_then_blow_up(key, info):
            if key.endswith("1.SHT30"):
                raise RuntimeError("bug while building the second sensor")
            return real_build(key, info)

        scan_env.on_channel = lambda channel: [channel]
        monkeypatch.setattr(sensors, "_build_sensor", build_then_blow_up)
        sensors.scan_for_sensors()  # must not raise

        assert list(sensors._sensors) == ["sensor.0.SHT30"]
        assert sensors._sensors["sensor.0.SHT30"].is_alive()

    def test_sensor_is_not_forgotten_unless_it_was_stopped(
        self, make_sensors, scan_env, monkeypatch,
    ):
        """
        A sensor found unhealthy is only dropped from the dict once it has
        been stopped; if the scan fails first it must stay tracked.

        """

        scan_env.settings = dict(self.TWO)
        sensors = make_sensors()
        assert len(sensors) == 2
        first = sensors._sensors["sensor.0.SHT30"]
        second = sensors._sensors["sensor.1.SHT30"]

        calls = []

        def verdicts(sensor_obj, name):
            calls.append(name)
            if len(calls) == 1:
                return False  # First sensor looks unhealthy...
            raise RuntimeError("...then the check of the next one blows up")

        monkeypatch.setattr(sensors, "_is_healthy", verdicts)
        sensors.scan_for_sensors()  # must not raise

        assert sensors._sensors["sensor.0.SHT30"] is first
        assert sensors._sensors["sensor.1.SHT30"] is second
        assert first.is_alive() and second.is_alive()  # nothing was stopped


class TestSmtpTimeout:

    def test_smtp_connection_has_a_timeout(self, monkeypatch):
        """A dead mail server must not hold a thread forever"""

        seen = {}

        def fake_smtp(*args, **kwargs):
            seen.update(kwargs)
            raise OSError("no route to host")

        monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", fake_smtp)
        mailer = EMailer()
        mailer.send_to = "a@b.c"
        mailer.send_from = {
            "user": "u", "pass": "p", "server": "s", "port": 465,
        }

        assert REAL_SEND_MAIL(mailer, "subject", "body") is False
        assert seen.get("timeout") == pytest.approx(emailer.SMTP_TIMEOUT)
