"""Install this checkout's units; only the phone control panel starts at boot."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milo_next.settings import initialize, service_prefix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["brain", "edge"])
    parser.add_argument('--no-enable', action='store_true')
    args = parser.parse_args()
    initialize(args.role)
    prefix = service_prefix()
    units = Path.home() / ".config/systemd/user"
    units.mkdir(parents=True, exist_ok=True)
    parts = ["edge"] if args.role == "edge" else ["llm", "stt", "rpc", "tunnel", "brain"]
    if (ROOT / 'config/native-agent.enabled').exists():
        parts = ['agent', 'edge'] if args.role == 'edge' else ['llm', 'stt', 'rpc', 'dds', 'brain']
    if args.role == 'brain':
        parts.append('web')
    if args.role == 'edge' and (ROOT / 'assets/models/vlm/smolvlm').exists():
        parts.append('vlm')
    for component in parts:
        # All paths are generated from the actual checkout, not another project.
        name = f"{prefix}-{component}.service"
        standby = component == 'web'
        content = f"""[Unit]
Description=MILO {component}
{'' if standby else 'PartOf=' + prefix + '.target'}
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart=/bin/bash {ROOT}/scripts/run-component.sh {component}
Restart=always
RestartSec=3
TimeoutStopSec=15
KillMode=control-group
UMask=0077
{'Nice=10' + chr(10) + 'CPUQuota=200%' + chr(10) + 'MemoryMax=4G' if component == 'vlm' else ''}

[Install]
WantedBy={'default.target' if standby else prefix + '.target'}
"""
        (units / name).write_text(content)
    names = " ".join(f"{prefix}-{part}.service" for part in parts if part != 'web')
    (units / f"{prefix}.target").write_text(f"""[Unit]
Description=MILO distributed robot
Wants={names}
After=network-online.target

[Install]
WantedBy=default.target
""")
    if args.role == 'brain':
        # These jobs are explicit only. Stopping a launch cancels pending home/tracking.
        for action, command in [('launch', 'start'), ('stop', 'stop')]:
            (units / f'{prefix}-{action}.service').write_text(f'''[Unit]
Description=MILO phone {command}
{'Conflicts=' + prefix + '-launch.service' + chr(10) + 'After=' + prefix + '-launch.service' if action == 'stop' else ''}

[Service]
Type=oneshot
WorkingDirectory={ROOT}
ExecStart={ROOT}/milo {command}
TimeoutStartSec=240
TimeoutStopSec=5
KillMode=control-group
UMask=0077
''')
        (units / f'{prefix}.target.wants' / f'{prefix}-web.service').unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(['systemctl', '--user', 'disable', prefix + '.target'], check=True)
    if args.role == 'brain':
        subprocess.run(['systemctl', '--user', 'enable', prefix + '-web.service'], check=True)
    print("Installed units; not started. Only the phone panel starts at boot. Linger is required.")


if __name__ == "__main__":
    main()
