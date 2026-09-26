"""Supervised serial tunnel. Loss of the arm cannot break the separate RPC link."""
import os
import pty
import select
import signal
import subprocess
import time
import tty

from .settings import ROOT, settings


def ssh_args():
    cfg = settings()
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "IPQoS=lowdelay",
            "-o", "ServerAliveInterval=2", "-o", "ServerAliveCountMax=2",
            "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={ROOT}/config/known_hosts",
            "-i", str(ROOT / "config/edge_key"), f"{cfg['edge_user']}@{cfg['edge_host']}"]


def main():
    cfg = settings()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        master, slave = pty.openpty()
        tty.setraw(master)
        tty.setraw(slave)
        agent = ssh = None
        try:
            args = ssh_args()
            ssh = subprocess.Popen(args + [f"systemctl --user start milo-edge.service && cd {cfg['edge_root']} && exec .venv/bin/python -m milo_next.serial_relay"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
            agent = subprocess.Popen([str(ROOT / "ros_ws/install/micro_ros_agent/lib/micro_ros_agent/micro_ros_agent"),
                                      "serial", "--dev", os.ttyname(slave), "-b", "2000000"],
                                     stdout=subprocess.DEVNULL)
            while not stopping and ssh.poll() is None and agent.poll() is None:
                ready, _, _ = select.select([master, ssh.stdout], [], [], 0.1)
                for source in ready:
                    block = os.read(source if isinstance(source, int) else source.fileno(), 4096)
                    if not block:
                        raise ConnectionError("edge disconnected")
                    dest = ssh.stdin.fileno() if source == master else master
                    while block:
                        block = block[os.write(dest, block):]
        except (OSError, ConnectionError) as exc:
            print(f"Tunnel reconnect: {exc}", flush=True)
        finally:
            for process in (agent, ssh):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            os.close(master)
            os.close(slave)
        if not stopping:
            time.sleep(2)


if __name__ == "__main__":
    main()
