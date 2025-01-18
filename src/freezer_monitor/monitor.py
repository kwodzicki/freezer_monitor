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
from . import utils
from .display import SSD1306
from .sensors import Sensors


def main(**kwargs):

    log = logging.getLogger(__name__)

    i2c = busio.I2C(SCL, SDA)

    # Iniitalize muxer
    log.info("Initializing sensor(s)")
    muxer = TCA9548A(i2c)
    sensors = Sensors(muxer)
    log.info("Found %d sensors", len(sensors))

    log.info("Loading display")
    # Initialize display
    device = None

    # If device found directly on I2C bus, then use that
    # Else we look for it in the muxer
    if utils.i2c_devcie_on_channel(i2c, SSD1306.ADDRESS):
        log.debug("Found display on main I2C bus")
        device = i2c
    else:
        log.debug(
            "No device with address '%s' on main I2C bus",
            SSD1306.ADDRESS,
        )
        channels = utils.muxer_device_on_channel(muxer, SSD1306.ADDRESS)
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
    sensors.join()

    log.info("Waiting for display thread to close")
    if display:
        display.join()  # Join display thread

    log.debug("Monitor thread dead!")
