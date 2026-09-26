# Architecture

## Design Goals

MILO is designed for offline operation, bounded resource use, explicit motion
authority, and recoverable failures. Generative models may describe or converse,
but they never publish motor commands. Motion comes only from deterministic
tracking, search, startup, or authenticated operator paths.

## Host Responsibilities

### Jetson: brain

| Service | Responsibility |
| --- | --- |
| `milo-llm` | Gemma 4 E2B Q4 through `llama-server`, CUDA offload |
| `milo-stt` | Whisper base through `whisper-server`, CUDA preferred |
| `milo-brain` | Dialogue routing, memory, behavior, identity, motion policy |
| `milo-rpc` | Authenticated edge HTTP and reverse STT forwarding over SSH |
| `milo-dds` | Loopback-only Fast DDS TCP forwarding over SSH |
| `milo-web` | Authenticated mobile panel; the only application service enabled at boot |

### Raspberry Pi: edge/body

| Service | Responsibility |
| --- | --- |
| `milo-edge` | Camera, perception, audio, display, and edge HTTP API |
| `milo-agent` | Exclusive CP2104 owner and bundled ROS-aware micro-ROS agent |
| `milo-vlm` | On-demand SmolVLM2 scene analysis, isolated from ordinary dialogue |

## Voice Request Flow

1. The Pi microphone callback produces fixed 20 ms mono frames.
2. WebRTC speech classification and energy segmentation reject silence and common
   non-speech noise. Echo-time frames are discarded while MILO is speaking.
3. After 600 ms of silence, the Pi sends one bounded WAV through the reverse SSH
   tunnel to Whisper on Jetson.
4. `brain.py` routes explicit memory, stop, search, and visual intents before
   ordinary dialogue.
5. Relevant identity-scoped memory and current observations are passed to Gemma
   as untrusted background. The current request remains the highest-priority input.
6. Generated sentences are streamed to persistent Piper synthesis on the Pi.
7. A fixed-period PortAudio callback drains a bounded PCM ring into the selected
   USB speaker.

## Vision Flow

The Pi owns one V4L2 camera capture thread. Latest-frame workers prevent inference
backlogs:

- YuNet detects faces at approximately 15 Hz.
- SFace refreshes identity embeddings at most twice per second per tracked face.
- MobileFaceNet estimates expression cues at approximately 2 Hz.
- Hailo-10H runs COCO object detection at approximately 5 Hz.

Regular polling sends metadata, not continuous video, to Jetson. A phone preview
requests bounded JPEG snapshots. Explicit scene questions keep at most three
reduced JPEG frames in RAM, submit them to the independent Pi VLM, and then clear
the buffer. A question such as "What do you see?" uses one current frame and sends
no arm command. Only explicit "look around" or search requests start a horizontal
J1 sweep.

## Motion Flow

```text
brain intent / operator API
        -> OperatorMotion, ObjectSearch, or face tracker
        -> Actuator policy and feedback validation
        -> ROS 2 arm command
        -> Fast DDS TCP on Jetson loopback
        -> SSH tunnel
        -> Fast DDS TCP on Pi loopback
        -> native micro-ROS agent
        -> CP2104 at 2,000,000 baud
        -> STM32 V4 controller
        -> servo bus and joint feedback
```

The LLM and VLM have no reference to the ROS publisher. MILO commands J1-J5
through bounded interfaces and never commands J6. The startup sequence requests
J4=0, J3=75, and J2=115, then enables J1/J3 face tracking. J1 and J5 remain at
their current position during startup.

## Network Topology

```text
Phone                 Jetson                       Raspberry Pi
10.42.0.x  <Wi-Fi>   10.42.0.1  <MILO-NET>       10.42.0.2
                         |
Mac 10.43.0.2 <Ethernet> 10.43.0.1
```

- `10.42.0.0/24` is the private robot and phone network.
- `10.43.0.0/24` is the wired administration network.
- Neither network installs a default route on the Mac or Pi.
- Edge APIs bind to Pi loopback and are reachable only through pinned-key SSH.
- HTTP APIs also require a shared random bearer token.
- The phone interface requires an access code, CSRF token, same-origin requests,
  and a strict session cookie.

## Startup Model

Power-on creates the private network and starts the phone panel. The robot target
remains disabled. Pressing **Start MILO** launches a systemd oneshot which starts
both runtime targets, waits for fresh arm feedback, requests the startup posture,
and explicitly arms face tracking. Closing the browser does not cancel that job.
Pressing **Stop MILO** cancels a pending launch, disarms motion, and stops both
runtime targets while keeping the network and phone panel available.

## State and Storage

- `config/runtime.json`: private shared token and device configuration, mode 0600
- `config/network.json`: private Wi-Fi password and interface names, mode 0600
- `data/memory.sqlite3`: person-scoped memory, WAL enabled
- `data/ESTOP`: persistent stop latch when present
- `logs/`: service-owned runtime logs; normal diagnostics use journalctl

These paths are excluded from Git. Models and third-party builds are recreated
from pinned revisions and SHA-256 manifests.

## Failure Boundaries

- Pi and Jetson services restart independently under systemd.
- RPC and DDS tunnels reconnect independently.
- Camera/Hailo failure restarts edge ownership cleanly rather than leaving stale
  native handles.
- USB audio hotplug triggers edge re-enumeration through a supervised restart.
- Stale camera or arm feedback removes motion permission.
- Hailo has a bounded recovery service and does not reboot the Pi in a loop.
- Ordinary dialogue remains available while the slower VLM is analyzing a frame.

See [Testing](TESTING.md) for the distinction between software tests and attended
hardware acceptance.
