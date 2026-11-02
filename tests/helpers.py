"""
Plain helpers shared by the tests (fixtures live in conftest.py)

This module must not import freezer_monitor: conftest.py has to set up the
environment (HOME, fake hardware libraries) before that happens.

"""

import time


class FakeSHT31D:
    """
    Stand-in for adafruit_sht31d.SHT31D

    Class attributes control behaviour and are reset before every test:

        status_failures: next N reads of `status` raise OSError
        instances: number of objects constructed so far

    """

    status_failures = 0
    instances = 0
    temperature = -18.0
    relative_humidity = 55.0

    def __init__(self, i2c_bus, address=0x44):
        type(self).instances += 1
        self.heater = False

    @property
    def status(self):
        cls = type(self)
        if cls.status_failures > 0:
            cls.status_failures -= 1
            raise OSError(121, "Remote I/O error")
        return 0x8010

    @classmethod
    def reset(cls):
        cls.status_failures = 0
        cls.instances = 0


def wait_until(condition, timeout=5.0, interval=0.01):
    """Poll `condition()` until it is truthy or `timeout` s pass"""

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(interval)
    return bool(condition())


class FakeMux:
    """Minimal stand-in for the TCA9548A multiplexer"""

    address = 0x70

    def __getitem__(self, channel):
        return object()


class ScanEnv:
    """
    Controls what Sensors.scan_for_sensors() 'finds'

    Attributes:
        settings: dict returned in place of the settings file
        load_calls: how many times the settings were loaded (i.e., how many
            scans actually ran their body)
        load_delay: seconds each settings load takes (makes a scan slow)
        on_channel: callable(channel) -> list; result of the mux scan.
            Default: the device is found on the requested channel.

    """

    def __init__(self):
        self.settings = {
            "sensor.0.SHT30": {"name": "Freezer", "interval": 10.0},
        }
        self.load_calls = 0
        self.load_delay = 0.0
        self.on_channel = lambda channel: [channel]


def raise_os_error(*args, **kwargs):
    """Raise the error the Linux I2C driver gives for a NACK"""

    raise OSError(121, "Remote I/O error")
