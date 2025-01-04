#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2017 Tony DiCola for Adafruit Industries
# SPDX-FileCopyrightText: 2017 James DeVito for Adafruit Industries
# SPDX-License-Identifier: MIT

# This example is for use on (Linux) computers that are using CPython with
# Adafruit Blinka to support CircuitPython libraries. CircuitPython does
# not support PIL/pillow (python imaging library)!
import logging

import time

from threading import Timer

import numpy
import adafruit_sht31d

from .. import STOP_EVENT, I2C_LOCK
from .basesensor import BaseSensor

# Minimum number of polls to run before NaN check is done
MIN_NUM_POLL = 10


class SHT30(BaseSensor):

    def __init__(
        self,
        i2c_bus,
        name: str,
        **kwargs,
    ):
        """

        Arguments:
            i2c_bus (I2C) : An I2C bus the SHT30 sensor is connected to.
                If using a  multiplexer for multiple sensors, this should be
                the multiplexer channel.
            name (str): Name to use for the sensor
        Keyword arguments:
            See BaseSensor for keywords

        Example:
            This is for use of the TCA9548A multiplexer

                tca = adafruit_tca9548a.TCA9548A(i2c)
                sht30 = SHT30(tca[0], 'Chest Freezer')

        """

        super().__init__(name, **kwargs)

        self.__log = logging.getLogger(__name__)
        self.__log.setLevel(logging.DEBUG)

        self.sensor = adafruit_sht31d.SHT31D(i2c_bus)

        self.websocket = None

        self.nn = 0  # Counter for times polled
        self.t_30min_avg = numpy.full(
            int(30 * 60 / self.interval),
            numpy.nan,
            dtype=numpy.float32,
        )

    def poll(self):
        """Poll the sensor for temperature and humidity"""

        self.nn += 1
        t = rh = numpy.nan
        with I2C_LOCK:  # Get I2C lock for thread safety
            try:  # Try to get information from temperature sensor
                t = self.sensor.temperature
            except Exception as err:
                self.__log.error(
                    "%s - Failed to get temperature from sensor : %s",
                    self.name,
                    err,
                )

            try:  # Try to get information from RH sensor
                rh = self.sensor.relative_humidity
            except Exception as err:
                self.__log.error(
                    "%s - Failed to get relative humidity from sensor : %s",
                    self.name,
                    err,
                )

        return t, rh  # Return the temperature and RH

    def display_text(self) -> list[str]:
        """
        Builds lines to display on screen

        Returns list of strings where each string is a new line to display
        on the screen

        """

        temp, rh = self.poll()
        return [
            self.name,
            f"T : {temp:6.1f} C",
        ]

    def run(self):
        """Run in thread for polling sensor"""

        # Initialize timer thread to run heater and start timer
        self._heater_timer = Timer(0.0, self.run_heater)
        self._heater_timer.start()

        t0 = 0.0
        # Wait for event, delay is computed in function and we want event to
        # be NOT set
        while not STOP_EVENT.wait(self.delay(t0)):
            # Get current monotonic time; used to compute delay until next poll
            t0 = time.monotonic()
            temp, rh = self.poll()  # Get temperature and humidity

            # Write data to the csv file
            self.data_log.write(f"{temp:6.1f}", f"{rh:6.1f}")

            if self.websocket:
                self.websocekt.write(
                    name=self.name,
                    temp=temp,
                    rh=rh,
                )
            # Shift data in rolling averge for new value
            # and add new temperature to rolling average array
            self.t_30min_avg = numpy.roll(self.t_30min_avg, -1)
            self.t_30min_avg[-1] = temp

            # If no max thres is set, then just continue
            if not isinstance(self.max_thres, (int, float)):
                continue

            # Compute average temperature
            avg = numpy.nanmean(self.t_30min_avg)
            # If polled enough times AND average is not finite
            if self.nn > MIN_NUM_POLL and not numpy.isfinite(avg):
                self.allNaN()  # AllNaN email
            elif (
                isinstance(self.max_thres, (int, float))
                and avg > self.max_thres
            ):
                self.overTemp(temp, rh)  # Over temp email
            elif (
                isinstance(self.min_thres, (int, float))
                and avg < self.min_thres
            ):
                self.underTemp(temp, rh)  # Under temp email

        # If heater timer thread exists, cancel it
        if self._heater_timer:
            self._heater_timer.cancel()
        # Ensure the heater is turned off
        self._toggle_heater(False)

        # Close data log and ensure thread is dead
        self.data_log.close()
        self.data_log.join()

        self.__log.debug("Sensor '%s' thread dead!", self.name)
