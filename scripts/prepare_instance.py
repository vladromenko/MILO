"""Prepare a separate runtime from local, existing assets without editing them."""
import argparse
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milo_next.settings import write_private_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['brain', 'edge'])
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    if source == ROOT:
        raise ValueError('source must be a different installation')
    for name in ('config', 'data', 'logs'):
        (ROOT / name).mkdir(exist_ok=True)
    runtime = ROOT / 'config/runtime.json'
    if not runtime.exists():
        cfg = json.loads((source / 'config/runtime.json').read_text())
        cfg.update(role=args.role, service_prefix='milo', edge_root='/home/vlados/MILO')
        write_private_json(runtime, cfg)
    for name in ('edge_key', 'edge_key.pub', 'known_hosts', 'llm.key'):
        original, destination = source / 'config' / name, ROOT / 'config' / name
        if original.exists() and not destination.exists():
            shutil.copy2(original, destination)
            destination.chmod(0o600)
    for name in ('assets', 'vendor', 'ros_ws'):
        if (source / name).exists() and not (ROOT / name).exists():
            shutil.copytree(source / name, ROOT / name, symlinks=True)
    db = source / 'data/memory.sqlite3'
    if db.exists() and not (ROOT / 'data/memory.sqlite3').exists():
        with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as old:
            with sqlite3.connect(ROOT / 'data/memory.sqlite3') as new:
                old.backup(new)
        (ROOT / 'data/memory.sqlite3').chmod(0o600)
    if (source / 'data/ESTOP').exists():
        shutil.copy2(source / 'data/ESTOP', ROOT / 'data/ESTOP')
    network = ROOT / 'config/network.json'
    cfg = json.loads(network.read_text()) if network.exists() else {'password': secrets.token_urlsafe(24)}
    cfg.update(wifi='wlP1p1s0' if args.role == 'brain' else 'wlan0', wired='enP8p1s0')
    write_private_json(network, cfg)
    print('Separate runtime prepared. No services started and no motion sent.')


if __name__ == '__main__':
    main()
