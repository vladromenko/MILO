"""Independent encrypted HTTP forwards, supervised by systemd on disconnect."""
import os
import subprocess
from .tunnel import ssh_args
from .settings import service_prefix, settings


def main():
    prefix = service_prefix()
    remote = f'systemctl --user start {prefix}-agent.service {prefix}-edge.service'
    if settings().get('visual_observer_enabled', False):
        remote += f' {prefix}-vlm.service'
    subprocess.run(ssh_args() + [remote],
                   check=True, timeout=15)
    args = ssh_args()
    args[1:1] = ["-N", "-L", "127.0.0.1:8872:127.0.0.1:8782",
                 "-R", "127.0.0.1:8873:127.0.0.1:8783"]
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
