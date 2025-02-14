import logging
from threading import Timer, Lock

from .. import LOCK_TIMEOUT
from ..utils import load_settings, muxer_device_on_channel
from ..emailer import EMailer

from . import sht


class Sensors(EMailer):
    """
    To hold all sensors and periodically scan

    """

    SCAN_LOCK = Lock()
    AVAILABLE = [sht]
    RESCAN = 10 * 60  # Rescan interval 10 minutes

    def __init__(self, muxer):
        super().__init__()

        self.log = logging.getLogger(__name__)
        self._rescan = None
        self._sensors = {}

        self.muxer = muxer
        self.scan_for_sensors()

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

        self.log.debug("Initializing new Timer object")
        self._rescan = Timer(self.RESCAN, self.scan_for_sensors)
        self._rescan.start()

        self.log.debug("Trying to scan for sensors")

        if not self.SCAN_LOCK.acquire(timeout=LOCK_TIMEOUT):
            self.log.debug("Failed to grab lock for scan!")
            return

        try:
            settings = load_settings()
        except Exception as err:
            self.log.info("Failed to load settings file: %s", err)
            self.SCAN_LOCK.release()
            self.log.debug('SCAN_LOCK released')
            return

        for key, info in settings.items():
            if not key.startswith('sensor.'):
                continue

            name = info.get('name', '???')

            sensor_obj = self._sensors.pop(key, None)

            # If object is not none, then we will check the stats
            if sensor_obj is not None:
                # If status is NOT None, then assume okay
                if sensor_obj.status is not None:
                    self.log.info("%s - Assuming sensor is okay", name)
                    self._sensors[key] = sensor_obj
                    continue

                # If made here, then statis IS None; i.e., failed to get
                # status, and will try rescan to find device.
                self.log.warning(
                    "%s - Sensor NOT functioning properly. "
                    "Stopping thread and waiting to close...",
                    name,
                )
                sensor_obj.remove()
                sensor_obj.join()

            _, channel, stype = key.split('.')  # Split information
            channel = int(channel)  # Convert channel number in to int

            # Try to get the sensor type
            sensor_cls = self.get_sensor_obj(stype)
            if sensor_cls is None:
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
                sensor_cls.ADDRESS,
                channel,
            )
            if len(channel) != 1:
                msg = (
                    "%s - Failed to find sensor of type '%s' on "
                    "address %d!"
                )
                self.log.warning(
                    msg,
                    name,
                    stype,
                    sensor_cls.ADDRESS,
                )
                continue

            # Initialize sensor, start the thread, and add to sensors dict
            sensor_obj = sensor_cls(
                self.muxer[channel[0]],
                **info,
            )
            sensor_obj.start()
            self._sensors[key] = sensor_obj

        self.log.debug("Finished scanning")

        if len(self._sensors) == 0:
            self.sendMail(
                "No Sensors!!!",
                "No sensors found/connected! Check the system immediately!",
            )
        self.SCAN_LOCK.release()
        self.log.debug('SCAN_LOCK released')

    def join(self):

        with self.SCAN_LOCK:
            self.log.debug("Canceling rescan timer")
            self._rescan.cancel()

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
