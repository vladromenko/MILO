# Testing and Acceptance

MILO separates deterministic software tests from attended hardware acceptance.
Passing unit tests does not prove that a powered arm has a clear path or correct
mechanical calibration.

## Hardware-Free Suite

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e . pytest
.venv/bin/python -m pytest -q
```

The suite covers:

- voice buffering, echo suppression, and noise gating;
- dialogue, identity, visual, search, and memory-intent routing;
- person-scoped memory and deletion;
- face, expression, and Hailo result parsing;
- lifecycle, authentication, network profile generation, and tunnel behavior;
- motion authority, feedback validation, startup sequence, joystick, and J6 denial;
- Hailo recovery behavior and camera-independent degradation.

CI runs this suite on Python 3.12 without models or robot hardware.

## Read-Only Preflight

On each device, `scripts/preflight.py` checks private configuration, permissions,
model/build paths, and expected devices. It never starts a service or sends a
motion command.

```bash
.venv/bin/python scripts/preflight.py brain
.venv/bin/python scripts/preflight.py edge
```

## Attended Hardware Acceptance

Perform these steps in order and record the commit, firmware image, and result.

1. With arm power off, verify cable routing and controller recovery switch state.
2. Start with `./milo start --no-home`; confirm Jetson, Pi, LLM, STT, camera,
   audio, display, Hailo, and fresh arm feedback in `./milo status --json`.
3. Verify the phone dashboard and annotated video on both portrait and landscape
   layouts.
4. With a person at the power cutoff, request one small J1 manual motion. Confirm
   direction and feedback; repeat once for J3. Do not command J6.
5. Request the startup posture and confirm J4, J3, and J2 complete in sequence.
6. Enable tracking and move slowly left/right, then up/down. Confirm bounded,
   continuous motion and fresh feedback.
7. Test microphone silence, background noise, one English request, playback, and
   interruption. Confirm that MILO does not transcribe its own speaker output.
8. Test current-view description, object detection, horizontal search, sustained
   expression cue, opt-in enrollment, fact recall, and deletion.
9. Disarm and run `.venv/bin/python scripts/check_runtime.py --seconds 60`.
10. Stop through the phone, cold boot both computers without the workstation,
    and repeat the phone startup path.

## Acceptance Record

The reference operating installation completed a 60-second warm observation with
60/60 healthy samples, maximum Pi metadata age of 102.2 ms, maximum arm-feedback
age of 346.3 ms, no motion commands during the disarmed run, and unchanged J6.
These figures describe one measured installation; repeat the test after hardware,
firmware, network, or power changes.
