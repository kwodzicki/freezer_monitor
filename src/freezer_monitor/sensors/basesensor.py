#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2017 Tony DiCola for Adafruit Industries
# SPDX-FileCopyrightText: 2017 James DeVito for Adafruit Industries
# SPDX-License-Identifier: MIT

# This example is for use on (Linux) computers that are using CPython with
# Adafruit Blinka to support CircuitPython libraries. CircuitPython does
# not support PIL/pillow (python imaging library)!
import logging
import os
import time

from threading import Thread
from typing import NamedTuple

from .. import DATADIR, DEFAULT_INTERVAL
from ..timerotatingcsv import DailyRotatingCSV
from . import EMailer


class Reading(NamedTuple):
    """
    One immutable sensor reading

    Immutability is what makes the cache lock-free: the polling thread
    publishes a whole new Reading with a single attribute assignment (atomic
    in CPython), and other threads either see the old one or the new one,
    never a half-written mix of temperature from one poll and humidity from
    another.

    """

    temp: float  # In the sensor's configured units
    rh: float  # Percent
    taken: float  # time.monotonic() when the reading was made


class BaseSensor(EMailer, Thread):

    # A reading older than STALE_FACTOR * interval is considered stale
    STALE_FACTOR = 3.0

    def __init__(
        self,
        name: str,
        interval: int | float = DEFAULT_INTERVAL,
        no_socket: bool = False,
        **kwargs,
    ):
        """
        Arguments:
            name (str) : Name assigned to the sensor. This should be a kind
                of description about the senors; e.g., location/what it is
                monitoring

        Keyword arguments:
            max_thres (int,float) : Temperature threshold (degree C) to
                trigger warning; if temperature exceeds this value, warning
                sent. Setting this will override the min_thres keyword
            min_thres (int,float) : Temperature threshold (degree C) to
                trigger warning; if temperature is below this value, warning
                sent. This keyword is ignored if the max_thres keyword is set.
            interval (int, float) : Polling interval for sensor (seconds)
            no_socket (bool) : If set, disable sending data to server socket

        """

        super().__init__()

        self.__log = logging.getLogger(__name__)
        self.__log.setLevel(logging.DEBUG)

        self._heater_timer = None

        self.sensor = None
        self._reading = None  # Latest Reading; written ONLY by polling thread

        self.name = name
        self.interval = interval

        self.data_log = DailyRotatingCSV(
            os.path.join(DATADIR, f"{name}.csv"),
        )

    def delay(self, t0):
        """
        Compute delay until next poll of sensor

        Arguments:
          t0 (float) : monotonic time of when sensor last polled

        """

        # Compute delay until next poll based on requested interval, current
        # monotonic time, and monotonic time of last poll
        dt = self.interval - (time.monotonic()-t0)
        if dt < 0.0:  # If the delay is less than zero (0.0), then return zero
            return 0.0
        return dt  # Return dt

    @property
    def latest(self) -> Reading | None:
        """
        Most recent reading made by the polling thread, or None

        Never touches the bus and never takes a lock, so it is safe (and
        cheap) to call from any thread, e.g., the display.

        """

        return self._reading

    def _publish(self, temp: float, rh: float):
        """Publish a new reading; only the polling thread should call"""

        self._reading = Reading(temp, rh, time.monotonic())

    def is_stale(self, reading: Reading | None) -> bool:
        """
        True if there is no reading, or it is too old to trust

        Takes the reading explicitly (rather than looking at self._reading)
        so a caller judges the very same snapshot it goes on to use, even if
        the polling thread publishes a new one in between.

        """

        if reading is None:
            return True
        age = time.monotonic() - reading.taken
        return age > self.STALE_FACTOR * self.interval

    def display_text(self) -> list[str]:
        """
        Return lines to write to display

        """

        pass
