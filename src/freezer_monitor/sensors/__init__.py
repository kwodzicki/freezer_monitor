import logging
from threading import Timer, Event

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
    JOIN_LOCK_TIMEOUT = 15.0  # Max wait for an in-flight scan during join()
    JOIN_TIMEOUT = 10.0  # Max wait for each sensor thread during join()

    def __init__(self, muxer):
        super().__init__()

        self.log = logging.getLogger(__name__)
        self._rescan = None
        self._sensors = {}
        self._closing = Event()  # Set by join(); scans stop at next check

        self.muxer = muxer
        self._scan_and_rearm()

    @locker(SCAN_LOCK)
    def __len__(self):

        return len(self._sensors)

    @locker(SCAN_LOCK)
    def __iter__(self):

        return list(self._sensors.values())

    def _scan_and_rearm(self):
        if STOP_EVENT.is_set() or self._closing.is_set():
            return                          # don't schedule after shutdown
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

            # Shutting down; do not rebuild/start sensors or send alerts
            if self._closing.is_set() or STOP_EVENT.is_set():
                self.log.info("Shutting down; ending scan early")
                return

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

        # If not sensors found, and we are not tearing them down b/c closing
        if len(self._sensors) == 0 and not self._closing.is_set():
            self.sendMail(
                "No Sensors!!!",
                "No sensors found/connected! Check the system immediately!",
            )

    def join(self):
        """
        Stop rescanning and stop/join all sensor threads

        Deliberately NOT wrapped in @locker: that decorator skips the whole
        call if the lock is not free within LOCK_TIMEOUT, which would leave
        the rescan timer and sensors untouched at shutdown. Here we flag
        shutdown so any scan stops at its next check, then wait (up to
        JOIN_LOCK_TIMEOUT) for an in-flight scan to finish. If the lock still
        cannot be had we carry on anyway; sensor threads exit on their own
        once STOP_EVENT is set, and nothing here needs the lock to be correct
        for that.

        """

        self._closing.set()

        self.log.debug("Canceling rescan timer")
        if self._rescan is not None:
            self._rescan.cancel()

        locked = SCAN_LOCK.acquire(timeout=self.JOIN_LOCK_TIMEOUT)
        if not locked:
            self.log.error(
                "Could not get scan lock in %.1f s; stopping sensors anyway",
                self.JOIN_LOCK_TIMEOUT,
            )

        try:
            sensors = list(self._sensors.values())
            self._sensors.clear()

            # Signal every sensor first so they all stop in parallel
            for sensor in sensors:
                sensor.remove()

            self.log.debug("Joining sensor threads")
            for sensor in sensors:
                try:
                    sensor.join(timeout=self.JOIN_TIMEOUT)
                except Exception:
                    self.log.exception("Error joining %s", sensor.name)
                    continue
                if sensor.is_alive():
                    self.log.error(
                        "%s - thread still running after %.1f s",
                        sensor.name,
                        self.JOIN_TIMEOUT,
                    )
        finally:
            if locked:
                SCAN_LOCK.release()

        self.log.debug("Sensors finished")

    @classmethod
    def get_sensor_obj(cls, stype: str):

        for module in cls.AVAILABLE:
            obj = getattr(module, stype, None)
            if obj is not None:
                return obj
