#!/usr/bin/env python3
import os
from datetime import datetime

from . import DATADIR

FMT = '%Y-%m-%d %H:%M:%S.%f'

def parsedata( fpath = None ):
    if fpath is None:
        fpath = os.path.join( DATADIR, 'freezer_monitor.log' )
    date, temp, rh = [], [], []
    with open(fpath, 'r') as fid:
        for line in fid.readlines():
            tmp = line.rstrip().split(',')
            date.append( datetime.strptime(tmp[0], FMT) )
            temp.append( float(tmp[1]) )
            rh.append(   float(tmp[2]) )

    return date, temp, rh

if __name__ == "__main__":
    parselog()
