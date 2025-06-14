import logging
import os
from functools import wraps
from threading import Lock

import yaml
from adafruit_tca9548a import TCA9548A

from . import SETTINGS_FILE

LOCK_TIMEOUT = 1.0


class MyLock:

    def __init__(self, name: str):
        self._lock = Lock()
        self._name = name

    def __repr__(self):

        return f"<{self._name}>"

    def acquire(self, *args, **kwargs):
        return self._lock.acquire(*args, **kwargs)

    def release(self, *args, **kwargs):
        # traceback.print_tb
        return self._lock.release(*args, **kwargs)

    def __enter__(self):
        self.acquire()

    def __exit__(self, type, value, traceback):
        self.release()


I2C_LOCK = MyLock('I2C_LOCK')
SCAN_LOCK = MyLock('SCAN_LOCK')


def i2c_lock(lock):
    log = logging.getLogger(__name__)

    def i2c_lock_decorator(func):

        @wraps(func)
        def wrapped(*args, **kwargs):
            res = None

            if not lock.acquire(timeout=LOCK_TIMEOUT):
                log.warning(
                    "Failed to acquire lock '%s', display not updated!",
                    lock,
                )
                return res

            log.log(5, "Acquired lock: %s", lock)
            try:
                res = func(*args, **kwargs)
            except Exception as err:
                log.exception("Failed to run function: %s", err)

            lock.release()
            log.log(5, "Released lock: %s", lock)

            return res
        return wrapped
    return i2c_lock_decorator


def load_settings(fpath: str | None = None) -> dict:
    """
    Load in settings YAML file

    Keyword arguments:
        fpath (str, None): Path to file to load, if None set, then will use
            package default value SETTINGS_FILE

    Returns:
        dict: All settings

    """

    log = logging.getLogger(__name__)
    fpath = fpath or SETTINGS_FILE
    if not os.path.isfile(fpath):
        log.warning("Settings file not exist, nothing to load: %s", fpath)
        return {}

    log.info("Loading settings from: %s", fpath)
    with open(fpath, mode='r') as fid:
        return yaml.load(
            fid,
            Loader=yaml.SafeLoader,
        )


def i2c_devcie_on_channel(i2c, dev_address: int) -> bool:
    """
    Try to find device on I2C bus

    Similar to the muxer function below, but this is intended to look
    for a device on the "main" I2C bus rather than through the muxer.

    """

    # Try to get the lock; should acquire eventually. This is recommended:
    # https://learn.adafruit.com/circuitpython-basics-i2c-and-spi/i2c-devices
    while not i2c.try_lock():
        pass

    addresses = i2c.scan()
    i2c.unlock()

    return dev_address in addresses


def muxer_device_on_channel(
    muxer: TCA9548A,
    dev_address: int,
    channel: int | list[int] | None = None,
) -> list[int]:
    """
    Check if device on muxer channel via device address

    Arguments:
        muxer: Muxer object to scan
        dev_address: Address of device to look for on each channel of the
            muxer

    Returns:
        list[int]: List of channel numbers that the device is on

    """

    log = logging.getLogger(__name__)

    if channel is None:
        channel = list(range(8))

    if isinstance(channel, (list, tuple)):
        found = []
        for ch in channel:
            found.extend(
                muxer_device_on_channel(muxer, dev_address, ch)
            )
        return found

    # Do not need while loop here because is done within method
    if not muxer[channel].try_lock():
        log.warning("Channel lock failed: %s", channel)
        return []

    addresses = [
        address
        for address in muxer[channel].scan()
        if address != muxer.address
    ]
    muxer[channel].unlock()
    if len(addresses) == 0:
        log.debug("No device(s) found on channel '%s'", channel)
        return []

    if dev_address not in addresses:
        log.debug(
            "Device with address '%s' not found on channel '%s'",
            dev_address,
            channel,
        )
        return []

    log.debug(
        "Found device with address'%s' on channel '%s'",
        dev_address,
        channel,
    )
    return [channel]
