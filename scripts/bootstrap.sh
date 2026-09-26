#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ROLE="${1:?use brain or edge}"
[[ "$ROLE" == brain || "$ROLE" == edge ]] || exit 2
mkdir -p assets vendor data logs config
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e . pytest
.venv/bin/python scripts/fetch_assets.py "$ROLE"
clone_pinned() {
  local url="$1" destination="$2" revision="$3"
  [[ -d "$destination/.git" ]] || git clone "$url" "$destination"
  git -C "$destination" diff --quiet
  git -C "$destination" checkout --detach "$revision"
  test "$(git -C "$destination" rev-parse HEAD)" = "$revision"
}
if [[ "$ROLE" == brain ]]; then
  clone_pinned https://github.com/ggml-org/llama.cpp vendor/llama.cpp 335b21fcbda972777e4e9e69decad1f719cafeb3
  clone_pinned https://github.com/ggml-org/whisper.cpp vendor/whisper.cpp 307869af285d7f6f689ba100b3515e2d1b3feb05
  cmake -S vendor/llama.cpp -B vendor/llama.cpp/build -DGGML_CUDA=ON \
    -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc -DCMAKE_CUDA_ARCHITECTURES=87 \
    -DLLAMA_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build vendor/llama.cpp/build --target llama-server -j2
  cmake -S vendor/whisper.cpp -B vendor/whisper.cpp/build -DGGML_CUDA=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build vendor/whisper.cpp/build --target whisper-server -j2
  if [[ "${MILO_STT_BACKEND:-cuda}" == cuda ]]; then
    cmake -S vendor/whisper.cpp -B vendor/whisper.cpp/build-cuda-final -DGGML_CUDA=ON \
      -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc -DCMAKE_CUDA_ARCHITECTURES=87 \
      -DWHISPER_BUILD_TESTS=OFF -DCMAKE_BUILD_TYPE=Release
    cmake --build vendor/whisper.cpp/build-cuda-final --target whisper-server -j2
  fi
  mkdir -p ros_ws/src
  clone_pinned https://github.com/micro-ROS/micro-ROS-Agent ros_ws/src/micro-ROS-Agent 7c932329ad5591ef23942ef1962534258c16b000
  clone_pinned https://github.com/micro-ROS/micro_ros_msgs ros_ws/src/micro_ros_msgs b1ef85201e5545d3e07b73f4628bb15836062573
  cp -a arm_msgs ros_ws/src/
  set +u
  source /opt/ros/jazzy/setup.bash
  set -u
  (cd ros_ws && MAKEFLAGS='-j2 -l2' colcon build --executor sequential --cmake-args -DCMAKE_BUILD_TYPE=Release)
fi
if [[ "$ROLE" == edge ]]; then
  .venv/bin/pip install -e '.[edge-languages]'
  clone_pinned https://github.com/ggml-org/llama.cpp vendor/llama.cpp 335b21fcbda972777e4e9e69decad1f719cafeb3
  cmake -S vendor/llama.cpp -B vendor/llama.cpp/build -DGGML_CUDA=OFF \
    -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
    -DLLAMA_BUILD_SERVER=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build vendor/llama.cpp/build --target llama-server -j2
fi
.venv/bin/python -m pytest -q
.venv/bin/python scripts/install_services.py "$ROLE" --no-enable
echo 'Installed without starting. Configure the shared token, SSH and devices before launch.'
