#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2017 Tony DiCola for Adafruit Industries
# SPDX-FileCopyrightText: 2017 James DeVito for Adafruit Industries
# SPDX-License-Identifier: MIT

# This example is for use on (Linux) computers that are using CPython with
# Adafruit Blinka to support CircuitPython libraries. CircuitPython does
# not support PIL/pillow (python imaging library)!
import logging

import os
import signal
import time
from datetime import datetime

from subprocess import check_output

from adafruit_tca9548a import TCA9548A

from . import DATADIR, DEFAULT_INTERVAL, STOP_EVENT, I2C_LOCK, I2C
from .display import SSD1306
from .sensors import sht30


def main(**kwargs):

    log = logging.getLogger(__name__)

    # Iniitalize muxer
    log.info("Initializing sensor(s)")
    muxer = TCA9548A(I2C)
    sensors = []
    for channel in muxer_device_on_channel(muxer, 0x44):
        name = kwargs.get("channel{channel}", f"Device{channel}")
        sensor = sht30.SHT30(muxer[channel], name, **kwargs)
        sensor.start()
        sensors.append(sensor)

    log.info("Loading display")
    # Initialize display
    channels = muxer_device_on_channel(muxer, 0x3c)
    if len(channels) != 1:
        log.error("Failed to find display!")
        display = None
    else:
        i2c = muxer[
            channels[0]
        ]
        display = SSD1306(sensors, i2c=i2c)
        display.start()

    # display = SSD1306(sensors)

    log.info("Waiting for stop event")
    # Wait for event, delay is computed in function and we want event to be NOT set 
    _ = STOP_EVENT.wait()

    log.info("Waiting for sensor thread(s) to close")
    for sensor in sensors:
        sensor.join()

    log.info("Waiting for display thread to close")
    if display:
        display.join()  # Join display thread

    log.debug( "Monitor thread dead!" )


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

        channels.append(channel)

    return channels
