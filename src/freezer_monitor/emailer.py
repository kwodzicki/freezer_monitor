import logging

import time
import smtplib
from email.message import EmailMessage
from threading import Thread, Lock

from .utils import load_settings

RESEND = 30 * 60.0  # Time before another email will be sent
TLOCK = Lock()


def locked(fn):
    """To use as decorator to make function thread safe"""

    def lockedWrap(*args, **kwargs):
        with TLOCK:
            return fn(*args, **kwargs)

    return lockedWrap


# Threaded function snippet
def threaded(fn):
    """To use as decorator to make a function call threaded."""

    def threadedWrap(*args, **kwargs):
        thread = Thread(target=fn, args=args, kwargs=kwargs)
        thread.start()
        return thread

    return threadedWrap


class EMailer:

    def __init__(self):
        super().__init__()
        self.__log = logging.getLogger(__name__)

        self.__overTemp = -RESEND
        self.__underTemp = -RESEND
        self.__allNaN = -RESEND

        self.name = None
        settings = load_settings()

        self.send_to = settings.get("send_to", None)
        if self.send_to is None:
            self.__log.error("No `send_to' information!")
        elif isinstance(self.send_to, (list, tuple,)):
            self.send_to = ",".join(
                [s for s in self.send_to if s is not None]
            )

        self.send_from = settings.get("send_from", None)
        if self.send_from is None:
            self.__log.error("No `send_from' information!")

    def check(self):
        """Check that we can even send an email"""

        return (self.send_to is not None) and (self.send_from is not None)

    @locked
    def allNaN(self):
        """Send email for all NaN slice encountered!"""

        if not self.check():
            return
        if (time.monotonic()-self.__allNaN) < RESEND:
            return

        subject = f"{self.name} sensor ERROR!"
        content = (
            f"The 30-min average temperature of the '{self.name}' is "
            "full of NaN values!"
            "\n\n"
            "Something has gone wrong, check immediately!!!"
        )

        self.sendMail(subject, content)
        self.__allNaN = time.monotonic()

    @locked
    def overTemp(self, temp, rh):
        """Send email for over temperature threshold!"""

        if not self.check():
            return
        if (time.monotonic()-self.__overTemp) < RESEND:
            return

        subject = f"{self.name} getting HOT!"
        content = (
            f"The 30-min average temperature of the '{self.name}' has "
            "exceeded the threshold set!"
            "\n\n"
            "Current stats:"
            "\n\n"
            f"  Temperature       : {temp:6.1f} C"
            "\n"
            f"  Relative Humidity : {rh:6.1f} %"
            "\n\n"
            "Check on freezer immediately!!!"
        )
        self.sendMail(subject, content)
        self.__overTemp = time.monotonic()

    @locked
    def underTemp(self, temp, rh):
        """Send email for under temperature threshold!"""

        if not self.check():
            return
        if (time.monotonic()-self.__underTemp) < RESEND:
            return

        subject = f"{self.name} getting too COLD!"
        content = (
            f"The 30-min average temperature of the '{self.name}' has gone "
            "below the threshold set!"
            "\n\n"
            "Current stats:"
            "\n\n"
            f"  Temperature       : {temp:6.1f} C"
            "\n"
            f"  Relative Humidity : {rh:6.1f} %"
            "\n\n"
            "Check on freezer immediately!!!"
        )

        self.sendMail(subject, content)
        self.__underTemp = time.monotonic()

    def sendMail(self, subject, content):

        self.__log.debug("%s - Sending email", self.name)
        msg = EmailMessage()
        msg["From"] = self.send_from["user"]
        msg["To"] = self.send_to
        msg["Subject"] = subject
        msg.set_content(content)

        try:
            server = smtplib.SMTP_SSL(
                self.send_from["server"],
                self.send_from["port"],
            )
        except Exception as err:
            self.__log.error("Failed to set up smtp connection : %s", err)
            return False

        try:
            server.login(
                self.send_from["user"],
                self.send_from["pass"],
            )
        except Exception as err:
            self.__log.error("Failed to log into smtp server : %s", err)
            return False
        try:
            server.sendmail(
                self.send_from["user"],
                self.send_to, msg.as_string(),
            )
        except Exception as err:
            self.__log.error("Failed to send email : %s", err)
            pass

        try:
            server.close()
        except Exception:
            pass
