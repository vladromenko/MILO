"""Read-only soak check. Does not arm, move, enroll, record, or change services."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from milo_next.settings import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 86400:
        parser.error("seconds must be 5..86400")
    request = urllib.request.Request("http://127.0.0.1:8780/status",
        headers={"Authorization": "Bearer " + settings()["token"]})
    started = time.monotonic()
    issues = {}
    maxima = {"edge_age_ms": 0, "feedback_age_ms": 0}
    samples = 0
    initial = None
    previous_callbacks = None
    while time.monotonic() - started < args.seconds:
        problems = []
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                status = json.load(response)
            arm, audio = status["arm"], status.get("audio") or {}
            if initial is None:
                initial = {"commands": arm["published_count"], "j6": arm["raw_pose"].get("J6"),
                           "underflows": audio.get("output_underflows", 0)}
            if arm["state"] != "DISARMED":
                problems.append("arm_not_disarmed")
            if arm["published_count"] != initial["commands"]:
                problems.append("command_counter_changed")
            if arm["raw_pose"].get("J6") != initial["j6"]:
                problems.append("j6_feedback_changed")
            age = arm.get("feedback_age_s")
            if age is None or age > .5:
                problems.append("stale_arm_feedback")
            maxima["feedback_age_ms"] = max(maxima["feedback_age_ms"], (age or 0) * 1000)
            edge_age = status.get("edge_age_ms", 1e6)
            maxima["edge_age_ms"] = max(maxima["edge_age_ms"], edge_age)
            if status.get("edge_error") or edge_age > 300:
                problems.append("edge_link")
            if status.get("perception", {}).get("status", {}).get("state") != "running":
                problems.append("perception")
            if not status.get("llm_ready"):
                problems.append("language_model")
            if not (status.get("display") or {}).get("running"):
                problems.append("display")
            if audio.get("errors") or not all(audio.get(k) for k in ("input_ready", "output_ready", "stt_ready")):
                problems.append("audio")
            callbacks = audio.get("output_callbacks", 0)
            if previous_callbacks is not None and callbacks <= previous_callbacks:
                problems.append("audio_callback_stalled_or_restarted")
            if audio.get("output_underflows", 0) != initial["underflows"]:
                problems.append("audio_underflow")
            previous_callbacks = callbacks
        except Exception as error:
            problems.append(type(error).__name__)
        for problem in problems:
            issues[problem] = issues.get(problem, 0) + 1
        samples += 1
        time.sleep(1)
    result = {"passed": not issues, "seconds": round(time.monotonic() - started, 1),
              "samples": samples, "issues": issues,
              "maxima": {key: round(value, 1) for key, value in maxima.items()},
              "initial": initial}
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
