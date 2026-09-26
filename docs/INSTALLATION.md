# Installation and First Commissioning

This procedure creates a new installation without starting MILO or moving the
arm. Commands marked **Jetson**, **Pi**, and **workstation** must run on the named
machine. Keep the existing working disks until the final cold-boot acceptance
has passed.

## 1. Base Systems

Install Ubuntu 24.04 with NVIDIA CUDA on the Jetson and Debian 13 arm64 on the
Raspberry Pi. Install ROS 2 Jazzy on Jetson from the official ROS packages and
the Hailo-10H 5.1.1 driver/runtime on Pi. Confirm CUDA, ROS, and Hailo independently
before installing this repository. CUDA, ROS, and Hailo are vendor/platform
layers and are deliberately verified rather than silently replaced by the
project. All ordinary Debian/Ubuntu packages are installed by `install.sh`.

## 2. Clone and Build

**Jetson:**

```bash
git clone https://github.com/vladromenko/MILO.git /home/vlad/MILO
cd /home/vlad/MILO
./install.sh jetson
```

**Pi:**

```bash
git clone https://github.com/vladromenko/MILO.git /home/vlados/MILO
cd /home/vlados/MILO
./install.sh pi
```

The installers add missing operating-system packages, create a virtual
environment, verify model SHA-256 hashes, build pinned native dependencies,
install and verify the relocatable Pi micro-ROS agent, run the hardware-free
tests, and write systemd user units. They do not start the robot target or send
motion commands.

## 3. Configure Devices and Shared Secrets

Discover interface and device names with the commands in
[Hardware](HARDWARE.md). Then configure Jetson first:

```bash
cd /home/vlad/MILO
.venv/bin/python scripts/configure_runtime.py brain \
  --wifi wlP1p1s0 --wired enP8p1s0 \
  --edge-host 10.42.0.2 --edge-user vlados --edge-root /home/vlados/MILO
```

Copy the two private configuration files to Pi, then let the Pi-specific command
replace only its role and device names. This preserves the shared bearer token
and Wi-Fi password.

```bash
scp config/runtime.json config/network.json vlados@PI_ADDRESS:/home/vlados/MILO/config/
ssh vlados@PI_ADDRESS 'chmod 600 /home/vlados/MILO/config/runtime.json /home/vlados/MILO/config/network.json'
```

**Pi:**

```bash
cd /home/vlados/MILO
.venv/bin/python scripts/configure_runtime.py edge \
  --wifi wlan0 \
  --camera /dev/video0 \
  --serial /dev/serial/by-id/YOUR_CP2104_PATH \
  --display-output HDMI-A-1 --display-transform 90 \
  --input-hint UM02 --output-hint UACDemo \
  --hailo-model /usr/share/hailo-models/yolov8m_h10.hef
```

Never commit `runtime.json` or `network.json`.

## 4. Pair Jetson to Pi

**Jetson:**

```bash
cd /home/vlad/MILO
ssh-keygen -t ed25519 -f config/edge_key -N '' -C milo-edge
ssh-copy-id -i config/edge_key.pub vlados@PI_ADDRESS
ssh-keyscan -H PI_ADDRESS > config/known_hosts
chmod 600 config/edge_key config/known_hosts
```

Verify the displayed Pi host fingerprint through a trusted local connection
before accepting it. Replace `PI_ADDRESS` with the commissioning address; after
network setup, `edge_host` remains `10.42.0.2`.

## 5. Verify the Native micro-ROS Agent on Pi

`./install.sh pi` downloads the release's aarch64 ROS-aware agent bundle,
verifies the archive SHA-256, extracts it under `vendor/agent`, and runs its
internal binary, library, and checksum checks without opening the serial port.
Verify it again at any time:

```bash
cd /home/vlados/MILO
./vendor/agent/check-libs
```

The reproducible bundler remains in `scripts/bundle_agent.py` for maintainers who
need to rebuild the archive from the pinned Jetson ROS workspace. Normal users
do not need to copy a binary manually. The running agent owns the CP2104
exclusively; do not run another micro-ROS agent or a legacy serial relay.

## 6. Install the Offline Network

Follow [Networking](NETWORKING.md). Installation is staged and automatically
rolls back after three minutes unless both hosts and the phone are reachable.

## 7. Enable User Services

On both machines:

```bash
sudo loginctl enable-linger "$USER"
```

Reinstall the units once configuration is final:

```bash
# Jetson
cd /home/vlad/MILO
.venv/bin/python scripts/install_services.py brain --no-enable

# Pi
cd /home/vlados/MILO
.venv/bin/python scripts/install_services.py edge --no-enable
```

Only the Jetson phone panel is enabled at boot. The main `milo.target` remains
disabled, so powering the computers does not start conversation or move the arm.

## 8. Preflight and Acceptance

```bash
# Jetson
cd /home/vlad/MILO
.venv/bin/python scripts/preflight.py brain

# Pi
cd /home/vlados/MILO
.venv/bin/python scripts/preflight.py edge
./vendor/agent/check-libs
```

The equivalent full checks are available through the same installation entry
point:

```bash
./install.sh jetson --check
./install.sh pi --check
```

First start without movement:

```bash
cd /home/vlad/MILO
./milo start --no-home
./milo status
```

Complete the attended sequence in [Testing](TESTING.md). Only after stable arm
feedback and a clear path should `./milo start` or **Start MILO** be used to send
the startup posture.

## Backup and Restore Boundary

Git restores source, manifests, firmware history, tests, and documentation. It
does not restore secrets, downloaded models, build products, SSH identity, face
embeddings, or personal memory. Back up `config/` and `data/` separately in
encrypted storage while MILO is stopped. Preserve `data/ESTOP` during recovery.
