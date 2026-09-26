import json
import os
from pathlib import Path
import secrets


def service_prefix(cfg=None):
    value = (settings() if cfg is None else cfg).get('service_prefix', 'milo')
    if value != 'milo':
        raise ValueError('unsupported service prefix')
    return value


def write_private_json(path, value):
    """Replace private configuration atomically, including first creation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

ROOT = Path(__file__).resolve().parents[1]


def settings():
    return json.loads((ROOT / "config/runtime.json").read_text())


def initialize(role):
    for name in ("data", "logs", "config"):
        (ROOT / name).mkdir(exist_ok=True)
    path = ROOT / "config/runtime.json"
    if path.exists():
        return
    config = {
        "role": role, "service_prefix": "milo", "token": secrets.token_urlsafe(48),
        "edge_host": "vlados.local", "edge_user": "vlados",
        "edge_root": "/home/vlados/MILO",
        "serial": "/dev/serial/by-id/usb-Silicon_Labs_CP2104_USB_to_UART_Bridge_Controller_02E0E664-if00-port0",
        "camera": "/dev/video0", "display_output": "HDMI-A-1",
        "display_transform": "90", "input_hint": "UM02", "output_hint": "UACDemo",
        "hailo_model": "/usr/share/hailo-models/yolov8m_h10.hef",
        "face_threshold": 0.55,
        "vlm_enabled": False,
        "visual_observer_enabled": True,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(config, out, indent=2)
