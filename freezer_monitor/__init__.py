import logging
from logging.handlers import  TimedRotatingFileHandler

import os

LOGDIR = os.path.join( os.path.expanduser('~'), 'logs' ) 
lfile  = os.path.join( LOGDIR, 'freezer_monitor.log' )
os.makedirs( LOGDIR, exist_ok=True )

lfile = TimedRotatingFileHandler(lfile, when='D', interval=1, backupCount=30)
lfile.setFormatter( logging.Formatter( '%(asctime)s\t%(message)s' ) )
lfile.setLevel( logging.DEBUG )

LOG    = logging.getLogger(__name__)
LOG.setLevel( logging.DEBUG )
LOG.addHandler(lfile)
