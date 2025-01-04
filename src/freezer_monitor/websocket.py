import logging

from datetime import datetime
import socket
import json
from threading import Thread, Lock, Event

try:
    from . import STOP_EVENT
except Exception:
    STOP_EVENT = Event()


class WebSocket(Thread):

    SOCKET = None
    CONNECTED = False
    LENGTH = 4
    BYTEORDER = 'little'

    LOCK = Lock()
    LOG = logging.getLogger(__name__)

    def __init__(self, host="192.168.200.150", port=20486, **kwargs):

        super().__init__()
        self.host = host
        self.port = port

        self._log = logging.getLogger(__name__)

        self.start()

    @classmethod
    def _connect(cls, host, port):
        """Try to connect to the socket"""

        with cls.LOCK:  # Grab lock of thread safety
            if cls.CONNECTED:
                return  # If we are connected, then return

            # If socket exists, close it
            if cls.SOCKET is not None:
                cls.SOCKET.close()

            # Setup new socket
            cls.SOCKET = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM,
            )
            try:  # Try to connect
                cls.SOCKET.connect(
                    (host, port)
                )
            except Exception as err:  # Exception on connect
                cls.LOG.debug("Failed to connect to web socket %s", err)
                cls.CONNECTED = False
            else:  # Else, set connected to true
                cls.CONNECTED = True

    @classmethod
    def _close(cls):
        try:
            cls.SOCKET.close()
        except Exception:
            pass

    def run(self):
        """Method run a separate thread"""

        self._connect(self.host, self.port)
        # Wait for 60 seconds for event to set; keep running while NOT set
        while not STOP_EVENT.wait(60.0):
            self._connect()

        self._close()																												# Close the socket

        self._log.debug("Thread dead")

    def write(self, **kwargs):
        """
        Write data to the socket

        Keyword arguments:
          All keyword arguments are converted to JSON format, then convert
          to a binary string. The length of this string is prepended before
          sending all data to socket

        """

        # Grab lock for thread safety
        with self.LOCK:
            if not self.CONNECTED:
                self._log.debug("Not connected, cannot send data")
                return

            # Add timestamp to keywords with current time of record
            kwargs["timestamp"] = datetime.now().isoformat()
            for key, val in kwargs.items():
                if not isinstance(val, (list, tuple)):
                    kwargs[key] = [val]

            # Convert keyword data to bytes
            data = json.dumps(kwargs).encode()
            # Prepend length of data to data bytes
            dataLen = len(data).to_bytes(self.LENGTH, self.BYTEORDER)
            try:
                self.SOCKET.sendall(dataLen + data)  # Send the data
            except Exception as err:
                self._log.error("Faild to send data to socket : %s", err)
                self._connected = False


def testServer(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, port))
        s.listen()
        while True:
            conn, addr = s.accept()
            with conn:
                print(f"Connected by {addr}")
                while True:
                    dataLen = conn.recv(4)
                    if dataLen:
                        data = conn.recv(int.from_bytes(dataLen, "little"))
                        print(json.loads(data.decode()))
                    else:
                        break
            print(f"Disconnected from {addr}")
