"""Exclusive transparent serial transport. No boot pose or actuator commands.

Only the brain's micro-ROS agent speaks on this pipe. DTR/RTS remain low to
avoid resetting the controller when opening the CP2104. stdout is binary only.
"""
import fcntl
import os
import select
import sys
import serial

from .settings import ROOT, settings


def main():
    lock = open(ROOT / "data/serial.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    port = serial.Serial(port=None, baudrate=2000000, timeout=0, write_timeout=0.25,
                         exclusive=True)
    port.dtr = False
    port.rts = False
    port.port = settings()["serial"]
    port.open()
    try:
        while True:
            readable, _, _ = select.select([0, port.fileno()], [], [], 1)
            if 0 in readable:
                block = os.read(0, 4096)
                if not block:
                    break
                port.write(block)
            if port.fileno() in readable:
                block = port.read(4096)
                while block:
                    block = block[os.write(1, block):]
    finally:
        port.close()


if __name__ == "__main__":
    main()
