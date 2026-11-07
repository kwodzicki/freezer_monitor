import logging
from threading import Timer, Event, Lock

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

    Locking model:

    * SCAN_LOCK guards the `_sensors` dict and NOTHING else. It is only ever
      held for a handful of dict operations (copy, update, clear), never
      across I/O, sleeps, joins or network calls. Anything that may block
      (I2C status reads, mux scans, creating sensors, stopping threads,
      sending mail) runs outside it, so the display and other callers of
      `len()`, `iter()` and `sensor_info()` are never held up by a scan.
    * `_scan_running` serializes whole scans. A scan that finds another one
      already running is skipped (the next rescan picks it up).

    """

    AVAILABLE = [sht]
    RESCAN = 10 * 60  # Rescan interval 10 minutes
    # Max wait in join() for SCAN_LOCK, and again for an in-flight scan
    JOIN_LOCK_TIMEOUT = 15.0
    JOIN_TIMEOUT = 10.0  # Max wait for each sensor thread during join()

    def __init__(self, muxer):
        super().__init__()

        self.log = logging.getLogger(__name__)
        self._rescan = None
        self._sensors = {}
        self._closing = Event()  # Set by join(); scans stop at next check
        self._scan_running = Lock()  # Held for the duration of one scan

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
    def _snapshot(self):
        """Copy of the sensors dict; the ONLY thing held under SCAN_LOCK"""

        return dict(self._sensors)

    def sensor_info(self):
        """
        Display text for every sensor, keyed by sensor name

        SCAN_LOCK is held just long enough to copy the dict of sensors. The
        text itself is built afterwards, outside the lock, from each sensor's
        cached reading (see BaseSensor.latest), so a slow scan can only delay
        this by the time it takes to copy a dict, and no I2C access happens
        here at all.

        Returns None if the scan lock could not be had in time.

        """

        sensors = self._snapshot()
        if sensors is None:
            return None

        return {
            sensor.name: sensor.display_text()
            for sensor in sensors.values()
        }

    def _shutting_down(self) -> bool:

        return self._closing.is_set() or STOP_EVENT.is_set()

    def scan_for_sensors(self):
        """
        Run scan for sensors

        Using information from settings file, run a scan to look for sensors.
        If a sensor is already in dictionary of sensors and seems to be running
        based on its 'status' property, then we just skip it. Otherwise, we
        scan the muxer to see if can find it.

        Only one scan runs at a time; if one is already in progress this
        returns immediately. SCAN_LOCK is NOT held while scanning; see the
        class docstring.

        """

        if not self._scan_running.acquire(blocking=False):
            self.log.info("Scan already in progress; skipping this one")
            return

        try:
            self._scan()
        except Exception:
            self.log.exception("Failed to run 'Sensors.scan_for_sensors'")
        finally:
            self._scan_running.release()

    def _scan(self):
        """One scan: settings -> snapshot -> work (no lock) -> commit"""

        self.log.debug("Trying to scan for sensors")

        try:
            settings = load_settings()
        except Exception as err:
            self.log.info("Failed to load settings file: %s", err)
            return

        current = self._snapshot()
        if current is None:
            self.log.warning("Could not get scan lock; skipping scan")
            return

        # `removed` and `added` are created HERE and filled IN PLACE by
        # _scan_work(), rather than being returned by it. This is on purpose.
        #
        # _scan_work() starts (non-daemon) sensor threads as it goes, and
        # these dicts are the only record of what it has done so far. If
        # _scan_work() raises part way through, a return value would be lost
        # with the exception: sensors that were started would be running with
        # nothing tracking them, nothing able to stop them, and nothing
        # stopping the process from exiting. Since we own the dicts, the
        # `finally` below still commits whatever was done up to the point of
        # failure. The exception then continues up to scan_for_sensors(),
        # which logs it.
        #
        # The invariant that makes this safe (see _scan_work):
        #   removed - ONLY sensors that have already been told to stop
        #   added   - ONLY sensors that have already been started
        removed = {}
        added = {}
        try:
            self._scan_work(settings, current, removed, added)
        finally:
            count = self._commit(removed, added)

        self.log.debug("Finished scanning")

        # If not sensors found, and we are not tearing them down b/c closing
        if count == 0 and not self._shutting_down():
            self.sendMail(
                "No Sensors!!!",
                "No sensors found/connected! Check the system immediately!",
            )

    def _scan_work(self, settings, current, removed, added):
        """
        The slow part of a scan; runs WITHOUT SCAN_LOCK

        `removed` and `added` are OUTPUT arguments: they are updated in place
        (not returned) so that _scan() can still commit them from a `finally`
        if this method raises part way through; see the comment in _scan().
        To keep that safe, each is updated immediately after the action it
        records, never before:

        * a sensor is put in `removed` only AFTER it has been stopped, since
          _commit() forgets everything in it; recording it first and then
          failing before the stop would drop a still-running sensor.
        * a sensor is put in `added` AS SOON AS it has been started, with no
          code in between that could fail and lose track of it.

        Arguments:
            settings (dict): Settings loaded from file
            current (dict): Copy of the sensors dict taken at start of scan
            removed (dict): OUTPUT; filled with sensors that were stopped
            added (dict): OUTPUT; filled with sensors that were started

        """

        wanted = {
            key: info
            for key, info in settings.items()
            if key.startswith('sensor.')
        }

        # Health checks; each reads sensor status, which waits on the I2C bus.
        # Collected in a local dict, NOT `removed`, until they are stopped.
        unhealthy = {}
        for key, info in wanted.items():
            sensor_obj = current.get(key, None)
            if sensor_obj is None:
                continue
            # Shutting down; stop checking. Whatever was already found
            # unhealthy is still stopped below, which is harmless.
            if self._shutting_down():
                break
            if not self._is_healthy(sensor_obj, info.get('name', '???')):
                unhealthy[key] = sensor_obj

        # Stop all the unhealthy ones together (bounded, no lock held). This
        # never raises, so the update right after it cannot be skipped.
        if unhealthy:
            self._stop_sensors(unhealthy.values())
            removed.update(unhealthy)

        # Build whatever is missing or was just removed
        for key, info in wanted.items():
            if key in current and key not in removed:
                continue  # Healthy, nothing to do
            # Shutting down; do not rebuild/start sensors or send alerts
            if self._shutting_down():
                self.log.info("Shutting down; ending scan early")
                return

            sensor_obj = self._build_sensor(key, info)
            if sensor_obj is not None:
                added[key] = sensor_obj  # Record at once; see docstring

    def _is_healthy(self, sensor_obj, name: str) -> bool:
        """Check thread is running AND hardware answers"""

        alive = sensor_obj.is_alive()
        status = sensor_obj.status

        # Healthy if thread running AND status read worked. If could not get
        # the bus lock (BUSY), we do not know anything; assume okay rather
        # than tear down a working sensor.
        if alive:
            if status is BUSY:
                self.log.info("%s - Busy, assuming okay", name)
                return True
            if status is not None:
                self.log.info("%s - Assuming sensor is okay", name)
                return True

        # If made here, then thread died OR status read failed
        self.log.warning(
            "%s - Sensor NOT functioning properly (thread alive: %s; "
            "status: %s). Stopping thread and waiting to close...",
            name,
            alive,
            "BUSY" if status is BUSY else status,
        )
        return False

    def _build_sensor(self, key: str, info: dict):
        """
        Find sensor on the muxer, create it and start its thread

        Returns the started sensor, or None if it could not be built. A
        failure here is contained to this one sensor so that the others
        in the settings still get their turn.

        """

        name = info.get('name', '???')
        sensor_obj = None
        try:
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
                return None

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
                return None

            # Initialize sensor and start the thread
            sensor_obj = sensor_cls(
                self.muxer[channel[0]],
                **info,
            )
            sensor_obj.start()
            return sensor_obj
        except Exception:
            self.log.exception("%s - Failed to build sensor '%s'", name, key)
            # Constructed but never started (e.g., could not start thread):
            # nobody will ever call run() to close its data log thread, which
            # is not a daemon, so close it here or it keeps the process alive
            if sensor_obj is not None:
                try:
                    sensor_obj.data_log.close()
                except Exception:
                    self.log.exception("%s - Failed to close data log", name)
            return None

    def _commit(self, removed: dict, added: dict) -> int:
        """
        Publish the results of a scan; the only write to _sensors in a scan

        Called from a `finally` in _scan(), so it runs even when the scan
        failed part way; `removed` and `added` then hold only what was
        actually done (see _scan_work()).

        If shutdown began while the scan was working, the sensors it built
        are stopped instead of being added: join() has already emptied the
        dict and will not come back for them.

        Returns:
            int: Number of sensors in the dict after the commit

        """

        with SCAN_LOCK:
            closing = self._closing.is_set()
            for key, sensor_obj in removed.items():
                # Only drop it if it is still the one we looked at
                if self._sensors.get(key) is sensor_obj:
                    del self._sensors[key]
            if not closing:
                self._sensors.update(added)
            count = len(self._sensors)

        if closing and added:
            self.log.info(
                "Shutting down; stopping %d new sensor(s)",
                len(added),
            )
            self._stop_sensors(added.values())

        return count

    def _stop_sensors(self, sensors):
        """
        Signal sensors to stop, then wait for each, bounded

        All are signalled first so they stop in parallel. Never raises, and
        never waits longer than JOIN_TIMEOUT for any one thread. Must NOT be
        called with SCAN_LOCK held.

        """

        sensors = list(sensors)

        for sensor in sensors:
            try:
                sensor.remove()
            except Exception:
                self.log.exception("Error removing %s", sensor.name)

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

    def join(self):
        """
        Stop rescanning and stop/join all sensor threads

        Deliberately NOT wrapped in @locker: that decorator skips the whole
        call if the lock is not free within LOCK_TIMEOUT, which would leave
        the rescan timer and sensors untouched at shutdown.

        Order matters here:

        1. Flag shutdown, so any scan stops at its next check and, if it
           has already built sensors, stops them itself when it commits.
        2. Take SCAN_LOCK only to grab and clear the dict of sensors. If it
           cannot be had within JOIN_LOCK_TIMEOUT we carry on anyway; sensor
           threads exit on their own once STOP_EVENT is set.
        3. Stop those sensors, with no lock held.
        4. Give an in-flight scan (up to JOIN_LOCK_TIMEOUT) a chance to
           finish cleaning up after itself.

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
        finally:
            if locked:
                SCAN_LOCK.release()

        self._stop_sensors(sensors)

        if self._scan_running.acquire(timeout=self.JOIN_LOCK_TIMEOUT):
            self._scan_running.release()
        else:
            self.log.error(
                "A scan was still running after %.1f s",
                self.JOIN_LOCK_TIMEOUT,
            )

        self.log.debug("Sensors finished")

    @classmethod
    def get_sensor_obj(cls, stype: str):

        for module in cls.AVAILABLE:
            obj = getattr(module, stype, None)
            if obj is not None:
                return obj
