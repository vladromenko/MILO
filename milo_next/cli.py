import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .settings import ROOT, settings, service_prefix
from .tunnel import ssh_args


def rpc(path, body=None):
    cfg = settings()
    request = urllib.request.Request("http://127.0.0.1:8780" + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": "Bearer " + cfg["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=125) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description="MILO operator control")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("stop", "arm", "disarm", "estop", "reset", "home", "logs", "observe", "web-code", "wifi"):
        sub.add_parser(command)
    for command in ('start', 'restart'):
        start = sub.add_parser(command)
        start.add_argument('--no-home', action='store_true', help='Start conversation and video without moving the arm')
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    chat = sub.add_parser("chat")
    chat.add_argument("text")
    jog = sub.add_parser("jog")
    jog.add_argument("joint", choices=["J1", "J3"])
    jog.add_argument("delta", type=int, choices=[-1, 1])
    enroll = sub.add_parser("enroll")
    enroll.add_argument("name")
    remember = sub.add_parser("remember")
    for key in ("person_id", "key", "value"):
        remember.add_argument(key)
    forget = sub.add_parser("forget")
    forget.add_argument("person_id")
    edge = sub.add_parser("edge")
    edge.add_argument("action", choices=["status", "restart", "stop", "start"])
    args = parser.parse_args()
    try:
        prefix = service_prefix()
        if args.command == 'wifi':
            network = json.loads((ROOT / 'config/network.json').read_text())
            print('SSID: MILO-NET')
            print('Password:', network['password'])
            print('Phone: http://10.42.0.1/')
        elif args.command == 'web-code':
            from .web_panel import access_code
            print(access_code())
        elif args.command in {"start", "stop", "restart"}:
            if args.command in {"stop", "restart"}:
                try:
                    rpc("/command", {"action": "disarm"})
                except Exception:
                    pass
            subprocess.run(["systemctl", "--user", args.command, prefix + ".target"], check=True)
            if args.command == 'stop':
                subprocess.run(ssh_args() + [f'systemctl --user stop {prefix}.target {prefix}-edge.service {prefix}-agent.service {prefix}-vlm.service'], check=True)
            elif not args.no_home:
                deadline = time.monotonic() + 90
                while True:
                    try:
                        state = rpc('/status')['arm']
                        if state.get('raw_pose') and (not state.get('firmware') or state['firmware']['fresh']):
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Services started, but arm feedback is unavailable. No startup movement sent.')
                    time.sleep(.5)
                rpc('/command', {'action': 'home'})
                rpc('/command', {'action': 'arm'})
                print('Startup posture complete: J4=0, J3=75, J2=115 (+/-2 deg). J1/J5/J6 unchanged. Face tracking enabled.')
        elif args.command == "edge":
            subprocess.run(ssh_args() + [f"systemctl --user {args.action} {prefix}-edge.service"], check=True)
        elif args.command == "logs":
            subprocess.run(["journalctl", "-b", "-n", "80", "--no-pager"] +
                [f'_SYSTEMD_USER_UNIT={prefix}-{part}.service' for part in
                 ('brain','edge','web','launch','stop','llm','stt','rpc','dds','agent','vlm')], check=True)
        elif args.command == "observe":
            import cv2
            cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
            try:
                ok, frame = False, None
                for _ in range(12):
                    ok, frame = cap.read()
                    if not ok:
                        break
                if not ok:
                    raise RuntimeError("observer camera unavailable")
                path = ROOT / "data/observer.jpg"
                if not cv2.imwrite(str(path), frame):
                    raise RuntimeError("snapshot write failed")
                print(path)
            finally:
                cap.release()
        else:
            body = vars(args).copy()
            body["action"] = body.pop("command")
            result = rpc("/status") if args.command == "status" else rpc("/command", body)
            if args.command == "status" and not args.json:
                audio = result.get("audio") or {}
                perception = result.get("perception") or {}
                arm = result["arm"]
                print("MILO")
                print("Language model:", "ready" if result.get("llm_ready") else "unavailable/loading")
                print("Speech recognition:", "ready" if result.get("stt_ready") else "unavailable/loading")
                print("Edge:", result.get("edge_error") or "connected")
                print("Perception:", perception.get("status", {}).get("state", "unavailable"),
                      "; faces:", len(perception.get("faces", [])))
                print("Microphone:", audio.get("input_device") if audio.get("input_ready") else "unavailable")
                print("Speaker:", audio.get("output_device") if audio.get("output_ready") else "unavailable")
                print("Display:", "running" if (result.get("display") or {}).get("running") else "unavailable")
                print("Arm:", arm["state"], "; reason:", (arm.get('firmware') or {}).get('error') or arm.get("error") or arm["reason"],
                      "; emitted commands:", arm["published_count"])
                print("Stable arm feedback:", arm["feedback_stable"], "; calibrated pose:", arm.get("pose", {}))
                print("Raw pose:", arm.get("raw_pose", {}))
                print('Startup posture:', result.get('home', {}))
                print('Object detection:', perception.get('status', {}).get('objects', {}))
                if audio.get("errors"):
                    print("Audio errors:", audio["errors"])
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode(), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
