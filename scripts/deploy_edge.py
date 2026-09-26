"""Update the edge through Jetson, preserving local assets and private state."""
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milo_next.settings import settings, service_prefix
from milo_next.tunnel import ssh_args


def main():
    cfg = settings()
    prefix = service_prefix(cfg)
    ssh = ssh_args()
    subprocess.run(ssh + [f"systemctl --user stop {prefix}-edge.service"], check=True)
    excluded = [".git", ".venv", "__pycache__", ".pytest_cache", "assets", "vendor",
                "ros_ws", "data", "logs", "artifacts", "hailort*.log", "config"]
    command = ["rsync", "-az"]
    for name in excluded:
        command.append("--exclude=" + name)
    command += ["-e", shlex.join(ssh[:-1]), str(ROOT) + "/", ssh[-1] + ":" + cfg["edge_root"] + "/"]
    subprocess.run(command, check=True)
    root = shlex.quote(cfg["edge_root"])
    subprocess.run(ssh + [f"cd {root} && .venv/bin/python scripts/install_services.py edge --no-enable && systemctl --user start {prefix}-edge.service"], check=True)


if __name__ == "__main__":
    main()
