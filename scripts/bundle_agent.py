#!/usr/bin/env python3
"""Read a trusted Jetson install over SSH into a local, relocatable agent archive.

No remote files, services, serial ports, or ROS participants are created.
Only the existing executable's --help is executed; ldd inspects trusted binaries.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import tarfile


HOST_LIBS = {
    "libc.so.6", "libm.so.6", "libpthread.so.0", "libdl.so.2",
    "librt.so.1", "libresolv.so.2", "libutil.so.1", "ld-linux-aarch64.so.1",
}
PACKAGES = ("micro_ros_msgs", "rmw_dds_common", "builtin_interfaces", "arm_msgs")
BACKENDS = ("typesupport_fastrtps_cpp", "typesupport_introspection_cpp")


def parse_ldd(output):
    result = {}
    for line in output.splitlines():
        if "=> not found" in line:
            raise RuntimeError("Unresolved dependency: " + line.strip())
        match = re.match(r"\s*(\S+)\s+=>\s+(/\S+)\s+\(", line)
        if match:
            result[match[1]] = match[2]
        elif line.lstrip().startswith("/"):
            path = line.split()[0]
            result[Path(path).name] = path
    return result


def capture(args):
    return subprocess.check_output(args, text=True, timeout=30)


def inventory(root):
    if platform.machine() != "aarch64":
        raise RuntimeError("The source host must be aarch64")
    install = root / "ros_ws/install"
    executable = install / "micro_ros_agent/lib/micro_ros_agent/micro_ros_agent"
    files = {"bin/micro_ros_agent": str(executable)}
    queue = [executable]
    dynamic = []
    for package in PACKAGES:
        directory = install / package / "lib"
        if not directory.is_dir():
            directory = Path("/opt/ros/jazzy/lib")
        for backend in BACKENDS:
            required = directory / f"lib{package}__rosidl_{backend}.so"
            if not required.is_file():
                raise RuntimeError(f"Missing dynamic typesupport: {required}")
        for path in sorted(directory.glob(f"lib{package}__rosidl_*.so")):
            if "generator_py" in path.name:
                continue
            files["lib/" + path.name] = str(path)
            dynamic.append(path.name)
            queue.append(path)
    seen = set()
    host = {}
    while queue:
        path = queue.pop()
        resolved = path.resolve(strict=True)
        if resolved in seen:
            continue
        seen.add(resolved)
        for name, dependency in parse_ldd(capture(["ldd", str(path)])).items():
            if name in HOST_LIBS:
                host[name] = dependency
                continue
            key = "lib/" + name
            previous = files.get(key)
            if previous and Path(previous).resolve() != Path(dependency).resolve():
                raise RuntimeError(f"Conflicting library {name}: {previous}, {dependency}")
            files[key] = dependency
            queue.append(Path(dependency))
    help_run = subprocess.run([str(executable), "serial", "--help"],
                              capture_output=True, text=True, timeout=15)
    help_text = help_run.stdout + help_run.stderr
    if help_run.returncode or "--dev" not in help_text or "--discovery" not in help_text:
        raise RuntimeError("Unexpected agent help response")
    manifest = {
        "architecture": platform.machine(), "source_glibc": capture(["getconf", "GNU_LIBC_VERSION"]).strip(),
        "host_provided_glibc": host, "dynamic_typesupport": dynamic,
        "files": {}, "agent_help": help_text,
    }
    for name, source in sorted(files.items()):
        digest = hashlib.file_digest(open(source, "rb"), "sha256").hexdigest()
        manifest["files"][name] = {"source": source, "sha256": digest,
                                   "bytes": os.stat(source).st_size}
    return files, manifest


RUNNER = '''#!/bin/sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -eq 0 ]; then
    echo 'No transport selected. Supply serial --dev DEVICE -b 2000000 -v 6.' >&2
    exit 2
fi
case "$*" in
    *--help*) ;;
    *) : "${FASTRTPS_DEFAULT_PROFILES_FILE:?Set your reviewed loopback TCPv4 XML profile}"
       test -r "$FASTRTPS_DEFAULT_PROFILES_FILE" ;;
esac
export LD_LIBRARY_PATH="$bundle_dir/lib"
export XRCE_DOMAIN_ID_OVERRIDE="${XRCE_DOMAIN_ID_OVERRIDE:-30}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-30}"
exec "$bundle_dir/bin/micro_ros_agent" "$@"
'''

PREFLIGHT = '''#!/bin/sh
set -eu
bundle_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export LD_LIBRARY_PATH="$bundle_dir/lib"
exec python3 -B - "$bundle_dir" <<'PY'
import ctypes, hashlib, json, os, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
manifest = json.loads((root / 'manifest.json').read_text())
for name, entry in manifest['files'].items():
    path = root / name
    with path.open('rb') as handle:
        assert hashlib.file_digest(handle, 'sha256').hexdigest() == entry['sha256'], name
    result = subprocess.run(['ldd', str(path)], capture_output=True, text=True, check=True)
    assert 'not found' not in result.stdout + result.stderr, name
for name in manifest['files']:
    if name.startswith('lib/'):
        ctypes.CDLL(str(root / name), mode=os.RTLD_NOW | os.RTLD_LOCAL)
result = subprocess.run([str(root / 'bin/micro_ros_agent'), 'serial', '--help'],
                        capture_output=True, text=True, check=True)
assert '--dev' in result.stdout + result.stderr
print('Checksums, ELF dependencies, dynamic libraries and help passed; no serial port opened.')
PY
'''

NOTES = '''# Pi agent bundle

Contains the existing aarch64 ROS-aware agent, original linked libraries and
native typesupport libraries for micro_ros_msgs, rmw_dds_common,
builtin_interfaces and arm_msgs, including Fast DDS and introspection backends.
Python message generators, CUDA, models, ROS CLI and full ROS installation are
not needed. The agent handles arm wire types without importing Python messages;
arm typesupport is included for completeness. manifest.json records every
source, size and SHA-256. System glibc/loader come from the destination; bundled
libstdc++ and libgcc are the original source versions. Target: Debian 13 aarch64.

No deployment or service activation is performed by this package.

## Owner-controlled validation and launch

After the owner extracts the archive, ./check-libs performs only checksums,
ldd, shared-library loads and serial --help; it does not open the serial port.
This check has NOT been run on the Pi by the bundler.

For the eventual owner-controlled launch, set an absolute
FASTRTPS_DEFAULT_PROFILES_FILE pointing to the reviewed loopback-only TCPv4
profile, XRCE_DOMAIN_ID_OVERRIDE=30, and run:

    ./run-agent serial --dev /dev/serial/by-id/ACTUAL_CP2104_ID -b 2000000 -v 6

This command is documentation only; opening physical serial may affect modem
control lines. It is NOT equivalent to the Python relay's DTR/RTS-low open.
Review that behavior before replacing the current serial owner. Keep ESTOP.
The owner must ensure exclusive serial ownership and that the old relay/agent
cannot reconnect. No resets, actuator commands, or service changes are included.

The launch wrapper requires a profile, but does not validate its network
isolation. Both XRCE-created and ROS graph participants must use loopback TCPv4
with built-in UDP transports disabled. The owner supplies XML and SSH forwarding.
Use FASTRTPS_DEFAULT_PROFILES_FILE on the Jetson ROS process as well. Review
ROS_LOCALHOST_ONLY and discovery settings there against the explicit TCP
profile; the bundle makes no Jetson environment changes. Keep domain 30 on both.
Do not globally install these libraries or change model process environments.

## Domain and dependencies

Actual help is in agent-help.txt. This build has NO --domain/-d domain flag:
-d/--discovery is a discovery port. Its Fast DDS implementation reads
XRCE_DOMAIN_ID_OVERRIDE; ROS_DOMAIN_ID alone is not that agent override.
The ROS graph manager uses the domain of the XRCE participant.

Agent graph support dynamically requests
micro_ros_msgs__rosidl_typesupport_fastrtps_cpp and
rmw_dds_common__rosidl_typesupport_fastrtps_cpp. These are absent from the
executable's initial ldd list, so bundling only that list is insufficient.
All included native typesupport libraries have their own ldd closure collected.
The standalone MicroXRCEAgent build option was OFF. Rebuilding that executable
would omit the ROS graph integration used by node/subscriber safety checks.

Original Jetson glibc is 2.39, target Pi glibc is 2.41; architecture and libc
direction are compatible, but target runtime checks and owner-controlled
integration remain necessary. No claim of working serial/TCP operation is made.
'''


def add_text(archive, name, text, mode=0o644):
    data = text.encode()
    info = tarfile.TarInfo("milo-agent/" + name)
    info.size, info.mode = len(data), mode
    archive.addfile(info, io.BytesIO(data))


def remote_bundle(root):
    files, manifest = inventory(root)
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz", dereference=True) as archive:
        for name, source in sorted(files.items()):
            archive.add(source, arcname="milo-agent/" + name, recursive=False)
        add_text(archive, "manifest.json", json.dumps(manifest, indent=2) + "\n")
        add_text(archive, "agent-help.txt", manifest["agent_help"])
        add_text(archive, "README.md", NOTES)
        add_text(archive, "run-agent", RUNNER, 0o755)
        add_text(archive, "check-libs", PREFLIGHT, 0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SSH destination for the Jetson build host")
    parser.add_argument("--root", type=Path, default=Path("/home/vlad/MILO"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--remote", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.remote:
        remote_bundle(args.root)
        return
    if args.output is None:
        parser.error("--output is required; existing paths are never overwritten")
    script = Path(__file__).read_bytes()
    command = "\n".join([
        "set -e", "source /opt/ros/jazzy/setup.bash",
        "source " + shlex.quote(str(args.root / "ros_ws/install/setup.bash")),
        "exec python3 -B - --remote --root " + shlex.quote(str(args.root)),
    ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as destination:
        try:
            subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                            "-o", "StrictHostKeyChecking=yes", args.host, command],
                           input=script, stdout=destination, check=True, timeout=180)
        except BaseException:
            args.output.unlink(missing_ok=True)
            raise
    with tarfile.open(args.output) as archive:
        manifest = json.load(archive.extractfile("milo-agent/manifest.json"))
        for name, entry in manifest["files"].items():
            with archive.extractfile("milo-agent/" + name) as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != entry["sha256"]:
                raise RuntimeError("Source changed during transfer: " + name)
    print(f"Verified {len(manifest['files'])} binaries/libraries; "
          f"{args.output.stat().st_size / 1048576:.1f} MiB: {args.output}")


if __name__ == "__main__":
    main()
