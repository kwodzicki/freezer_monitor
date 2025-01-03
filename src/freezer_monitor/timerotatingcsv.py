import os
from datetime import datetime
from threading import Thread
from queue import Queue


class DailyRotatingCSV(Thread):
    """
    Thread to rotate save file every day

    """

    def __init__(self, csvFile, backupCount=30):
        super().__init__()

        self.csv = os.path.abspath(csvFile)
        os.makedirs(
            os.path.dirname(self.csv),
            exist_ok=True,
        )

        self._backup = backupCount if backupCount > 1 else 1
        self._QUEUE = Queue()
        self._date = None
        self._fid = None
        self._closed = False
        self.start()

    def _initFile(self):

        # if file open, close it
        if self._fid:
            self._fid.close()

        fpath = f"{self.csv}.{self._date:%Y-%m-%d}"
        self._fid = open(fpath, mode="a", buffering=1)
        try:
            os.remove(self.csv)
        except Exception:
            pass
        os.link(fpath, self.csv)

    def _removeOld(self):

        fdir, fname = os.path.split(self.csv)
        for item in os.listdir(fdir):
            if fname in item:
                try:
                    date = datetime.strptime(
                        item.split(".")[-1],
                        "%Y-%m-%d",
                    )
                except Exception:
                    continue
                if (self._date - date).days > self._backup:
                    os.remove(
                        os.path.join(fdir, item)
                    )

    def _rotateFile(self):
        date = datetime.now()
        if date.date() != self._date.date():
            self._removeOld()
            self._initFile()
            self._date = date

    def _writeData(self, date, *args):

        self._rotateFile()
        self._fid.write(
            date.strftime("%Y-%m-%d %H:%M:%S.%f,")
        )
        self._fid.write(
            ",".join(
                [str(i).strip() for i in args]
            )
        )
        self._fid.write(os.linesep)

    def write(self, *args):

        if not self._closed:
            self._QUEUE.put(
                (datetime.now(),) + args
            )

    def run(self):

        self._date = datetime.now()
        self._initFile()

        while True:
            try:
                data = self._QUEUE.get(1.0)
            except Exception:
                continue
            else:
                self._QUEUE.task_done()

            if data is None:
                break
            self._writeData(*data)

        self._fid.close()

        while not self._QUEUE.empty():
            _ = self._QUEUE.get()
            self._QUEUE.task_done()

    def close(self):

        self._closed = True
        self._QUEUE.put(None)
