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

from . import STOP_EVENT, I2C_LOCK
from .basesensor import BaseSensor

class SHT30( BaseSensor):

    def __init__(self, i2c_bus, name, **kwargs):
        """

        Arguments:
            i2c_bus (I2C) : An I2C bus the SHT30 sensor is connected to. If using a
                multiplexer for multiple sensors, this should be the multiplexer
                channel.

        Keyword arguments:
            See BaseSensor for keywords

        Example:
            This is for use of the TCA9548A multiplexer

                tca = adafruit_tca9548a.TCA9548A(i2c)
                sht30 = SHT30(tca[0], 'Chest Freezer')

        """

        super().__init__(name, **kwargs)

        self.__log = logging.getLogger(__name__)
        self.__log.setLevel( logging.DEBUG )

        self._heater_timer = None
        self.sensor        = adafruit_sht31d.SHT31D( i2c_bus )

    def poll(self):
        """Poll the sensor for temperature and humidity"""

        t = rh = numpy.nan
        with I2C_LOCK:																														# Get I2C lock for thread safety
            try:																																		# Try to get information from temperature sensor
                t = self.sensor.temperature
            except Exception as err:
                self.__log.error(
                    "Failed to get temperature from sensor : %s",
                    err,
                )

            try:																																		# Try to get information from RH sensor
                rh = self.sensor.relative_humidity
            except Exception as err:
                self.__log.error(
                    "Failed to get relative humidity from sensor : %s",
                    err,
                )

        return t, rh																															# Return the temperature and RH

    def run(self):
        """Run in thread for polling sensor"""

        self._heater_timer = Timer( 0.0, self.run_heater )													  # Initialize timer thread to run heater
        self._heater_timer.start()																										# Start the timer thread

        t0 = 0.0
        while not STOP_EVENT.wait( self.delay(t0) ):                              # Wait for event, delay is computed in function and we want event to be NOT set
            t0       = time.monotonic()# Get current monotonic time; used to compute delay until next poll
            temp, rh = self.poll()     # Get temperature and humidity

            self.data_log.write( f"{temp:6.1f}", f"{rh:6.1f}" )                         # Write data to the csv file

            self.t_30min_avg     = numpy.roll( self.t_30min_avg, -1 )               # Shift data in rolling averge for new value
            self.t_30min_avg[-1] = temp                                             # Add new temperature to rolling average array 

            avg = numpy.nanmean( self.t_30min_avg )                                 # Compute average temperature
            if not numpy.isfinite(avg):                                             # If is not finite
                self.allNaN()                                                         # AllNaN email
            elif isinstance(self.max_thres, (int,float)) and (avg > self.max_thres):
                self.overTemp(temp, rh)                                               # Over temp email
            elif isinstance(self.min_thres, (int,float)) and (avg < self.min_thres):
                self.underTemp(temp, rh)                                              # Under temp email

        # If heater timer thread exists, cancel it
        if self._heater_timer: 
            self._heater_timer.cancel()
        # Ensure the heater is turned off
        self._toggle_heater( False )

        # Close data log and ensure thread is dead
        self.data_log.close()
        self.data_log.join()

        self.__log.debug( "Sensor '%s' thread dead!", self.name )
