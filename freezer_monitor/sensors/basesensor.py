#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2017 Tony DiCola for Adafruit Industries
# SPDX-FileCopyrightText: 2017 James DeVito for Adafruit Industries
# SPDX-License-Identifier: MIT

# This example is for use on (Linux) computers that are using CPython with
# Adafruit Blinka to support CircuitPython libraries. CircuitPython does
# not support PIL/pillow (python imaging library)!
import logging

import time

from threading import Thread, Lock

import numpy

from . import DATADIR, DEFAULT_INTERVAL, STOP_EVENT, I2C_LOCK, I2C
from .timerotatingcsv import DailyRotatingCSV
from .emailer import EMailer

class BaseSensor( EMailer, Thread ):

    HEATER_LOCK = Lock()
    def __init__(self, name, max_thres=-10, min_thres=None, interval=DEFAULT_INTERVAL, no_socket=False, **kwargs):
        """
        Arguments:
            name (str) : Name assigned to the sensor. This should be a kind of description
                about the senors; e.g., location/what it is monitoring

        Keyword arguments:
            max_thres (int,float) : Temperature threshold (degree C) to trigger warning;
                if temperature exceeds this value, warning sent. Setting this will override
                the min_thres keyword
            min_thres (int,float) : Temperature threshold (degree C) to trigger warning;
                if temperature is below this value, warning sent. This keyword is ignored
                if the max_thres keyword is set. 
            interval (int, float) : Polling interval for sensor (seconds)
            no_socket (bool) : If set, will disable sending data to server socket

        """

        super().__init__()

        self.__log = logging.getLogger(__name__)
        self.__log.setLevel( logging.DEBUG )

        self._heater_timer = None

        self.sensor     = None

        self.name       = name
        self.interval   = interval

        self.min_thres    = min_thres
        self.max_thres    = max_thres
        self.data_log    = DailyRotatingCSV(os.path.join(DATADIR, f"{name}.csv"))

        self.t_30min_avg = numpy.full( int(30*60/interval), numpy.nan, dtype=numpy.float32 )

    def _toggle_heater(self, state):
        """Toggle sensor heater state"""

        with I2C_LOCK:																														# Grab I2C lock for thread safety
            try:																																		# Try to set the heater state
                self.sensor.heater = state
            except Exception as err:																								# On exception, log the a warning
                self.__log.warning( "Failed to toggle heater : %s", err )
            else:																																		# Else, some debug logging
                self.__log.debug( "Heater state set to : %s", state )
 
    def delay(self, t0):
        """
        Compute delay until next poll of sensor

        Arguments:
          t0 (float) : monotonic time of when sensor last polled

        """

        dt = self.interval - (time.monotonic()-t0)																# Compute delay until next poll based on requested interval, current monotonic time, and monotonic time of last poll
        if dt < 0.0:																															# If the delay is less than zero (0.0), then return zero
            return 0.0
        return dt																																	# Return dt

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
            self._toggle_heater( True )
            # Wait for STOP_EVENT; if it happens, just return, else, continue function
            if STOP_EVENT.wait( duration ):
                return
            self._toggle_heater( False )

        # Initialize and start another timer thread for the heater
        self._heater_timer = Timer( interval, self.run_heater )
        self._heater_timer.start()
