#!/usr/bin/env python3
"""Install a root-owned, bounded Hailo-only recovery timer. Never reboot the Pi."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


DEST = Path('/usr/local/libexec/milo-hailo-recovery.py')
STATE = Path('/run/milo-hailo-recovery')
SERVICE = '''[Unit]
Description=MILO Hailo device recovery (maximum three attempts per boot)
After=systemd-modules-load.service
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/libexec/milo-hailo-recovery.py --check
RuntimeDirectory=milo-hailo-recovery
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
TimeoutStartSec=100
UMask=0077
ProtectHome=yes
PrivateTmp=yes
'''
TIMER = '''[Unit]
Description=Check for a missing Hailo device
[Timer]
OnBootSec=45
OnUnitActiveSec=60
AccuracySec=5
Unit=milo-hailo-recovery.service
[Install]
WantedBy=timers.target
'''


def present():
    return any(Path('/dev').glob('hailo[0-9]*'))


def hailo_devices():
    result = []
    for path in Path('/sys/bus/pci/devices').iterdir():
        try:
            if (path.joinpath('vendor').read_text().strip() == '0x1e60'
                    and path.joinpath('device').read_text().strip() == '0x45c4'):
                result.append(path)
        except OSError:
            continue
    return result


def run(*args):
    return subprocess.run(args, check=True, timeout=30, capture_output=True, text=True)


def recover(state, *, available=present, devices=hailo_devices, execute=run, sleep=time.sleep):
    if available():
        return {'state': 'available'}
    paths = devices()
    if len(paths) != 1:
        return {'state': 'missing_or_ambiguous_pcie_device'}
    counter = state / 'attempts.json'
    try:
        attempts = json.loads(counter.read_text())['attempts'] if counter.exists() else 0
    except (OSError, ValueError, KeyError):
        return {'state': 'invalid_attempt_counter'}
    if type(attempts) is not int or not 0 <= attempts <= 3:
        return {'state': 'invalid_attempt_counter'}
    if attempts == 3:
        return {'state': 'recovery_exhausted', 'attempts': attempts, 'manual_power_check_required': True}
    # Persist before touching the driver; interruption must still consume the attempt.
    temporary = counter.with_suffix('.tmp')
    temporary.write_text(json.dumps({'attempts': attempts + 1}))
    temporary.replace(counter)
    try:
        execute('/usr/sbin/modprobe', '-r', 'hailo1x_pci')
        try:
            reset = paths[0] / 'reset'
            if reset.exists():
                reset.write_text('1\n')
        finally:
            execute('/usr/sbin/modprobe', 'hailo1x_pci')
        for _ in range(15):
            if available():
                return {'state': 'recovered', 'attempts': attempts + 1}
            sleep(1)
        return {'state': 'device_still_missing', 'attempts': attempts + 1}
    except (OSError, subprocess.SubprocessError) as exc:
        return {'state': 'recovery_failed', 'attempts': attempts + 1, 'error': str(exc)}


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--install', action='store_true')
    action.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run with sudo in the terminal; do not send passwords in chat.')
    if args.install:
        if not hailo_devices():
            parser.error('Hailo-10H PCIe device not found; install only on the Pi.')
        DEST.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve(), DEST)
        DEST.chmod(0o755)
        os.chown(DEST, 0, 0)
        for name, content in [('service', SERVICE), ('timer', TIMER)]:
            unit = Path('/etc/systemd/system') / ('milo-hailo-recovery.' + name)
            unit.write_text(content)
            unit.chmod(0o644)
            os.chown(unit, 0, 0)
        run('/usr/bin/systemctl', 'daemon-reload')
        run('/usr/bin/systemctl', 'enable', '--now', 'milo-hailo-recovery.timer')
        print('Installed Hailo-only recovery timer. No arm, audio or network service is restarted.')
    else:
        STATE.mkdir(mode=0o700, exist_ok=True)
        with (STATE / 'lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = recover(STATE)
            (STATE / 'status.json').write_text(json.dumps(result))
            print(json.dumps(result))


if __name__ == '__main__':
    main()
