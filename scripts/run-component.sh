#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=2
case "${1:-}" in
  web)
    exec .venv/bin/python -m milo_next.web_panel ;;
  rpc)
    exec .venv/bin/python -m milo_next.rpc_tunnel ;;
  dds)
    exec .venv/bin/python -m milo_next.dds_tunnel ;;
  agent)
    export LD_LIBRARY_PATH="$ROOT/vendor/agent/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export ROS_DOMAIN_ID=30
    export XRCE_DOMAIN_ID_OVERRIDE=30
    export FASTRTPS_DEFAULT_PROFILES_FILE="$ROOT/config/dds-server.xml"
    exec .venv/bin/python -m milo_next.local_agent ;;
  edge)
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export WAYLAND_DISPLAY=wayland-0
    export SDL_VIDEODRIVER=wayland
    export PYGAME_HIDE_SUPPORT_PROMPT=1
    exec .venv/bin/python -m milo_next.edge ;;
  brain|tunnel)
    set +u
    source /opt/ros/jazzy/setup.bash
    source "$ROOT/ros_ws/install/setup.bash"
    set -u
    export ROS_DOMAIN_ID=30
    export ROS_LOCALHOST_ONLY=1
    if [[ "$1" == brain && -f "$ROOT/config/native-agent.enabled" ]]; then
      export ROS_LOCALHOST_ONLY=0
      export FASTRTPS_DEFAULT_PROFILES_FILE="$ROOT/config/dds-client.xml"
    fi
    exec .venv/bin/python -m "milo_next.$1" ;;
  llm)
    .venv/bin/python scripts/prepare_model_key.py
    MMPROJ=()
    if .venv/bin/python -c 'from milo_next.settings import settings; raise SystemExit(0 if settings().get("vlm_enabled", False) else 1)'; then
      MMPROJ=(--mmproj assets/models/vlm/gemma4/mmproj-gemma-4-E2B-it-Q8_0.gguf --no-mmproj-offload)
    fi
    exec vendor/llama.cpp/build/bin/llama-server \
      -m assets/models/vlm/gemma4/gemma-4-E2B-it-Q4_0.gguf \
      "${MMPROJ[@]}" \
      --api-key-file config/llm.key \
      --host 127.0.0.1 --port 8781 -ngl "${MILO_LLM_GPU_LAYERS:-99}" -c 2048 -np 1 -t 3 -b 128 -ub 64 \
      --reasoning off ;;
  stt)
    STT_BIN=vendor/whisper.cpp/build/bin/whisper-server
    if [[ "${MILO_STT_BACKEND:-cuda}" == cuda && -x vendor/whisper.cpp/build-cuda-final/bin/whisper-server ]]; then
      STT_BIN=vendor/whisper.cpp/build-cuda-final/bin/whisper-server
    fi
    exec "$STT_BIN" \
      -m assets/models/whisper/ggml-base.bin --host 127.0.0.1 --port 8783 -t 3 -l en -nlp ;;
  vlm)
    exec vendor/llama.cpp/build/bin/llama-server \
      -m assets/models/vlm/smolvlm/SmolVLM2-500M-Video-Instruct-Q8_0.gguf \
      --mmproj assets/models/vlm/smolvlm/mmproj-SmolVLM2-500M-Video-Instruct-Q8_0.gguf \
      --host 127.0.0.1 --port 8785 -ngl 0 -c 2048 -np 1 -t 2 -b 128 -ub 64 ;;
  *) echo "component required: edge|brain|tunnel|llm|stt" >&2; exit 2 ;;
esac
