#!/usr/bin/env python3
import os
from datetime import datetime

from . import LOGDIR

FMT = '%Y-%m-%d %H:%M:%S,%f'

def parselog( file = None ):
    if file is None:
        file = os.path.join( LOGDIR, 'freezer_monitor.log' )
    date, temp, rh = [], [], []
    with open(file, 'r') as fid:
        for line in fid.readlines():
            tmp = line.rstrip().split('\t')
            date.append( datetime.strptime(tmp[0], FMT) )
            temp.append( float(tmp[1]) )
            rh.append(   float(tmp[2]) )

    return date, temp, rh

if __name__ == "__main__":
    parselog()
