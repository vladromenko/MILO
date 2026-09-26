# Design Evolution

MILO grew through three working systems. Each stage answered a different question
instead of attempting the final architecture at once.

| Stage | Compute model | Interaction | Main engineering result |
| --- | --- | --- | --- |
| `pi_friend_hat-2` | Pi 5 + Hailo, sequential | local voice | proved the offline speech loop |
| `Jetson_Friend` | one Jetson process family | voice, camera, memory, display, arm | proved the embodied companion |
| MILO | Jetson brain + Pi/Hailo body | concurrent multimodal robot + phone operations | isolated I/O, accelerated dialogue, repeatable deployment |

## 1. Raspberry Pi Voice Prototype

[`pi_friend_hat-2`](https://github.com/vladromenko/pi_friend_hat-2) established
that a compact Raspberry Pi 5 and Hailo AI HAT+ 2 could support a completely local
assistant pipeline. Its sequential loop recorded speech, ran whisper.cpp, queried
a local Qwen2.5 1.5B model through `hailo-ollama`, and synthesized Piper audio.

**What it proved:** offline voice interaction and accelerator deployment on a
small computer.

**What limited it:** sequential processing, no embodied perception or arm, and
little separation between hardware ownership and dialogue logic.

The loop also waited for a fixed four-second recording before starting Whisper,
then ran recognition, a non-streamed LLM request, Piper synthesis, and playback
in sequence. In the final system, voice activity detection closes a request 600
ms after speech ends, services remain warm, and completed sentences stream to
speech synthesis. For a one-second phrase, that change alone starts processing
at about 1.6 seconds instead of 4.0 seconds. See
[Performance](PERFORMANCE.md) for the measured and code-derived comparisons.

## 2. Jetson Friend

[`Jetson_Friend`](https://github.com/vladromenko/Jetson_Friend) moved the project
to Jetson and introduced the embodied companion: structured camera state,
behavior events, persistent person memory, display, camera, and arm integration.
It established the principle that deterministic code owns motion while the LLM
handles conversation.

**What it proved:** a coherent social-robot experience on one host.

**What limited it:** one computer owned both high-level reasoning and physical
I/O, so camera, audio, inference, ROS, and recovery competed in the same failure
domain. Deployment history also accumulated around a specific machine.

## 3. Distributed MILO

The current repository divides responsibility by latency and ownership:

| Jetson brain | Raspberry Pi body |
| --- | --- |
| Gemma dialogue and policy | camera and latest-frame workers |
| CUDA Whisper | Hailo object inference |
| person-scoped memory | face, expression, and identity inference |
| web lifecycle and motion decisions | microphone, Piper, speaker, display |
| ROS publisher | exclusive micro-ROS serial owner |

The split reduced contention, isolated hardware failures, and allowed each device
to use its strongest accelerator. Full LLM GPU offload cut median completion time
from 4.858 s to 1.814 s; CUDA STT reduced a warm short phrase from about 2.27 s to
0.24 s. The private network and phone panel turned startup from a developer SSH
procedure into an operator-controlled presentation workflow.

The measured optimization inside the final generation is equally important. The
language server moved from partial to full CUDA offload, reducing median first
token latency from 0.463 s to 0.188 s and complete response latency from 4.858 s
to 1.814 s. Persistent CUDA Whisper reduced a warm short phrase from roughly
2.27 s to 0.24 s. Latest-frame vision workers removed camera queue growth, while
the slower VLM became an on-demand process so it could not hold up ordinary
conversation. See [Performance](PERFORMANCE.md) for methodology and raw data.

## Engineering Lessons

1. **Measure before repartitioning.** Hailo is valuable for continuous object
   detection, while the Jetson GPU is better used for dialogue and speech.
2. **One physical owner per device.** The Pi alone opens camera, audio, display,
   and CP2104; reconnecting tunnels cannot create competing serial owners.
3. **Keep generative output away from actuators.** LLM and VLM results become
   observations or intent, never raw joint commands.
4. **Degrade by capability.** A Hailo timeout must not remove faces, conversation,
   camera preview, or manual operation.
5. **Separate boot from motion.** Power-on creates connectivity; a human starts
   MILO explicitly after observing the workspace.
6. **Treat old repositories as evidence.** The earlier repositories document
   real design progression. `Jetson_Friend` remains a runnable, documented
   single-Jetson release, while this repository carries the distributed system.
