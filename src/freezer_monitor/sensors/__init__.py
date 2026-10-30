import logging
from threading import Timer

from .. import STOP_EVENT, SCAN_LOCK, BUSY
from ..utils import (
    load_settings,
    muxer_device_on_channel,
)
from ..thread_utils import locker

from ..emailer import EMailer

from . import sht


class Sensors(EMailer):
    """
    To hold all sensors and periodically scan

    """

    AVAILABLE = [sht]
    RESCAN = 10 * 60  # Rescan interval 10 minutes

    def __init__(self, muxer):
        super().__init__()

        self.log = logging.getLogger(__name__)
        self._rescan = None
        self._sensors = {}

        self.muxer = muxer
        self._scan_and_rearm()

    @locker(SCAN_LOCK)
    def __len__(self):

        return len(self._sensors)

    @locker(SCAN_LOCK)
    def __iter__(self):

        return list(self._sensors.values())

    def _scan_and_rearm(self):
        if STOP_EVENT.is_set():             # don't schedule after shutdown
            return
        self._rescan = Timer(self.RESCAN, self._scan_and_rearm)
        self._rescan.daemon = True          # won't keep the process alive
        self._rescan.start()
        self.scan_for_sensors()             # may time out; the chain survives

    @locker(SCAN_LOCK)
    def sensor_info(self):

        return {
            sensor.name: sensor.display_text()
            for sensor in self._sensors.values()
        }

    @locker(SCAN_LOCK)
    def scan_for_sensors(self):
        """
        Run scan for sensors

        Using information from settings file, run a scan to look for sensors.
        If a sensor is already in dictionary of sensors and seems to be running
        based on its 'status' property, then we just skip it. Otherwise, we
        scan the muxer to see if can find it.

        """

        self.log.debug("Trying to scan for sensors")

        try:
            settings = load_settings()
        except Exception as err:
            self.log.info("Failed to load settings file: %s", err)
            return

        for key, info in settings.items():
            if not key.startswith('sensor.'):
                continue

            name = info.get('name', '???')

            sensor_obj = self._sensors.get(key, None)

            # If object is not none, then we will check the health
            if sensor_obj is not None:
                alive = sensor_obj.is_alive()
                status = sensor_obj.status

                # Healthy if thread running AND status read worked. If could
                # not get the bus lock (BUSY), we do not know anything; assume
                # okay rather than tear down a working sensor.
                if alive:
                    if status is BUSY:
                        self.log.info("%s - Busy, assuming okay", name)
                        continue
                    if status is not None:
                        self.log.info("%s - Assuming sensor is okay", name)
                        continue

                # If made here, then thread died OR status read failed
                self.log.warning(
                    "%s - Sensor NOT functioning properly (thread alive: %s; "
                    "status: %s). Stopping thread and waiting to close...",
                    name,
                    alive,
                    "BUSY" if status is BUSY else status,
                )
                sensor_obj.remove()
                sensor_obj.join()
                del self._sensors[key]

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

    @locker(SCAN_LOCK)
    def join(self):

        self.log.debug("Canceling rescan timer")
        self._rescan.cancel()

        self.log.debug("Joining sensor threads")
        for key in list(self._sensors.keys()):
            sensor = self._sensors.pop(key)
            sensor.remove()
            sensor.join()

        self.log.debug("Sensors finished")

    @classmethod
    def get_sensor_obj(cls, stype: str):

        for module in cls.AVAILABLE:
            obj = getattr(module, stype, None)
            if obj is not None:
                return obj
