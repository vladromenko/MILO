#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
  echo "usage: ./install.sh {jetson|pi} [--check]"
  echo "  jetson  install the brain on NVIDIA Jetson Orin Nano"
  echo "  pi      install the body on Raspberry Pi 5 + Hailo-10H"
}

case "${1:-}" in
  jetson) ROLE=brain ;;
  pi) ROLE=edge ;;
  *) usage; exit 2 ;;
esac

MODE="${2:-install}"
if [[ "$MODE" != install && "$MODE" != --check ]]; then
  usage
  exit 2
fi

if [[ "$MODE" == --check ]]; then
  [[ -x .venv/bin/python ]] || {
    echo "[BLOCKED] .venv is missing; run ./install.sh ${1}"
    exit 1
  }
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider
  exec .venv/bin/python scripts/preflight.py "$ROLE"
fi

scripts/install_system.sh "$ROLE"
scripts/bootstrap.sh "$ROLE"
if [[ "$ROLE" == edge ]]; then
  scripts/install_edge_agent.sh
fi
.venv/bin/python scripts/preflight.py "$ROLE" --software-only

echo "[READY] MILO ${1} software is installed and verified"
echo "[STOPPED] installation did not start MILO or move the arm"
if [[ "$ROLE" == brain ]]; then
  echo "Next: configure Jetson and pair it with Pi using docs/INSTALLATION.md"
else
  echo "Next: copy shared configuration from Jetson and configure Pi devices"
fi
