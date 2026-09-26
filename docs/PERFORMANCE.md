# Performance

## Method

Measurements were taken on the hardware in [Hardware](HARDWARE.md), after model
warm-up, using local requests and the benchmark scripts in `scripts/`. Raw LLM
runs are retained in [`benchmarks/`](../benchmarks/). Results are medians unless
otherwise stated. They are component measurements, not a guaranteed end-to-end
voice latency.

## Results

| Component | Baseline | Optimized | Change |
| --- | ---: | ---: | ---: |
| LLM time to first token | 0.463 s | 0.188 s | 59.4% lower, 2.46x faster |
| LLM first complete sentence | 1.518 s | 0.815 s | 46.3% lower, 1.86x faster |
| LLM complete response | 4.858 s | 1.814 s | 62.7% lower, 2.68x faster |
| Warm short-phrase STT | 2.27 s CPU | 0.24 s CUDA | about 9.5x faster |
| Hailo YOLO inference | not offloaded | about 26 ms | dedicated edge inference |
| YuNet face detection | — | about 9 ms | CPU on Pi |
| SFace embedding | — | about 21 ms | rate-limited CPU on Pi |
| One-frame VLM | 32.2 s | 26-29 s | isolated slow path |

The initial LLM ran with 24 GPU layers. Full CUDA offload reduced both first-token
and completion latency. The conversational measurement includes the current
short-response prompt and sentence streaming. Whisper moved from CPU to a
persistent CUDA server; its first request remains slower than warm requests.

## Comparison With The First Prototype

The first `pi_friend_hat-2` repository does not contain timestamped end-to-end
benchmark runs, so it would be misleading to publish a single measured speedup
for the whole conversation loop. Its source still supports a useful structural
comparison:

| Stage | Audio capture | STT and TTS | LLM delivery | Concurrency |
| --- | --- | --- | --- | --- |
| Raspberry Pi prototype | fixed 4.0 s block | new command per turn | complete, non-streamed response | sequential turn |
| Distributed MILO | speech duration + 0.6 s silence | persistent services | sentence streaming | concurrent bounded workers |

For a one-second spoken request, the prototype completed capture at 4.0 seconds;
MILO can complete it at about 1.6 seconds. Processing begins 2.4 seconds earlier,
a 60% reduction in capture-stage delay. For a two-second request, it begins
1.4 seconds earlier, a 35% reduction. These are calculations from the checked-in
capture algorithms, not hardware benchmark samples, and exclude STT, LLM, TTS,
audio-device, and network time.

The measured figures above begin with the retained Stage 3 baseline rather than
the first Raspberry Pi prototype. They show a further 9.5x improvement in warm
short-phrase STT, a 1.86x improvement to the first complete LLM sentence, and a
2.68x improvement to complete LLM generation. Sentence streaming matters to
perceived latency because Piper can start speaking while later text is still
being generated.

## Scheduling Strategy

- Ordinary speech uses persistent Jetson LLM and STT services.
- Completed LLM sentences stream immediately to persistent Pi TTS.
- Camera workers consume only the latest frame, preventing inference queues.
- Faces run near 15 Hz, objects near 5 Hz, and expression/identity near 2 Hz.
- The Pi VLM runs independently with CPU and memory limits. During a measured
  VLM request, ordinary LLM responses still completed in 2.10-2.64 seconds.
- Motion, audio, and phone endpoints remain independent of Hailo availability.

## Reproduction

With MILO stopped or disarmed as required by each script:

```bash
.venv/bin/python scripts/bench_english.py
.venv/bin/python scripts/bench_voice.py
.venv/bin/python scripts/bench_acoustic.py
.venv/bin/python scripts/check_runtime.py --seconds 60
```

Record power mode, clocks, model hashes, temperature, warm-up count, prompt set,
and Git commit with every result. Do not compare one-frame VLM latency with LLM
text latency; they are different models and workloads.
