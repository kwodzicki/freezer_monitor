#!/usr/bin/env python3

import argparse
import signal
from . import STOP_EVENT, lfile
# from .freezer_monitor import DEFAULT_INTERVAL, FreezerMonitor
from . import monitor


def kill(*args, **kwargs):
    print("setting STOP_EVENT")
    STOP_EVENT.set()


def checkMinMax(val, flag):
    if val is None or isinstance(val, (int, float)):
        return val

    if isinstance(val, str):
        if val.title() == "None":
            return None
        try:
            return float(val)
        except Exception:
            print(f"Must input `None' or float value for --{flag}")
            exit()

    raise Exception("Expected str input")


def main():

    parser = argparse.ArgumentParser(
        description="Monitor freezer temperature",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--maxThres",
        type=str,
        default=-10.0,
        help="If temperature exceeds this value, email warning sent",
    )
    parser.add_argument(
        "--minThres",
        type=str,
        default=None,
        help="If temperature goes below this value, email warning sent",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Temperature update interval in seconds",
    )
    parser.add_argument(
        "--loglevel",
        type=int,
        default=30,
        help="Set logging level",
    )
    parser.add_argument(
        "--show-ip",
        action="store_true",
        help="If set, IP address will be displayed on OLED screen",
    )
    parser.add_argument(
        "--no-socket",
        action="store_true",
        help="If set, disable sending data to server on socket",
    )

    args = parser.parse_args()

    maxThres = checkMinMax(args.maxThres, "maxThres")
    minThres = checkMinMax(args.minThres, "minThres")

    signal.signal(signal.SIGINT,  kill)
    signal.signal(signal.SIGTERM, kill)

    lfile.setLevel(args.loglevel)
    _ = monitor.main(
            maxThres=maxThres,
            minThres=minThres,
            interval=args.interval,
            showIP=args.show_ip,
            no_socket=args.no_socket,
    )
