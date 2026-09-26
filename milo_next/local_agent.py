"""Pi-local micro-ROS agent; preserve low DTR/RTS and exclusive serial ownership."""
import fcntl
import os
import pty
import select
import signal
import subprocess
import tty
import serial
from .settings import ROOT, settings


def main():
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    lock = open(ROOT / 'data/serial.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    port = serial.Serial(port=None, baudrate=2000000, timeout=0,
                         write_timeout=.25, exclusive=True)
    port.dtr = False
    port.rts = False
    port.port = settings()['serial']
    port.open()
    master, slave = pty.openpty()
    tty.setraw(master)
    tty.setraw(slave)
    agent = None
    try:
        agent = subprocess.Popen([str(ROOT / 'vendor/agent/bin/micro_ros_agent'),
            'serial', '--dev', os.ttyname(slave), '-b', '2000000', '-v', '4'])
        while not stopping and agent.poll() is None:
            ready, _, _ = select.select([master, port.fileno()], [], [], .1)
            if master in ready:
                data = os.read(master, 4096)
                if not data:
                    break
                port.write(data)
            if port.fileno() in ready:
                data = port.read(4096)
                while data:
                    data = data[os.write(master, data):]
    finally:
        if agent is not None and agent.poll() is None:
            agent.terminate()
            try:
                agent.wait(timeout=3)
            except subprocess.TimeoutExpired:
                agent.kill()
                agent.wait()
        port.close()
        os.close(master)
        os.close(slave)
        lock.close()


if __name__ == '__main__':
    main()
