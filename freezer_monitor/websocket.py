import logging

from datetime import datetime
import socket
import json
from threading import Thread, Lock, Event

try:
  from . import STOP_EVENT
except:
  STOP_EVENT = Event()

class WebSocket( Thread ):

  def __init__(self, host="192.168.200.150", port = 20486, **kwargs):

    super().__init__()
    self.host       = host
    self.port       = port

    self._log       = logging.getLogger(__name__)
    self._socket    = None
    self._connected = False
    self._lock      = Lock()
    self._length    = 4
    self._byteorder = "little"

    self.start()

  def _connect(self):
    """Try to connect to the socket"""

    with self._lock:							    # Grab lock of thread safety
      if self._connected: return                                            # If we are connected, then return
      if self._socket is not None: self._socket.close()                       # If socket exists, close it
      self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)	    # Setup new socket
      try:								    # Try to connect
        self._socket.connect( (self.host, self.port) )
      except Exception as err:						    # Exception on connect
        self._log.debug( f"Failed to connect to web socket {err}" )
        self._connected = False
      else:								    # Else, set connected to true
        self._connected = True

  def run(self):
    """Method run a separate thread"""

    self._connect()
    while not STOP_EVENT.wait( 60.0 ):																				# Wait for 60 seconds for event to set; keep running while event NOT set
      self._connect()

    self._socket.close()																												# Close the socket

    self._log.debug( "Thread dead" )

  def write( self, **kwargs ):
    """
    Write data to the socket

    Keyword arguments:
      All keyword arguments are converted to JSON format, then convert
      to a binary string. The length of this string is prepended before
      sending all data to socket

    """

    with self._lock:																													# Grab lock for thread safety
      if not self._connected:
        self._log.debug( "Not connected, cannot send data" )
        return

      kwargs["timestamp"] = datetime.now().isoformat()																			# Add timestamp to keywords with current time of record
      for key, val in kwargs.items():
        if not isinstance(val, (list, tuple)):
          kwargs[key] = [val]
      data    = json.dumps( kwargs ).encode()																# Convert keyword data to bytes
      dataLen = len(data).to_bytes(self._length, self._byteorder)						# Prepend length of data to data bytes
      try:
        self._socket.sendall( dataLen + data )																# Send the data
      except Exception as err:
        self._log.error( f"Faild to send data to socket : {err}" )
        self._connected = False

# echo-server.py

def testServer( host, port ):
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind( (host, port) )
    s.listen()
    while True:
      conn, addr = s.accept()
      with conn:
        print(f"Connected by {addr}")
        while True:
          dataLen = conn.recv( 4 )
          if dataLen:
            data = conn.recv( int.from_bytes(dataLen, "little") )
            print( json.loads( data.decode() ) )
          else:
            break
      print( f"Disconnected from {addr}" )
  
