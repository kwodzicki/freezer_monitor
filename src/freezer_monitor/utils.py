import logging
import os

import yaml

from . import SETTINGS_FILE


def load_settings(fpath: str | None = None) -> dict:
    """
    Load in settings YAML file

    Keyword arguments:
        fpath (str, None): Path to file to load, if None set, then will use
            package default value SETTINGS_FILE

    Returns:
        dict: All settings

    """

    log = logging.getLogger(__name__)
    fpath = fpath or SETTINGS_FILE
    if not os.path.isfile(fpath):
        log.warning("Settings file not exist, nothing to load: %s", fpath)
        return {}

    log.info("Loading settings from: %s", fpath)
    with open(fpath, mode="r") as fid:
        return yaml.load(
            fid,
            Loader=yaml.SafeLoader,
        )
