#!/usr/bin/env python3
"""Staged offline network installation. Inspect output before running as root.

Install prepares profiles without switching Wi-Fi. Activate schedules an
automatic rollback; confirm is required after checking both SSH paths.
"""
import argparse
import configparser
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
NETWORK_DIR = Path('/etc/milo-network')
NM_DIR = Path('/etc/NetworkManager/system-connections')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def profile(role, wifi, password):
    if role not in {'jetson', 'pi'} or not re.fullmatch(r'[A-Za-z0-9_-]+', wifi):
        raise ValueError('invalid role or interface')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,63}', password):
        raise ValueError('network password must be 16..63 letters, digits, underscore or hyphen')
    name = 'milo-ap' if role == 'jetson' else 'milo-body'
    return {
        'connection': {'id': name, 'uuid': str(uuid.uuid5(uuid.NAMESPACE_DNS, 'milo.' + name)),
                       'type': 'wifi', 'interface-name': wifi, 'autoconnect': 'false',
                       'autoconnect-priority': '100', 'autoconnect-retries': '0'},
        'wifi': {'mode': 'ap' if role == 'jetson' else 'infrastructure', 'ssid': 'MILO-NET',
                 **({'band': 'bg', 'channel': '6'} if role == 'jetson' else {}),
                 'powersave': '2'},
        'wifi-security': {'key-mgmt': 'wpa-psk', 'psk': password},
        'ipv4': {'method': 'shared' if role == 'jetson' else 'manual',
                 'address1': '10.42.0.1/24' if role == 'jetson' else '10.42.0.2/24',
                 'never-default': 'true'},
        'ipv6': {'method': 'disabled'},
    }


def admin_profile(wired):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', wired):
        raise ValueError('invalid wired interface')
    return {
        'connection': {'id': 'milo-admin', 'uuid': str(uuid.uuid5(uuid.NAMESPACE_DNS, 'milo.admin')),
                       'type': 'ethernet', 'interface-name': wired, 'autoconnect': 'true'},
        'ethernet': {},
        'ipv4': {'method': 'manual', 'address1': '10.43.0.1/24', 'never-default': 'true'},
        'ipv6': {'method': 'disabled'},
    }


def write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + secrets.token_hex(8))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'w') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_profile(values):
    path = NM_DIR / (values['connection']['id'] + '.nmconnection')
    import io
    output = io.StringIO()
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_dict(values)
    parser.write(output, space_around_delimiters=False)
    write(path, output.getvalue())
    run('nmcli', 'connection', 'load', str(path))


def unit(name, content):
    write(Path('/etc/systemd/system') / name, content, 0o644)


def install(args, cfg):
    wifi = cfg['wifi']
    values = profile(args.role, wifi, cfg['password'])
    if not (Path('/sys/class/net') / wifi).exists():
        raise ValueError('configured Wi-Fi interface is missing')
    if args.role == 'jetson':
        for binary in ('/usr/sbin/dnsmasq', '/lib/systemd/systemd-socket-proxyd'):
            if not Path(binary).exists():
                raise ValueError(f'required system binary missing: {binary}')
        admin = admin_profile(cfg['wired'])
        if not (Path('/sys/class/net') / cfg['wired']).exists():
            raise ValueError('configured Ethernet interface is missing')
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)
    installed = NETWORK_DIR / 'setup_network.py'
    shutil.copyfile(__file__, installed)
    installed.chmod(0o700)
    # Preserve the active home-network profile by UUID for bounded rollback.
    previous = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', wifi,
                   capture_output=True).stdout.strip()
    state_path = NETWORK_DIR / 'state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if (state['role'], state['wifi']) != (args.role, wifi):
            raise ValueError('existing network installation uses a different role or interface')
    else:
        if previous == values['connection']['uuid']:
            raise ValueError('cannot infer original Wi-Fi while MILO profile is active')
        state = {'role': args.role, 'wifi': wifi, 'previous': previous}
        write(state_path, json.dumps(state))
    save_profile(values)
    if args.role == 'jetson':
        save_profile(admin)
        active_wired = run('nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', cfg['wired'],
                           capture_output=True).stdout.strip()
        if active_wired != admin['connection']['uuid']:
            run('nmcli', 'connection', 'up', 'milo-admin')
        # NetworkManager owns AP DHCP. A second server causes an activation loop.
        legacy = Path('/etc/systemd/system/milo-network-dhcp.service')
        if legacy.exists():
            run('systemctl', 'disable', '--now', legacy.name)
        write(Path('/etc/NetworkManager/dnsmasq-shared.d/milo.conf'),
              'address=/milo.local/10.42.0.1\n', 0o644)
        unit('milo-web.socket', '''[Unit]
Description=MILO phone interface on robot and admin networks
[Socket]
ListenStream=10.42.0.1:80
ListenStream=10.43.0.1:80
FreeBind=true
[Install]
WantedBy=sockets.target
''')
        unit('milo-web.service', '''[Unit]
Description=MILO phone HTTP forward
[Service]
ExecStart=/lib/systemd/systemd-socket-proxyd 127.0.0.1:8784
DynamicUser=yes
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
''')
        hosts = Path('/etc/avahi/hosts')
        if hosts.exists():
            original = hosts.read_text()
            if '# MILO offline alias' not in original:
                write(NETWORK_DIR / 'avahi-hosts.before', original)
                write(hosts, original + '\n10.42.0.1 milo.local # MILO offline alias\n', 0o644)
                run('systemctl', 'restart', 'avahi-daemon')
    else:
        unit('milo-network-join.service', f'''[Unit]
Description=MILO robot network retry
After=NetworkManager.service
[Service]
ExecStart=/usr/bin/python3 /etc/milo-network/setup_network.py pi --watch
Restart=always
RestartSec=3
[Install]
WantedBy=multi-user.target
''')
    write(NETWORK_DIR / 'state.json', json.dumps(state))
    run('systemctl', 'daemon-reload')
    if args.role == 'jetson':
        run('systemctl', 'enable', '--now', 'milo-web.socket')
    print('Profiles installed. Existing Wi-Fi is unchanged. Next: verify Ethernet SSH, then --activate.')


