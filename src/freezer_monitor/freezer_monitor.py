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
from threading import Thread, Timer

import numpy

import adafruit_sht31d

from . import DATADIR, DEFAULT_INTERVAL, STOP_EVENT, I2C_LOCK, I2C
from .timerotatingcsv import DailyRotatingCSV
from .websocket import WebSocket
from .display import SSD1306
from .emailer import EMailer


class FreezerMonitor( EMailer, Thread ):

    def __init__(
        self,
        maxThres=-10,
        minThres=None,
        interval: int | float = DEFAULT_INTERVAL,
        no_socket: bool = False,
        **kwargs,
    ):
        """
        Keyword arguments:
          maxThres (int,float) : Temperature threshold (degree C) to trigger warning;
            if temperature exceeds this value, warning sent. Setting this will override
            the minThres keyword
          minThres (int,float) : Temperature threshold (degree C) to trigger warning;
            if temperature is below this value, warning sent. This keyword is ignored
            if the maxThres keyword is set. 
          interval (int, float) : Polling interval for sensor (seconds)
          no_socket (bool) : If set, will disable sending data to server socket

        """

        super().__init__()

        self.__log = logging.getLogger(__name__)
        self.__log.setLevel( logging.DEBUG )

        self._heatTimer  = None

        self._display    = SSD1306()
        self._interval   = interval
        self.minThres    = minThres
        self.maxThres    = maxThres
        self._sensor     = adafruit_sht31d.SHT31D( I2C )
        self.data        = DailyRotatingCSV( os.path.join(DATADIR, "freezer_stats.csv") ) 
        self.webSocket   = None if no_socket else WebSocket( **kwargs )																	# Initialize web socket to send data to website front-end
        self.t_30min_avg = numpy.full( int(30*60/interval), numpy.nan, dtype=numpy.float32 )

  def delay(self, t0):
    """
    Compute delay until next poll of sensor

    Arguments:
      t0 (float) : monotonic time of when sensor last polled

    """

    dt = self._interval - (time.monotonic()-t0)																# Compute delay until next poll based on requested interval, current monotonic time, and monotonic time of last poll
    if dt < 0.0:																															# If the delay is less than zero (0.0), then return zero
      return 0.0
    return dt																																	# Return dt

  def _toggleHeater(self, state):
    """Toggle sensor heater state"""

    with I2C_LOCK:																														# Grab I2C lock for thread safety
      try:																																		# Try to set the heater state
        self._sensor.heater = state
      except Exception as err:																								# On exception, log the a warning
        self.__log.warning( f"Failed to toggle heater : {err}" )
      else:																																		# Else, some debug logging
        self.__log.debug( f"Heater state set to : {state}" )

  def runHeater(self, duration = 10.0):
    """
    Run the heater for specified number of seconds

    Keyword arguments:
      duration (float) : Duration (in seconds) to run heater for

    """

    self._toggleHeater( True )                                      					# Turn on the heater

    if STOP_EVENT.wait( duration ):                                 					# Wait for STOP_EVENT; if it happens, just return, else, continue function 
      return

    self._toggleHeater( False )                                     					# Turn off the heater
    self._heatTimer = Timer( 30*60, self.runHeater  )               					# Another timer thread for the heater
    self._heatTimer.start()                                         					# Start timer thread

  def poll(self):
    """Poll the sensor for temperature and humidity"""

    t = rh = float("nan")																											# Set temperature and RH to NaN to start
    with I2C_LOCK:																														# Get I2C lock for thread safety
      try:																																		# Try to get information from temperature sensor
        t = self._sensor.temperature
      except Exception as err:
        self.__log.error( f"Failed to get temperature from sensor : {err}" )

      try:																																		# Try to get information from RH sensor
        rh = self._sensor.relative_humidity
      except Exception as err:
        self.__log.error( f"Failed to get relative humidity from sensor : {err}" )

    return t, rh																															# Return the temperature and RH

  def start(self):
    """Overload start method so that display thread is started as well"""

    self._display.start()
    super().start()

  def run(self):
    """Run in thread for polling sensor"""

    self._heatTimer = Timer( 0.0, self.runHeater )													  # Initialize timer thread to run heater
    self._heatTimer.start()																										# Start the timer thread

    t0 = 0.0
    while not STOP_EVENT.wait( self.delay(t0) ):                              # Wait for event, delay is computed in function and we want event to be NOT set
      t0       = time.monotonic()                                             # Get current monotonic time; used to compute delay until next poll
      temp, rh = self.poll()                                                  # Get temperature and humidity
      self.data.write( f"{temp:6.1f}", f"{rh:6.1f}" )                         # Write data to the csv file
      if self.webSocket is not None:
        self.webSocket.write( temp = temp, rh = rh )						  # Write the data to the websocket

      self.t_30min_avg     = numpy.roll( self.t_30min_avg, -1 )               # Shift data in rolling averge for new value
      self.t_30min_avg[-1] = temp                                             # Add new temperature to rolling average array 

      avg = numpy.nanmean( self.t_30min_avg )                                 # Compute average temperature
      if not numpy.isfinite(avg):                                             # If is not finite
        self.allNaN()                                                         # AllNaN email
      elif isinstance(self.maxThres, (int,float)) and (avg > self.maxThres):
        self.overTemp(temp, rh)                                               # Over temp email
      elif isinstance(self.minThres, (int,float)) and (avg < self.minThres):
        self.underTemp(temp, rh)                                              # Under temp email

      self._display.temperature       = temp                                  # Update the temperature on display
      self._display.relative_humidity = rh                                    # Update rh on display

    if self._heatTimer:                                                       # Cancel heater thread if exists
      self._heatTimer.cancel()
    self._toggleHeater( False )                                               # Ensure the heater is turned off

    self.data.close()                                                         # Close data file
    self.data.join()                                                          # Join data file thread

    self._display.join()                                                      # Join display thread
    self.__log.debug( "Monitor thread dead!" )
