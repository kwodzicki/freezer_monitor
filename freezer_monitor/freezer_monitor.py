#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2017 Tony DiCola for Adafruit Industries
# SPDX-FileCopyrightText: 2017 James DeVito for Adafruit Industries
# SPDX-License-Identifier: MIT

# This example is for use on (Linux) computers that are using CPython with
# Adafruit Blinka to support CircuitPython libraries. CircuitPython does
# not support PIL/pillow (python imaging library)!
import logging


import signal
import time
from datetime import datetime

from subprocess import check_output
from threading import Thread, Event, Lock

from PIL import Image, ImageDraw, ImageFont
from gpiozero import Button

import busio
from board import SCL, SDA
import adafruit_ssd1306
import adafruit_sht31d

def c2f( temp ):

    return temp * 9.0 / 5.0 + 32.0

class FreezerMonitor( Thread ):

  _KILL = Event()
  IP = ['hostname', '-I']

  def __init__(self, interval = 1.0):
    super().__init__()

    self._interval = interval

    self._lock      = Lock()

    self._displayOn = False
    self._timeout   = 30.0
    self.lastOn     = -self._timeout

    self._button    = Button( 17 )

    # Create the I2C interface.
    self._i2c = busio.I2C(SCL, SDA)

#   Create the SSD1306 OLED class.
#   The first two parameters are the pixel width and pixel height.  Change these
#   to the right size for your display!
    self._display = adafruit_ssd1306.SSD1306_I2C(128, 32, self._i2c)
    self._sensor  = adafruit_sht31d.SHT31D( self._i2c )

    # Create blank image for drawing.
    # Make sure to create image with mode '1' for 1-bit color.
    self.width  = self._display.width
    self.height = self._display.height
    self.image  = Image.new("1", (self.width, self.height))
    self.draw   = ImageDraw.Draw( self.image )
    self.fontIP = ImageFont.truetype('DejaVuSansMono', size =  8)
    self.font   = ImageFont.truetype('DejaVuSansMono', size = 12)

    self.log    = logging.getLogger(__name__)
    self.log.setLevel( logging.DEBUG )

    self.t0         = 0.0
    self.start()

  @property
  def lastOn(self):
    with self._lock:
      return self._lastOn
  @lastOn.setter
  def lastOn(self, val):
    with self._lock:
      self._lastOn = val 

  def clearDisplay(self):

    # Clear display.
    self._display.fill(0)
    self._display.show()
    self._displayOn = False

  def getIP(self):

    ip = check_output( self.IP ).decode("utf-8")
    ip = ip.split(' ')[0]
    return f'IP : {ip}'

  def getT(self):

    return self._sensor.temperature

  def getRH(self):

    return self._sensor.relative_humidity

  def update(self):
    self.t0 = time.monotonic()

    temp = self.getT()
    rh   = self.getRH()

    self.log.info( f"{temp:6.1f}\t{rh:6.1f}" )
    if (self.t0-self.lastOn) > self._timeout:
      if self._displayOn:
        self.clearDisplay()
    else:
      self.updateDisplay( temp, rh )

  def updateDisplay(self, temp, rh):

    if not self._displayOn:
      self.lastOn = time.monotonic()

    # Draw a black filled box to clear the image.
    self.draw.rectangle((0, 0, self.width, self.height), outline=0, fill=0)
    
    # Draw some shapes.
    # First define some constants to allow easy resizing of shapes.
    padding = -2
    top = padding
    bottom = self.height - padding
    # Move left to right keeping track of the current x position for drawing shapes.
    x = 0
    

    # Write four lines of text.
    tempF = c2f( temp )
    temp  = f"T  : {temp:6.1f} C / {tempF:6.1F} F"
    rh    = f"RH : {rh:6.1f}%"

    self.draw.text((x, top), self.getIP(), font=self.fontIP, fill=255)
    top += self.fontIP.size
    self.draw.text((x, top), temp, font=self.font, fill=255)
    top += self.font.size
    self.draw.text((x, top), rh, font=self.font, fill=255)

    # Display image.
    self._display.image( self.image )
    self._display.show()
    self._displayOn = True 
    # Load default font.
    
    # Alternatively load a TTF font.  Make sure the .ttf font file is in the
    # same directory as the python script!
    # Some other nice fonts to try: http://www.dafont.com/bitmap.php
    # font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 9)

  def run(self):
    while not self._KILL.is_set():
      if self._button.wait_for_press(timeout=1.0):
        self.lastOn = time.monotonic()

  def delay(self):

    dt = self._interval - (time.monotonic()-self.t0)
    if dt < 0.0:
      return 0.0
    return dt

  def main(self):

    self.update() 
    while not self._KILL.wait( self.delay() ):
      self.update() 
    self.clearDisplay()

def kill( *args, **kwargs ):

  FreezerMonitor._KILL.set()

#signal.signal( signal.SIGKILL, kill )
signal.signal( signal.SIGINT,  kill )
signal.signal( signal.SIGTERM, kill )


if __name__ == "__main__":

    inst = FreezerMonitor( )
    inst.main()
