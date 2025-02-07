import logging
from logging.handlers import RotatingFileHandler

import os
from threading import Event, Lock

HOME = os.path.expanduser("~")
APPDIR = os.path.join(HOME, "Freezer_Monitor")
DATADIR = os.path.join(APPDIR, "data")
FIGDIR = os.path.join(APPDIR, "figures")
LOGDIR = os.path.join(APPDIR, "logs")
lfile = os.path.join(LOGDIR, "freezer_monitor.log")
os.makedirs(LOGDIR, exist_ok=True)

SETTINGS_FILE = os.path.join(HOME, f"{__name__}.yml")

lfile = RotatingFileHandler(lfile, maxBytes=2**20, backupCount=5)
lfile.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
    ),
)
lfile.setLevel(logging.WARNING)

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)
LOG.addHandler(lfile)

DEFAULT_INTERVAL = 10.0  # Sensor polling interval
STOP_EVENT = Event()
I2C_LOCK = Lock()
LOCK_TIMEOUT = 1.0
