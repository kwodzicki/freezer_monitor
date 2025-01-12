import logging

from threading import Thread, Timer, Lock, Event
from subprocess import check_output

from PIL import Image, ImageDraw, ImageFont
from gpiozero import Button

import adafruit_ssd1306

from . import STOP_EVENT, I2C_LOCK, LOCK_TIMEOUT

NCYCLES = 3  # Number of times to cycle through all sensors
TIMEOUT = 60.0  # Timeout (in seconds) for display to turn off


class SSD1306(Thread):
    """
    Thread for updating screen

    """

    IP = ["hostname", "-I"]
    FONT = "DejaVuSansMono"
    BRIGHTNESS = 200
    ADDRESS = 0x3c

    def __init__(
        self,
        i2c,
        sensors: list,
        timeout: int | float | None = None,
        ncycles: int | None = None,
        showIP: bool = False,
    ):
        super().__init__()

        self.__log = logging.getLogger(__name__)
        self.__log.setLevel(logging.DEBUG)

        self._showIP = showIP
        self._displayOn = Event()
        self._lock = Lock()
        self._timer = None

        timeout = timeout or TIMEOUT

        # Get number of sensors and set class attribute
        nsensors = len(sensors)
        self.sensors = sensors

        if nsensors > 0:
            # Default number of cycles, clip to beween 3 and 10
            ncycles = min(
                max(ncycles or NCYCLES, NCYCLES),
                10,
            )

            # Define time to show each sensor's data while cycling
            self.cycle_time = max(
                timeout / nsensors / ncycles,
                1.5,
            )

            # Modify timeout because need to change based on nsensors
            timeout = self.cycle_time * ncycles * nsensors
        else:
            self.cycle_time = timeout

        # Initialize display button object for turn on/off display
        self.buttonThread = DisplayButton(self._displayOn, timeout)

        self._displayThread = None
        # Create the SSD1306 OLED class.
        # The first two parameters are the pixel width and pixel height.
        # Change these to the right size for your display!
        self.width = 128
        self.height = 32
        self.padding = -2
        self._display = adafruit_ssd1306.SSD1306_I2C(
            self.width,
            self.height,
            i2c,
            addr=self.ADDRESS,
        )

        # Create blank image for fdrawing.
        # Make sure to create image with mode "1" for 1-bit color.
        self._image = Image.new(
            "1",
            (self.width, self.height),
        )
        self._draw = ImageDraw.Draw(self._image)
        self._draw.rectangle(
            (0, 0, self.width, self.height),
            outline=0,
            fill=0,
        )

        if self._showIP:
            self.fontIP = ImageFont.truetype(self.FONT, size=7)
            self.font = ImageFont.truetype(self.FONT, size=12)
        else:
            self.fontIP = None
            self.font = ImageFont.truetype(self.FONT, size=15)

    def getIP(self):
        """Get current IP address and return"""

        ip = check_output(self.IP).decode("utf-8")
        ip = ip.split(" ")[0]
        return f"IP : {ip}"

    def update(self, sensor):
        """Update information in the image that is drawn on screen"""

        self.__log.debug("Updating the display: %s", sensor.name)

        txt = sensor.display_text()
        if len(txt) < 2:
            self.__log.error("Too few lines to display!")
            return

        coord = [0, self.padding]  # Upper-left corner to start drawing

        self._draw.rectangle(
            (0, 0, self.width, self.height),
            outline=0,
            fill=0,
        )  # Draw rectangle to clear image; don"t want old text showing
        if self.fontIP:  # If fontIP is set
            self._draw.text(
                coord,
                self.getIP(),
                font=self.fontIP,
                fill=self.BRIGHTNESS,
            )  # Write IP address to screen

            # Update the offset of upper left for next draw
            coord[1] += self.fontIP.size

        self._draw.text(
            coord,
            txt[0],
            font=self.font,
            fill=self.BRIGHTNESS,
        )  # Draw temperature data to image

        # Update the offset of upper left for next draw
        coord[1] += self.font.size

        self._draw.text(
            coord,
            txt[1],
            font=self.font,
            fill=self.BRIGHTNESS,
        )  # Draw relative humidity data to image

    def draw(self):
        """Thread safe way to draw image on screen"""

        # Get I2C lock for thread safety
        if not I2C_LOCK.acquire(timeout=LOCK_TIMEOUT):
            self.__log.warning("Failed to acquire lock, display not updated!")
            return

        try:
            self._display.image(self._image)  # Write image data to screen
            self._display.show()  # Refresh the screen
        except Exception as err:
            self.__log.error("Failed to update display : %s", err)
        I2C_LOCK.release()

    def clear(self):
        """Clear the display; turn off all pixels"""

        self.__log.debug("Clearing display")
        self._draw.rectangle(
            (0, 0, self.width, self.height),
            outline=0,
            fill=0,
        )  # Draw rectangle to clear image
        self.draw()  # Update image on the display

    def start(self):
        """Overload the start method to start button thread as well"""

        self.buttonThread.start()
        super().start()

    def run(self):

        needsClear = False  # Dose the screen need to be cleared
        while not STOP_EVENT.is_set():  # While stop event is NOT set
            idx = 0  # index into self.sensors to display
            # Wait for displayOn event; is false if not set within timeout
            while self._displayOn.wait(timeout=1):
                if len(self.sensors) == 0:
                    continue
                needsClear = True  # Set needsClear to be True
                self.update(self.sensors[idx])  # Update the image on screen
                self.draw()  # Draw to the screen
                idx = (idx + 1) % len(self.sensors)

                # Wait for cycle_time for STOP_EVENT to be set. If set within
                # timeout, then break out of loop
                if STOP_EVENT.wait(timeout=self.cycle_time):
                    break

            if needsClear:  # If screen needs to be cleared
                self.clear()  # clear the screen
                needsClear = False  # Set needsClear to False

        self.clear()  # Ensure display is cleared
        self.__log.debug("Display thread dead!")


class DisplayButton(Thread):

    def __init__(self, event, timeout, pin=17):
        super().__init__()

        self.timer = None
        self.event = event
        self.timeout = timeout
        self.button = Button(pin)

    def run(self):

        while not STOP_EVENT.is_set():
            if not self.button.wait_for_press(timeout=1.0):
                continue

            self.event.set()
            if self.timer:
                self.timer.cancel()
            self.timer = Timer(self.timeout, self.event.clear)
            self.timer.start()

        if self.timer:
            self.timer.cancel()
