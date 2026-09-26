# Operation

## Presentation Startup

1. Clear the arm path and inspect cable slack.
2. Power Jetson, Pi, display, and arm controller.
3. Connect the phone to `MILO-NET` and retain the network without internet.
4. Open `http://10.42.0.1/` and enter the access code.
5. Confirm camera, microphone, speaker, Hailo, Pi, and arm feedback are ready.
6. Press **Start MILO**. The startup sequence requests J4=0, J3=75, then J2=115;
   J1, J5, and J6 are unchanged. Face tracking is enabled after completion.

The **Stop MILO** button stops the robot services on both computers while leaving
the private network and phone panel available.

## Jetson Commands

```bash
cd /home/vlad/MILO
./milo status
./milo status --json
./milo start
./milo start --no-home
./milo stop
./milo restart --no-home
./milo home
./milo arm
./milo disarm
./milo estop
./milo reset
./milo logs
./milo wifi
./milo web-code
./milo chat 'What do you see?'
./milo enroll 'Name'
```

`start --no-home` is the diagnostic start: it starts speech, display, camera, and
perception without requesting a posture. `disarm` stops new autonomous movement.
`estop` stores a persistent software latch. Investigate the physical cause before
using `reset`; never clear it in a retry loop.

## Phone Controls

- **Dashboard:** lifecycle and component health.
- **Camera:** annotated preview, tracking, startup posture, and J1-J5 manual input.
- **Audio:** language, microphone, speaker, and listening controls.
- **Memory:** local enrolled identities and explicitly remembered facts.

Language changes only through the phone settings. Supported configurations are
English, Russian, French, German, Japanese, Arabic, and Urdu. Recognition does
not automatically switch the selected language.

Manual control and tracking share one motion authority. Enter manual mode before
moving J2, J4, or J5; autonomous tracking pauses while the operator controls the
arm. J6 feedback may be displayed but MILO never commands it.

## Dialogue and Perception

Ordinary dialogue stays on the fast Jetson LLM path. “What do you see?” describes
the current view without moving. Explicit requests such as “look around” or
“find a cup” start a horizontal J1 search from the current pose. Hailo provides
object detections; the LLM generates the spoken response. A separate Pi VLM is
used only for open-ended visual questions and may take tens of seconds.

Face expression output is an uncertain social cue, not an emotion diagnosis.
MILO may acknowledge sustained positive or negative-looking expressions, with
cooldowns to avoid repetition. Face enrollment and personal facts require an
explicit request and remain local in SQLite.

## Shutdown

```bash
ssh milo-jetson '/home/vlad/MILO/milo stop'
ssh -t milo-pi 'sudo /sbin/poweroff'
ssh -t milo-jetson 'sudo /sbin/poweroff'
```

Wait for both operating systems to halt before removing power.
