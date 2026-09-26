#!/usr/bin/env python3
"""Create or update private runtime and network configuration without starting MILO."""

import argparse
import json
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from milo_next.settings import initialize, settings, write_private_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['brain', 'edge'])
    parser.add_argument('--wifi', required=True, help='NetworkManager Wi-Fi interface')
    parser.add_argument('--wired', help='Jetson Ethernet interface used for 10.43.0.1/24')
    parser.add_argument('--edge-host', default='10.42.0.2')
    parser.add_argument('--edge-user', default='vlados')
    parser.add_argument('--edge-root', default='/home/vlados/MILO')
    parser.add_argument('--camera')
    parser.add_argument('--serial')
    parser.add_argument('--display-output')
    parser.add_argument('--display-transform')
    parser.add_argument('--input-hint')
    parser.add_argument('--output-hint')
    parser.add_argument('--hailo-model')
    args = parser.parse_args()
    if args.role == 'brain' and not args.wired:
        parser.error('--wired is required for the brain role')

    initialize(args.role)
    runtime = settings()
    runtime.update(role=args.role, service_prefix='milo', edge_host=args.edge_host,
                   edge_user=args.edge_user, edge_root=args.edge_root)
    for argument, key in (
        ('camera', 'camera'), ('serial', 'serial'), ('display_output', 'display_output'),
        ('display_transform', 'display_transform'), ('input_hint', 'input_hint'),
        ('output_hint', 'output_hint'), ('hailo_model', 'hailo_model')
    ):
        value = getattr(args, argument)
        if value:
            runtime[key] = value
    write_private_json(ROOT / 'config/runtime.json', runtime)

    network_path = ROOT / 'config/network.json'
    network = json.loads(network_path.read_text()) if network_path.exists() else {
        'password': secrets.token_urlsafe(24)
    }
    network['wifi'] = args.wifi
    if args.wired:
        network['wired'] = args.wired
    write_private_json(network_path, network)
    print('Private configuration updated. No services started and no motion sent.')


if __name__ == '__main__':
    main()
