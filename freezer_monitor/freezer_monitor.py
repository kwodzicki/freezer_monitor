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
from threading import Thread

import numpy

import adafruit_sht31d

from . import DATADIR, DEFAULT_INTERVAL, STOP_EVENT, I2C_LOCK, I2C
from .timerotatingcsv import DailyRotatingCSV
from .display import SSD1306

class FreezerMonitor( Thread ):

  def __init__(self, interval = DEFAULT_INTERVAL, **kwargs):
    super().__init__()

    self.log    = logging.getLogger(__name__)
    self.log.setLevel( logging.DEBUG )

    self._display    = SSD1306()
    self._interval   = interval

    self._sensor     = adafruit_sht31d.SHT31D( I2C )
    self.data        = DailyRotatingCSV( os.path.join(DATADIR, 'freezer_stats.csv') ) 
    self.t_30min_avg = numpy.full( int(30*60/interval), numpy.nan, dtype=numpy.float32 )

    self.t0          = 0.0

  def delay(self):

    dt = self._interval - (time.monotonic()-self.t0)
    if dt < 0.0:
      return 0.0
    return dt

  def poll(self):
    """Poll the sensor for temperature and humidity"""

    t = rh = float('nan')
    with I2C_LOCK:
      try:
        t = self._sensor.temperature
      except Exception as err:
        self.log.error( f'Failed to get temperature from sensor : {err}' )

      try:
        rh = self._sensor.relative_humidity
      except:
        self.log.error( f'Failed to get relative humidity from sensor : {err}' )

    return t, rh

  def start(self):

    self._display.start()
    super().start()

  def run(self):

    while not STOP_EVENT.wait( self.delay() ):                                  # Wait for event, delay is computed in function and we want event to be NOT set
      temp, rh = self.poll()                                                    # Get temperature and humidity
      self.data.write( f"{temp:6.1f}", f"{rh:6.1f}" )                           # Write data to the csv file

      self.t_30min_avg     = numpy.roll( self.t_30min_avg, -1 )                 # Shift data in rolling averge for new value
      self.t_30min_avg[-1] = temp                                               # Add new temperature to rolling average array 

      self._display.temperature       = temp                                    # Update the temperature on display
      self._display.relative_humidity = rh                                      # Update rh on display

      self.t0 = time.monotonic()                                                # Get current monotonic time; used to compute delay until next poll

    self.data.close()                                                           # Close data file
    self.data.join()                                                            # Join data file thread

    self._display.join()                                                        # Join display thread
    self.log.debug( 'Monitor thread dead!' )
