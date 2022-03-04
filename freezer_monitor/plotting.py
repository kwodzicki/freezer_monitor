import os
import matplotlib.pyplot as plt

from . import FIGDIR
from .parsers import parsedata

def plotDay( dataFile, plotFile = None ):
    if plotFile is None:
      plotFile = os.path.basename( dataFile )
      plotFile = os.path.join( FIGDIR, plotFile + '.png' ) 

    os.makedirs( os.path.dirname( plotFile ), exist_ok = True )

    date, temp, rh = parsedata( dataFile )

    fig, ax1 = plt.subplots()
    color    = 'black'
    ax1.plot( date, temp, color=color )
    ax1.set_ylabel( 'Temperature (C)', color=color )

    ax2   = ax1.twinx()
    color = 'gray'
    ax2.plot(date, rh, color=color)
    ax2.set_ylabel( 'Relative Humidity (%)', color=color )
    ax2.tick_params(axis='y', labelcolor=color)

    fig.suptitle( date[0].strftime( '%Y-%m-%d' ) )
    fig.savefig( plotFile )

