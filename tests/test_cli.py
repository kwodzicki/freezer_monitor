"""Tests for the signal handler in freezer_monitor.cli"""

import importlib
import sys
from unittest import mock

import pytest

# Hardware / display libraries imported (indirectly) by freezer_monitor.cli
HARDWARE_MODULES = [
    "busio",
    "board",
    "gpiozero",
    "adafruit_ssd1306",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
]
IMPORTED_BY_CLI = [
    "freezer_monitor.cli",
    "freezer_monitor.monitor",
    "freezer_monitor.display",
]


@pytest.fixture
def cli(monkeypatch):
    """freezer_monitor.cli imported with the hardware libraries stubbed out"""

    for name in HARDWARE_MODULES:
        monkeypatch.setitem(sys.modules, name, mock.MagicMock())
    for name in IMPORTED_BY_CLI:
        monkeypatch.delitem(sys.modules, name, raising=False)

    module = importlib.import_module("freezer_monitor.cli")
    yield module

    # monkeypatch restores sys.modules; make sure nothing built on the stubs
    # survives into other tests
    for name in IMPORTED_BY_CLI:
        sys.modules.pop(name, None)


def test_kill_sets_stop_event(cli):
    import freezer_monitor as fm

    assert not fm.STOP_EVENT.is_set()
    cli.kill()
    assert fm.STOP_EVENT.is_set()


def test_kill_ignores_repeat_signals(cli, monkeypatch):
    """
    A second SIGINT/SIGTERM must not touch STOP_EVENT's locks again: signal
    handlers run in the main thread, which may already hold one of them
    while shutting down.

    """

    spy = mock.Mock(wraps=cli.STOP_EVENT)
    monkeypatch.setattr(cli, "STOP_EVENT", spy)

    cli.kill()
    cli.kill()
    cli.kill()

    assert spy.set.call_count == 1


def test_kill_accepts_signal_handler_arguments(cli):
    cli.kill(2, None)  # signal.signal handlers are called as (signum, frame)
