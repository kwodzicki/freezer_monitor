#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2017 Tony DiCola for Adafruit Industries
# SPDX-FileCopyrightText: 2017 James DeVito for Adafruit Industries
# SPDX-License-Identifier: MIT

# This example is for use on (Linux) computers that are using CPython with
# Adafruit Blinka to support CircuitPython libraries. CircuitPython does
# not support PIL/pillow (python imaging library)!
import logging
import time

from threading import Timer, Event, Lock

import numpy
import adafruit_sht31d

from .. import STOP_EVENT, I2C_LOCK, LOCK_TIMEOUT
from .basesensor import BaseSensor

# Minimum number of polls to run before NaN check is done
MIN_NUM_POLL = 10

DEFAULT_UNIT = 'degC'

DEG_F = [
    'degree_Fahrenheit',
    'fahrenheit',
    'degF',
    'degreeF',
]

DEG_C = [
    'degree_Celsius',
    'celsius',
    'degC',
    'degreeC',
]


class BaseSHT(BaseSensor):
    """
    Base class for SHT-type sensors

    """

    HEATER_LOCK = Lock()

    def __init__(
        self,
        name: str,
        max_thres: int = -10,
        min_thres: int | None = None,
        units: str | None = None,
        **kwargs,
    ):
        """
        Arguments:
            name (str) : Name assigned to the sensor. This should be a kind
                of description about the senors; e.g., location/what it is
                monitoring

        Keyword arguments:
            max_thres (int,float) : Temperature threshold (degree C) to
                trigger warning; if temperature exceeds this value, warning
                sent. Setting this will override the min_thres keyword
            min_thres (int,float) : Temperature threshold (degree C) to
                trigger warning; if temperature is below this value, warning
                sent. This keyword is ignored if the max_thres keyword is set.
            **kwargs: See BaseSensor

        """

        super().__init__(name, **kwargs)

        self.__log = logging.getLogger(__name__)
        self.__log.setLevel(logging.DEBUG)

        self.sensor = None

        self.units = units or DEFAULT_UNIT
        if self.units not in DEG_C and self.units not in DEG_F:
            self.__log.error(
                "Unsupported unit '%s', defaulting to '%s'",
                self.units,
                DEFAULT_UNIT,
            )
            self.units = DEFAULT_UNIT

        self.__log.debug("Temperature units set to '%s'", self.units)

        self.min_thres = min_thres
        self.max_thres = max_thres

    def _toggle_heater(self, state):
        """Toggle sensor heater state"""

        # Grab I2C lock for thread safety
        if not I2C_LOCK.acquire(timeout=LOCK_TIMEOUT):
            self.__log.warning(
                "%s - Failed to acquire lock, could not toggle heater!",
                self.name,
            )
            return

        try:  # Try to set the heater state
            self.sensor.heater = state
        except Exception as err:  # On exception, log the a warning
            self.__log.warning(
                "%s - Failed to toggle heater : %s",
                self.name,
                err,
            )
        else:  # Else, some debug logging
            self.__log.debug(
                "%s - Heater state set to : %s",
                self.name,
                state,
            )
        I2C_LOCK.release()

    def run_heater(self, duration=10.0, interval=1800.0):
        """
        Run the heater for specified number of seconds

        Keyword arguments:
            duration (int,float) : Duration (in seconds) to run heater for
            interval (int,float) : Interval to wait (in seconds) before next
                call to this method. Default is 30 minutes

        """

        # Grab lock so that multiple heaters don't run at same time
        with self.HEATER_LOCK:
            self._toggle_heater(True)
            _ = STOP_EVENT.wait(duration)
            self._toggle_heater(False)

        # Initialize and start another timer thread for the heater
        if not STOP_EVENT.is_set():
            self._heater_timer = Timer(interval, self.run_heater)
            self._heater_timer.start()

    def poll(self):

        pass


class SHT30(BaseSHT):
    """
    Class for the SHT30 Temperature and Humidity Sensor

    """

    ADDRESS = 0x44

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

        self.__stop = Event()

        self.websocket = None
        self.sensor = adafruit_sht31d.SHT31D(i2c_bus, address=self.ADDRESS)

        self.nn = 0  # Counter for times polled
        self.t_30min_avg = numpy.full(
            int(30 * 60 / self.interval),
            numpy.nan,
            dtype=numpy.float32,
        )

    @property
    def status(self) -> int:
        try:
            return self.sensor.status
        except Exception:
            return None

    def poll(self):
        """Poll the sensor for temperature and humidity"""

        t = rh = numpy.nan
        # Get I2C lock for thread safety
        if not I2C_LOCK.acquire(timeout=LOCK_TIMEOUT):
            self.__log.warning(
                "%s - Failed to acquire lock, could not poll sensor!",
                self.name,
            )
            return t, rh

        self.nn += 1
        try:  # Try to get information from temperature sensor
            t = self.sensor.temperature
        except Exception as err:
            self.__log.error(
                "%s - Failed to get temperature from sensor : %s",
                self.name,
                err,
            )
        else:
            if self.units in DEG_F:
                t = t * 9/5 + 32

        try:  # Try to get information from RH sensor
            rh = self.sensor.relative_humidity
        except Exception as err:
            self.__log.error(
                "%s - Failed to get relative humidity from sensor : %s",
                self.name,
                err,
            )

        I2C_LOCK.release()
        return t, rh  # Return the temperature and RH

    def display_text(self) -> list[str]:
        """
        Builds lines to display on screen

        Returns list of strings where each string is a new line to display
        on the screen

        """

        temp, rh = self.poll()
        unit = 'C' if self.units in DEG_C else 'F'
        return [
            self.name,
            f"T : {temp:6.1f} {unit}",
        ]

    def run(self):
        """Run in thread for polling sensor"""

        # Initialize timer thread to run heater and start timer
        self._heater_timer = Timer(0.0, self.run_heater)
        self._heater_timer.start()

        t0 = 0.0
        # Wait for event, delay is computed in function and we want event to
        # be NOT set
        while not self.__stop.is_set() and not STOP_EVENT.wait(self.delay(t0)):
            # Get current monotonic time; used to compute delay until next poll
            t0 = time.monotonic()
            temp, rh = self.poll()  # Get temperature and humidity

            # Write data to the csv file
            self.data_log.write(
                f"{temp:6.1f} {self.units}",
                f"{rh:6.1f} %",
            )

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

            # If not enough polss, then continue
            if self.nn < MIN_NUM_POLL:
                continue

            # Compute average temperature
            avg = numpy.nanmean(self.t_30min_avg)

            # If polled enough times AND average is not finite
            if not numpy.isfinite(avg):
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

    def stop(self):

        self.__stop.set()
