"""Select the Pi's static robot-network address using an already verified key."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milo_next.settings import settings, write_private_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host-key', required=True, help='verified Pi ed25519 PUBLIC host key')
    args = parser.parse_args()
    fields = args.host_key.split()
    if len(fields) != 2 or fields[0] != 'ssh-ed25519':
        raise ValueError('expected an ed25519 public host key without comment')
    cfg = settings()
    if cfg.get('service_prefix') != 'milo' or cfg['role'] != 'brain':
        raise ValueError('only the brain installation can be changed')
    path = ROOT / 'config/known_hosts'
    lines = path.read_text().splitlines()
    entry = '10.42.0.2 ' + args.host_key
    existing = [line for line in lines if line.split(' ')[0] == '10.42.0.2']
    if existing and existing != [entry]:
        raise ValueError('existing robot-network host key differs; verify before replacing')
    if not existing:
        path.write_text('\n'.join(lines + [entry]) + '\n')
        path.chmod(0o600)
    cfg.setdefault('previous_edge_host', cfg['edge_host'])
    cfg['edge_host'] = '10.42.0.2'
    write_private_json(ROOT / 'config/runtime.json', cfg)
    print('Runtime now uses verified Pi at 10.42.0.2. Restart rpc and dds after AP activation.')


if __name__ == '__main__':
    main()
