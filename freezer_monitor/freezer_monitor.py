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
from .display import SSD1306
from .emailer import EMailer

class FreezerMonitor( EMailer, Thread ):

  def __init__(self, thres = -10, interval = DEFAULT_INTERVAL, **kwargs):
    super().__init__()

    self.__log = logging.getLogger(__name__)
    self.__log.setLevel( logging.DEBUG )

    self._heatTimer  = None

    self._display    = SSD1306()
    self._interval   = interval
    self.thres       = thres
    self._sensor     = adafruit_sht31d.SHT31D( I2C )
    self.data        = DailyRotatingCSV( os.path.join(DATADIR, 'freezer_stats.csv') ) 
    self.t_30min_avg = numpy.full( int(30*60/interval), numpy.nan, dtype=numpy.float32 )

  def delay(self, t0):

    dt = self._interval - (time.monotonic()-t0)
    if dt < 0.0:
      return 0.0
    return dt

  def _toggleHeater(self, state):

    with I2C_LOCK:
      try:
        self._sensor.heater = state
      except Exception as err:
        self.__log.warning( f"Failed to toggle heater : {err}" )
      else:
        self.__log.debug( f'Heater state set to : {state}' )

  def runHeater(self, duration = 10.0):

    self._toggleHeater( True )                                      # Turn on the heater

    if STOP_EVENT.wait( duration ):                                 # Wait for STOP_EVENT; if it happens, just return, else, continue function 
      return

    self._toggleHeater( False )                                     # Turn off the heater
    self._heatTimer = Timer( 30*60, self.runHeater  )               # Another timer thread for the heater
    self._heatTimer.start()                                         # Start timer thread

  def poll(self):
    """Poll the sensor for temperature and humidity"""

    t = rh = float('nan')
    with I2C_LOCK:
      try:
        t = self._sensor.temperature
      except Exception as err:
        self.__log.error( f'Failed to get temperature from sensor : {err}' )

      try:
        rh = self._sensor.relative_humidity
      except Exception as err:
        self.__log.error( f'Failed to get relative humidity from sensor : {err}' )

    return t, rh

  def start(self):
    """Overload start method so that display thread is started as well"""

    self._display.start()
    super().start()

  def run(self):
    """Run in thread for polling sensor"""

    self._heatTimer = Timer( 0.0, self.runHeater )
    self._heatTimer.start()

    t0 = 0.0
    while not STOP_EVENT.wait( self.delay(t0) ):                                # Wait for event, delay is computed in function and we want event to be NOT set
      temp, rh = self.poll()                                                    # Get temperature and humidity
      self.data.write( f"{temp:6.1f}", f"{rh:6.1f}" )                           # Write data to the csv file

      self.t_30min_avg     = numpy.roll( self.t_30min_avg, -1 )                 # Shift data in rolling averge for new value
      self.t_30min_avg[-1] = temp                                               # Add new temperature to rolling average array 

      avg = numpy.nanmean( self.t_30min_avg )                                   # Compute average temperature
      if not numpy.isfinite(avg):                                               # If is not finite
        self.allNaN()                                                           # AllNaN email
      elif avg > self.thres:                                                    # If greater than threshold
        self.overTemp(temp, rh)                                                 # Over temp email

      self._display.temperature       = temp                                    # Update the temperature on display
      self._display.relative_humidity = rh                                      # Update rh on display

      t0 = time.monotonic()                                                     # Get current monotonic time; used to compute delay until next poll

    if self._heatTimer:                                                         # Cancel heater thread if exists
      self._heatTimer.cancel()
    self._toggleHeater( False )                                                 # Ensure the heater is turned off

    self.data.close()                                                           # Close data file
    self.data.join()                                                            # Join data file thread

    self._display.join()                                                        # Join display thread
    self.__log.debug( 'Monitor thread dead!' )
