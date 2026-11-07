"""
Shared fixtures for the freezer_monitor test suite.

Nothing here needs hardware:

* $HOME is pointed at a throw-away directory BEFORE freezer_monitor is
  imported, because the package creates its log/data directories under $HOME
  at import time.
* adafruit_sht31d is replaced by FakeSHT31D (and adafruit_tca9548a by a stub
  if it cannot be imported), so no I2C bus is touched.
* EMailer.sendMail is replaced by a recorder, so no mail is ever sent.

Every test starts with STOP_EVENT cleared and the global locks free, and every
thread a test creates is stopped and joined afterwards, so tests cannot leak
state into each other.

"""

import atexit
import os
import shutil
import sys
import tempfile
import threading
import time
import types
from unittest import mock

import pytest

# --------------------------------------------------------------------------
# Environment set-up: must run before `import freezer_monitor`
# --------------------------------------------------------------------------
_HOME = tempfile.mkdtemp(prefix="freezer_monitor_tests_")
os.environ["HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)

from helpers import FakeMux, FakeSHT31D, ScanEnv  # noqa: E402

_fake_sht = types.ModuleType("adafruit_sht31d")
_fake_sht.SHT31D = FakeSHT31D
sys.modules["adafruit_sht31d"] = _fake_sht

try:
    import adafruit_tca9548a  # noqa: F401  (just checking it imports)
except Exception:  # not installed / cannot import on this platform
    sys.modules["adafruit_tca9548a"] = mock.MagicMock()

import freezer_monitor as fm  # noqa: E402
import freezer_monitor.sensors as sensors_pkg  # noqa: E402
from freezer_monitor import thread_utils  # noqa: E402
from freezer_monitor.emailer import EMailer  # noqa: E402
from freezer_monitor.sensors import Sensors, sht  # noqa: E402

TEST_LOCK_TIMEOUT = 0.3  # Shortened from 1.0 s so lock-timeout tests are quick


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def mail_log(monkeypatch):
    """List of the subjects of every email 'sent' during the test"""

    sent = []

    def record(self, subject, *args, **kwargs):
        sent.append(subject)

    monkeypatch.setattr(EMailer, "sendMail", record)
    return sent


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, mail_log):
    """Fresh global state in; all threads stopped and joined out"""

    monkeypatch.setattr(thread_utils, "LOCK_TIMEOUT", TEST_LOCK_TIMEOUT)
    FakeSHT31D.reset()

    # Wait out lock holders left over from a previous test
    for lock in (fm.I2C_LOCK, fm.SCAN_LOCK):
        assert lock.acquire(timeout=10), f"{lock} still held at test start"
        lock.release()
    fm.STOP_EVENT.clear()

    before = set(threading.enumerate())
    yield

    fm.STOP_EVENT.set()  # tells every sensor/heater thread to exit
    new = [t for t in threading.enumerate() if t not in before]
    for thread in new:
        if isinstance(thread, threading.Timer):
            thread.cancel()
    for thread in new:
        thread.join(timeout=10)
    leaked = [t.name for t in new if t.is_alive() and not t.daemon]
    fm.STOP_EVENT.clear()
    assert not leaked, f"Test leaked running threads: {leaked}"


@pytest.fixture
def hold_lock():
    """
    hold_lock(lock, seconds): hold `lock` from another thread for `seconds`

    Returns once the lock is actually held.

    """

    threads = []

    def _hold(lock, seconds):
        held = threading.Event()

        def run():
            lock.acquire()
            held.set()
            time.sleep(seconds)
            lock.release()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        threads.append(thread)
        assert held.wait(5), "could not take lock in helper thread"
        return thread

    yield _hold
    for thread in threads:
        thread.join(timeout=15)


@pytest.fixture
def make_sensor():
    """
    make_sensor(interval=0.2, name='T') -> SHT30 (not started)

    A sensor starts its CSV logging thread when it is constructed, but only
    closes it when run() ends; so sensors a test never starts are closed here.

    """

    made = []

    def _make(interval=0.2, name="T", **kwargs):
        sensor = sht.SHT30(object(), name=name, interval=interval, **kwargs)
        made.append(sensor)
        return sensor

    yield _make

    for sensor in made:
        sensor.data_log.close()
        sensor.data_log.join(timeout=5)


@pytest.fixture
def scan_env(monkeypatch):
    """Replaces the settings file and mux scan used by Sensors"""

    env = ScanEnv()

    def load_settings():
        env.load_calls += 1
        time.sleep(env.load_delay)
        return env.settings

    monkeypatch.setattr(sensors_pkg, "load_settings", load_settings)
    monkeypatch.setattr(
        sensors_pkg,
        "muxer_device_on_channel",
        lambda muxer, address, channel: env.on_channel(channel),
    )
    return env


@pytest.fixture
def make_sensors(scan_env):
    """make_sensors() -> Sensors; runs the first scan like main() does"""

    def _make():
        return Sensors(FakeMux())

    return _make
