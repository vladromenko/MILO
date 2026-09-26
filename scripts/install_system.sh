#!/usr/bin/env bash
set -Eeuo pipefail

ROLE="${1:?use brain or edge}"
[[ "$ROLE" == brain || "$ROLE" == edge ]] || exit 2
[[ "$(uname -m)" == aarch64 ]] || {
  echo "[BLOCKED] MILO requires aarch64 hardware"
  exit 1
}
command -v apt-get >/dev/null || {
  echo "[BLOCKED] the validated installer requires Debian or Ubuntu with apt"
  exit 1
}

common=(
  alsa-utils build-essential cmake curl ffmpeg git libasound2-dev
  libcurl4-openssl-dev libportaudio2 libssl-dev pkg-config portaudio19-dev
  python3-dev python3-opencv python3-scipy python3-serial python3-venv rsync
)
if [[ "$ROLE" == brain ]]; then
  packages=("${common[@]}" dnsmasq libasio-dev libtinyxml2-dev network-manager
    python3-colcon-common-extensions python3-rosdep python3-vcstool)
else
  packages=("${common[@]}" network-manager python3-pygame v4l-utils wlr-randr)
fi

missing=()
for package in "${packages[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed' || missing+=("$package")
done
if ((${#missing[@]})); then
  echo "[INSTALL] system packages: ${missing[*]}"
  sudo apt-get update
  sudo apt-get install -y "${missing[@]}"
fi

if [[ "$ROLE" == brain ]]; then
  [[ -x /usr/local/cuda/bin/nvcc ]] || {
    echo "[BLOCKED] install the NVIDIA Jetson CUDA stack before MILO"
    exit 1
  }
  [[ -f /opt/ros/jazzy/setup.bash ]] || {
    echo "[BLOCKED] install ROS 2 Jazzy before MILO"
    exit 1
  }
else
  command -v hailortcli >/dev/null || {
    echo "[BLOCKED] install the Hailo-10H 5.1.1 driver/runtime before MILO"
    exit 1
  }
  [[ -e /dev/hailo0 || -e /dev/hailo1x ]] || {
    echo "[BLOCKED] Hailo-10H is not available under /dev"
    exit 1
  }
fi

echo "[READY] ${ROLE} operating-system and vendor prerequisites"
