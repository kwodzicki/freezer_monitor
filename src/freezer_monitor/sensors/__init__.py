import logging
from threading import Lock

from .. import LOCK_TIMEOUT
from ..utils import load_settings, muxer_device_on_channel, RepeatedTimer
from . import sht


class Sensors:
    """
    To hold all sensors and periodically scan

    """

    SCAN_LOCK = Lock()
    AVAILABLE = [sht]
    RESCAN = 10 * 60  # Rescan interval 10 minutes

    def __init__(self, muxer):

        self.log = logging.getLogger(__name__)
        self._sensors = {}

        self.muxer = muxer
        self.scan_for_sensors()
        self._rescanner = RepeatedTimer(
            self.RESCAN,
            self.scan_for_sensors,
        )
        self._rescanner.start()

    def __len__(self):

        with self.SCAN_LOCK:
            return len(self._sensors)

    def __iter__(self):

        if self.SCAN_LOCK:
            return list(self._sensors.values())

    def cycle_thru(self, ncycles):

        with self.SCAN_LOCK:
            for i in range(ncycles):
                for sensor in self._sensors.values():
                    yield sensor

    def scan_for_sensors(self):
        """
        Run scan for sensors

        Using information from settings file, run a scan to look for sensors.
        If a sensor is already in dictionary of sensors and seems to be running
        based on its 'status' property, then we just skip it. Otherwise, we
        scan the muxer to see if can find it.

        """

        self.log.debug("Trying to scan for sensors")

        if not self.SCAN_LOCK.acquire(timeout=LOCK_TIMEOUT):
            self.log.debug("Failed to grab lock for scan!")
            return

        try:
            settings = load_settings()
        except Exception as err:
            self.log.info("Failed to load settings file: %s", err)
            return

        for key, info in settings.items():
            if not key.startswith('sensor.'):
                continue

            name = info.get('name', '???')

            if key in self._sensors and self._sensors[key].status is not None:
                self.log.info(
                    "%s - Sensor seems to be functioning properly",
                    name,
                )
                continue

            _, channel, stype = key.split('.')  # Split information
            channel = int(channel)  # Convert channel number in to int

            sensor = self.get_sensor_obj(stype)  # Try to get the sensor type
            if sensor is None:
                self.log.error(
                    "%s - No matching sensor type found: %s",
                    name,
                    stype,
                )
                continue

            # Try  to find the device on the muxer, will return list of with
            # one (1) element if found
            channel = muxer_device_on_channel(
                self.muxer,
                sensor.ADDRESS,
                channel,
            )
            if len(channel) != 1:
                if key in self._sensors:
                    # If in sensors dict, but not on muxer (and failed status
                    # check above), then we pop off list and ensure thread
                    # is stopped
                    msg = (
                        "%s - Sensor not found on scan. Was it unplugged? "
                        "Type: %s; Address: %d."
                    )
                    self._sensors.pop(key).stop()
                else:
                    # Else, we just didn't find it
                    msg = (
                        "%s - Failed to find sensor of type '%s' on "
                        "address %d!"
                    )
                self.log.warning(
                    msg,
                    name,
                    stype,
                    sensor.ADDRESS,
                )
                continue

            # Initialize sensor, start the thread, and add to sensors dict
            sensor = sensor(
                self.muxer[channel[0]],
                **info,
            )
            sensor.start()
            self._sensors[key] = sensor

        self.SCAN_LOCK.release()

    def join(self):

        with self.SCAN_LOCK:
            self.log.debug("Canceling rescan timer")
            self._rescanner.stop()

            self.log.debug("Joining sensor threads")
            for key in list(self._sensors.keys()):
                self._sensors.pop(key).join()

            self.log.debug("Sensors finished")

    @classmethod
    def get_sensor_obj(cls, stype: str):

        for module in cls.AVAILABLE:
            obj = getattr(module, stype, None)
            if obj is not None:
                return obj
