#!/usr/bin/env python3
"""Read-only installation preflight. It never starts services or sends motion commands."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(condition, name, detail, results):
    results.append({'check': name, 'passed': bool(condition), 'detail': detail})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=['brain', 'edge'])
    parser.add_argument('--software-only', action='store_true')
    args = parser.parse_args()
    results = []
    runtime_path = ROOT / 'config/runtime.json'
    network_path = ROOT / 'config/network.json'
    check(runtime_path.is_file(), 'runtime_config', str(runtime_path), results)
    if not args.software_only:
        check(network_path.is_file(), 'network_config', str(network_path), results)
    if not runtime_path.is_file():
        print(json.dumps({'passed': False, 'checks': results}, indent=2))
        return 1
    cfg = json.loads(runtime_path.read_text())
    check(cfg.get('role') == args.role, 'role', cfg.get('role'), results)
    check(cfg.get('service_prefix') == 'milo', 'service_prefix', cfg.get('service_prefix'), results)
    check(runtime_path.stat().st_mode & 0o077 == 0, 'runtime_permissions', 'expected 0600', results)
    check(isinstance(cfg.get('token'), str) and len(cfg['token']) >= 48,
          'shared_token', 'present and not printed', results)

    if args.role == 'brain':
        paths = [
            ROOT / 'vendor/llama.cpp/build/bin/llama-server',
            ROOT / 'assets/models/vlm/gemma4/gemma-4-E2B-it-Q4_0.gguf',
            ROOT / 'assets/models/whisper/ggml-base.bin',
            Path('/opt/ros/jazzy/setup.bash'),
            ROOT / 'ros_ws/install/setup.bash',
        ]
        if not args.software_only:
            paths.extend((ROOT / 'config/edge_key', ROOT / 'config/known_hosts'))
        for path in paths:
            check(path.exists(), 'brain_path', str(path), results)
        check(shutil.which('ssh') is not None, 'ssh', 'required for Pi tunnels', results)
    else:
        paths = [
            Path(cfg.get('hailo_model', '')),
            ROOT / 'vendor/agent/check-libs',
            ROOT / 'assets/models/vision/face_detection_yunet.onnx',
            ROOT / 'assets/models/vision/face_recognition_sface.onnx',
            ROOT / 'assets/models/vision/facial_expression_mobilefacenet.onnx',
        ]
        if not args.software_only:
            paths[0:0] = (Path(cfg.get('camera', '')), Path(cfg.get('serial', '')))
        for path in paths:
            check(path.exists(), 'edge_path', str(path), results)
        if not args.software_only:
            cards = Path('/proc/asound/cards').read_text() if Path('/proc/asound/cards').exists() else ''
            check(cfg.get('input_hint', '').casefold() in cards.casefold(),
                  'microphone', cfg.get('input_hint'), results)
            check(cfg.get('output_hint', '').casefold() in cards.casefold(),
                  'speaker', cfg.get('output_hint'), results)
            check(Path('/dev/hailo0').exists() or Path('/dev/hailo1x').exists(),
                  'hailo_device', 'expected /dev/hailo0 or /dev/hailo1x', results)

    passed = all(item['passed'] for item in results)
    print(json.dumps({'passed': passed, 'role': args.role, 'checks': results}, indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
