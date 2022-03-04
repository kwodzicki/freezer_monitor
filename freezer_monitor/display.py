import logging
import time

from threading import Thread, Timer, Lock, Event
from subprocess import check_output

from PIL import Image, ImageDraw, ImageFont
from gpiozero import Button

import adafruit_ssd1306

from . import STOP_EVENT, I2C_LOCK, I2C 

class DisplayButton( Thread ):

  def __init__(self, event, timeout, pin = 17):
    super().__init__()

    self.timer   = None
    self.event   = event
    self.timeout = timeout
    self.button  = Button( pin )

  def run(self):

    while not STOP_EVENT.is_set():
      if self.button.wait_for_press(timeout=1.0):
        self.event.set()
        if self.timer: 
          self.timer.cancel()
        self.timer = Timer( self.timeout, self.event.clear )
        self.timer.start()
        time.sleep(0.2)                                                             # Quick sleep to prevent multiple very-fast clicks

    if self.timer:
      self.timer.cancel()

class SSD1306( Thread ):

  IP         = ['hostname', '-I']
  FONT       = 'DejaVuSansMono'
  BRIGHTNESS = 200

  def __init__(self, timeout = 30.0, showIP = False):
    super().__init__()

    self.log    = logging.getLogger(__name__)
    self.log.setLevel( logging.DEBUG )

    self._showIP            = showIP
    self._displayOn         = Event()
    self._lock              = Lock()
    self._timer             = None

    self.buttonThread       = DisplayButton( self._displayOn, timeout ) 

    self._temperature       = float('nan')
    self._relative_humidity = float('nan')

    self._displayThread     = None
    # Create the SSD1306 OLED class.
    # The first two parameters are the pixel width and pixel height.  Change these
    # to the right size for your display!
    self.width    = 128
    self.height   =  32
    self.padding  =  -2
    self._display = adafruit_ssd1306.SSD1306_I2C( self.width, self.height, I2C )

    # Create blank image for fdrawing.
    # Make sure to create image with mode '1' for 1-bit color.
    self._image  = Image.new("1", (self.width, self.height))
    self._draw   = ImageDraw.Draw( self._image )
    self._draw.rectangle( (0, 0, self.width, self.height), outline=0, fill=0)
      
    if self._showIP:
      self.fontIP = ImageFont.truetype(self.FONT, size =  7)
      self.font   = ImageFont.truetype(self.FONT, size = 12)
    else:
      self.fontIP = None
      self.font   = ImageFont.truetype(self.FONT, size = 15)

  # Ensure thread-safe updating of temperature
  @property
  def temperature(self):
    with self._lock:
      return self._temperature
  @temperature.setter
  def temperature(self, val):
    with self._lock:
      self._temperature = val

  # Ensure thread-safe updating of relative humidity
  @property
  def relative_humidity(self):
    with self._lock:
      return self._relative_humidity
  @relative_humidity.setter
  def relative_humidity(self, val):
    with self._lock:
      self._relative_humidity = val

  def getIP(self):
    """Get current IP address and return"""

    ip = check_output( self.IP ).decode("utf-8")
    ip = ip.split(' ')[0]
    return f'IP : {ip}'

  def update(self):
    """Update information in the image that is drawn on screen"""

    coord = [0, self.padding]                                                   # Upper-left corner to start drawing
    temp  = f"T  : {self.temperature:6.1f} C"                                   # Temperature string
    rh    = f"RH : {self.relative_humidity:6.1f} %"                             # RH string

    self._draw.rectangle((0, 0, self.width, self.height), outline=0, fill=0)    # Draw rectangle to clear image; don't want old text showing
    if self.fontIP:                                                             # If fontIP is set
      self._draw.text( coord, self.getIP(), font=self.fontIP, fill=self.BRIGHTNESS) # Write IP address to screen
      coord[1] += self.fontIP.size                                              # Update the offset of upper left for next draw

    self._draw.text( coord, temp, font=self.font, fill=self.BRIGHTNESS)          # Draw temperature data to image
    coord[1] += self.font.size                                                  # Update the offset of upper left for next draw

    self._draw.text( coord, rh,   font=self.font, fill=self.BRIGHTNESS)          # Draw relative humidity data to image

  def draw(self):
    """Thread safe way to draw image on screen"""

    with I2C_LOCK:                                                              # Get I2C lock for thread safety
      try:
        self._display.image( self._image )                                      # Write image data to screen
        self._display.show()                                                    # Refresh the screen
      except Exception as err:
        self.log.error( f'Failed to update display : {err}' )

  def clear(self):
    """Clear the display; turn off all pixels"""

    self.log.debug('Clearing display')
    self._draw.rectangle((0, 0, self.width, self.height), outline=0, fill=0)    # Draw rectangle to clear image
    self.draw()                                                                 # Update image on the display

  def start(self):
    """Overload the start method to start button thread as well"""

    self.buttonThread.start()
    super().start()

  def run(self):

    needsClear = False                                                          # Dose the screen need to be cleared
    while not STOP_EVENT.is_set():                                              # While stop event is NOT set
      while self._displayOn.wait(timeout=1.0):                                  # Wait for displayOn event; is false if not set within timeout
        needsClear = True                                                       # Set needsClear to be True
        self.update()                                                           # Update the image to draw to screen
        self.draw()                                                             # Draw to the screen
        time.sleep(1.0)                                                         # Sleep for one (1) second; don't need screen to update very often

      if needsClear:                                                            # If screen needs to be cleared
        self.clear()                                                            # clear the screen
        needsClear = False                                                      # Set needsClear to False


    self.clear()                                                                # Ensure display is cleared
    self.log.debug( 'Display thread dead!' )
