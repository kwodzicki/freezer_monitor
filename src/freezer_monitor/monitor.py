#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2017 Tony DiCola for Adafruit Industries
# SPDX-FileCopyrightText: 2017 James DeVito for Adafruit Industries
# SPDX-License-Identifier: MIT

# This example is for use on (Linux) computers that are using CPython with
# Adafruit Blinka to support CircuitPython libraries. CircuitPython does
# not support PIL/pillow (python imaging library)!
import logging

import busio
from board import SCL, SDA

from adafruit_tca9548a import TCA9548A

from . import STOP_EVENT
from .utils import load_settings
from .display import SSD1306
from .sensors.sht30 import SHT30


def main(**kwargs):

    log = logging.getLogger(__name__)

    i2c = busio.I2C(SCL, SDA)

    settings = load_settings()
    # Iniitalize muxer
    log.info("Initializing sensor(s)")
    muxer = TCA9548A(i2c)
    sensors = []
    for channel in muxer_device_on_channel(muxer, 0x44):
        ch_settings = (
            settings
            .get(f"channel{channel}", {})
        )
        ch_settings['name'] = (
            ch_settings.pop('name', f"Device{channel}")
        )
        sensor = SHT30(muxer[channel], **ch_settings, **kwargs)
        sensor.start()
        sensors.append(sensor)

    log.info("Loading display")
    # Initialize display
    device = None

    # If device found directly on I2C bus, then use that
    # Else we look for it in the muxer
    if i2c_devcie_on_channel(i2c, SSD1306.ADDRESS):
        log.debug("Found display on main I2C bus")
        device = i2c
    else:
        log.debug(
            "No device with address '%s' on main I2C bus",
            SSD1306.ADDRESS,
        )
        channels = muxer_device_on_channel(muxer, SSD1306.ADDRESS)
        if len(channels) != 1:
            log.error(
                "Failed to find (or found multiple) display on mulitplex!",
            )
        else:
            log.debug(
                "Found display on channel '%s' of muxtiplex",
                channels[0],
            )
            device = muxer[channels[0]]

    # If device is set, then initialize display
    if device:
        display = SSD1306(device, sensors)
        display.start()
    else:
        display = None

    log.info("Waiting for stop event")
    # Wait for event, delay is computed in function and we want event
    # to be NOT set
    _ = STOP_EVENT.wait()

    log.info("Waiting for sensor thread(s) to close")
    for sensor in sensors:
        sensor.join()

    log.info("Waiting for display thread to close")
    if display:
        display.join()  # Join display thread

    log.debug("Monitor thread dead!")


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


def muxer_device_on_channel(muxer: TCA9548A, dev_address: int) -> list[int]:
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
    channels = []
    for channel in range(8):
        # Do not need while loop here because is done within method
        if not muxer[channel].try_lock():
            log.warning("Channel lock failed: %s", channel)
            continue

        addresses = [
            address
            for address in muxer[channel].scan()
            if address != muxer.address
        ]
        muxer[channel].unlock()
        if len(addresses) == 0:
            log.debug("No device(s) found on channel '%s'", channel)
            continue

        if dev_address not in addresses:
            log.debug(
                "Device with address '%s' not found on channel '%s'",
                dev_address,
                channel,
            )
            continue
        log.debug(
            "Found device with address'%s' on channel '%s'",
            dev_address,
            channel,
        )
        channels.append(channel)

    return channels