def activate(role):
    # This deadline restores the previous Wi-Fi if the new path is not confirmed.
    subprocess.run(['systemctl', 'stop', 'milo-network-rollback.timer',
                    'milo-network-rollback.service'], check=False, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', 'reset-failed', 'milo-network-rollback.service'],
                   check=False, stderr=subprocess.DEVNULL)
    run('systemd-run', '--unit=milo-network-rollback', '--on-active=180',
        '/usr/bin/python3', '/etc/milo-network/setup_network.py', role, '--rollback')
    name = 'milo-ap' if role == 'jetson' else 'milo-body'
    run('nmcli', 'connection', 'modify', name, 'connection.autoconnect', 'yes')
    if role == 'jetson':
        method = run('nmcli', '-g', 'ipv4.method', 'connection', 'show', name,
                     capture_output=True).stdout.strip()
        if method != 'shared':
            raise ValueError('reinstall the AP profile: NetworkManager must own DHCP')
        run('nmcli', '--wait', '20', 'connection', 'up', name)
    else:
        run('systemctl', 'enable', '--now', 'milo-network-join.service')
    print('Rollback armed for 180 seconds. Confirm only after both devices and phone work.')


def rollback(role):
    state = json.loads((NETWORK_DIR / 'state.json').read_text())
    if role == 'pi':
        subprocess.run(['systemctl', 'disable', '--now', 'milo-network-join.service'], check=False)
    name = 'milo-ap' if role == 'jetson' else 'milo-body'
    run('nmcli', 'connection', 'modify', name, 'connection.autoconnect', 'no')
    subprocess.run(['nmcli', 'connection', 'down', name], check=False, stderr=subprocess.DEVNULL)
    previous = state.get('previous')
    if previous and previous != '--':
        run('nmcli', 'connection', 'up', 'uuid', previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['jetson', 'pi'])
    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ('install', 'install-and-activate', 'activate', 'confirm', 'rollback', 'watch'):
        actions.add_argument('--' + name, action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Use sudo in your terminal; never send the administrator password to chat.')
    if args.install or args.install_and_activate:
        cfg = json.loads((ROOT / 'config/network.json').read_text())
        install(args, cfg)
        if args.install_and_activate:
            activate(args.role)
    elif args.activate:
        activate(args.role)
    elif args.confirm:
        run('systemctl', 'stop', 'milo-network-rollback.timer')
        print('Network activation retained. Cold boot verification is still required.')
    elif args.rollback:
        rollback(args.role)
    else:
        while True:
            active = run('nmcli', '-t', '-f', 'NAME', 'connection', 'show', '--active',
                         capture_output=True).stdout.splitlines()
            if 'milo-body' not in active:
                subprocess.run(['nmcli', '--wait', '12', 'connection', 'up', 'milo-body'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            time.sleep(5)


if __name__ == '__main__':
    main()
