"""Optional, persistent USB audio for Python 3.12+.

Start ``run(on_utterance, on_state)`` as a task and await ``ready.wait()``.
Callbacks receive text/state strings and may be synchronous or asynchronous;
synchronous callbacks must be quick. ``say`` accepts one sentence (<=500 chars),
backpressures at eight queued sentences, and raises when output is unavailable.
``drain`` waits for accepted playback; ``close`` discards pending work. Instances
are single-use. Inspect ``status`` for device names, errors and timing counters.

There are at most 12 seconds of segmented recording and one second of pending
microphone frames. STT is serial: while it runs, only the newest second survives.
Numerical conversion, device operations and Piper run off the asyncio thread.
Native synthesis cannot be forcibly cancelled; shutdown joins an active native
call before releasing its resources. No subprocesses or remote shells are used.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import importlib
import inspect
import io
import json
import logging
import math
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit
import wave
from pathlib import Path
from milo_next.languages import LANGUAGES


_LOG = logging.getLogger(__name__)
FRAME_SECONDS = 0.02
CAPTURE_FRAMES = 50
SPEECH_QUEUE_SIZE = 8
MAX_SENTENCE_CHARS = 500
ECHO_TAIL_SECONDS = 0.35


@dataclass
class AudioStatus:
    state: str = "idle"
    input_device: str | None = None
    output_device: str | None = None
    input_rate: int = 0
    output_rate: int = 0
    input_ready: bool = False
    output_ready: bool = False
    stt_ready: bool = False
    speaking: bool = False
    errors: dict[str, str] = field(default_factory=dict)
    stt_ms: float = 0.0
    tts_ms: float = 0.0
    stt_total_ms: float = 0.0
    tts_total_ms: float = 0.0
    stt_requests: int = 0
    rejected_transcriptions: int = 0
    speech_filter: str | None = None
    tts_sentences: int = 0
    capture_dropped: int = 0  # 20 ms frames, including intentional echo drops
    capture_overflows: int = 0
    speech_dropped: int = 0
    output_underflows: int = 0
    output_callbacks: int = 0
    output_callback_at: float = 0.0
    response_pcm_ms: float | None = None
    tts_first_pcm_ms: float | None = None

    @property
    def health(self) -> str:
        if self.state == "closed":
            return "closed"
        if self.errors:
            return "degraded"
        if not (self.input_ready and self.output_ready and self.stt_ready):
            return "starting" if self.state != "idle" else "idle"
        return "ok"


class NewestBuffer:
    """Thread-safe bounded handoff without one asyncio callback per frame."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._items: deque = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def put(self, item: Any) -> bool:
        with self._lock:
            dropped = len(self._items) == self._items.maxlen
            self._items.append(item)
            return dropped

    def get(self) -> Any | None:
        with self._lock:
            return self._items.popleft() if self._items else None

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            return count

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class PlaybackBuffer:
    """Fixed PCM ring: producer backpressures; the device always gets silence or PCM."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._data = bytearray(capacity)
        self._view = memoryview(self._data)
        self._silence = memoryview(bytes(capacity))
        self._read = self._write = self._size = 0
        self._condition = threading.Condition()
        self._progress = time.monotonic()

    def fill(self, output) -> int:
        output = memoryview(output).cast("B")
        with self._condition:
            size = min(len(output), self._size)
            first = min(size, len(self._data) - self._read)
            output[:first] = self._view[self._read:self._read + first]
            output[first:size] = self._view[:size - first]
            # The device callback uses a fixed 20 ms block, smaller than capacity.
            output[size:] = self._silence[:len(output) - size]
            self._read = (self._read + size) % len(self._data)
            self._size -= size
            self._progress = time.monotonic()
            self._condition.notify_all()
            return size

    def _wait(self, stop) -> bool:
        if stop.is_set():
            return False
        if time.monotonic() - self._progress > 2:
            raise RuntimeError("speaker callback stopped")
        self._condition.wait(0.02)
        return not stop.is_set()

    def put(self, data, stop) -> None:
        data = memoryview(data).cast("B")
        offset = 0
        with self._condition:
            while offset < len(data) and not stop.is_set():
                if self._size == len(self._data):
                    if not self._wait(stop):
                        return
                    continue
                size = min(len(data) - offset, len(self._data) - self._size,
                           len(self._data) - self._write)
                self._view[self._write:self._write + size] = data[offset:offset + size]
                self._write = (self._write + size) % len(self._data)
                self._size += size
                offset += size

    def drain(self, stop) -> None:
        with self._condition:
            while self._size:
                if not self._wait(stop):
                    return


class VoiceActivityDetector:
    """Energy segmentation with an optional speech classifier for 20 ms mono frames.

    An utterance requires 200 ms of voiced frames, ends after 600 ms of silence,
    includes 300 ms of pre-roll, and is capped at 12 seconds including padding.
    The threshold is an RMS amplitude in normalized [-1, 1] samples. Energy VAD
    rejects silence/short clicks; production additionally uses WebRTC speech classification.
    """

    def __init__(self, sample_rate: int, threshold: float = 0.012, is_speech=None):
        if sample_rate < 1000 or not math.isfinite(threshold) or threshold <= 0:
            raise ValueError("invalid VAD configuration")
        self.frame_samples = round(sample_rate * FRAME_SECONDS)
        self.threshold = threshold
        self.is_speech = is_speech
        self._pre: deque = deque(maxlen=15)
        self._frames: list = []
        self._voiced = 0
        self._quiet = 0

    @property
    def buffered_frames(self) -> int:
        return len(self._pre) + len(self._frames)

    def reset(self) -> None:
        self._pre.clear()
        self._frames = []
        self._voiced = self._quiet = 0

    def feed(self, samples: Any) -> list | None:
        if len(samples) != self.frame_samples:
            self.reset()
            raise ValueError("VAD requires exactly one 20 ms frame")
        # Subtract DC so a constant offset is not mistaken for speech.
        mean = sum(float(x) for x in samples) / len(samples)
        energy = sum((float(x) - mean) ** 2 for x in samples) / len(samples)
        # Run on quiet frames too so the speech classifier can adapt to background noise.
        speech = self.is_speech(samples) if self.is_speech else True
        voiced = speech and math.isfinite(energy) and energy >= self.threshold**2
        if not self._frames:
            if not voiced:
                self._pre.append(samples)
                return None
            self._frames = list(self._pre)
            self._pre.clear()
        self._frames.append(samples)
        self._voiced += int(voiced)
        self._quiet = 0 if voiced else self._quiet + 1
        if self._quiet >= 30 or len(self._frames) >= 600:
            result = self._frames if self._voiced >= 10 else None
            self.reset()
            return result
        return None


def select_device(devices: Any, hint: str, direction: str) -> tuple[int, Any]:
    """Require one matching, capable device; never fall back to system default."""
    key = f"max_{direction}_channels"
    hint = hint.strip().casefold()
    if not hint or direction not in {"input", "output"}:
        raise ValueError("a nonempty device hint and valid direction are required")
    matches = [(i, d) for i, d in enumerate(devices)
               if d.get(key, 0) > 0 and hint in d["name"].casefold()]
    exact = [(i, d) for i, d in matches if d["name"].casefold() == hint]
    matches = exact or matches
    if len(matches) != 1:
        names = ", ".join(d["name"] for _, d in matches) or "none"
        raise RuntimeError(f"{direction} device {hint!r}: expected one match, found {names}")
    return matches[0]


def clean_transcription(payload: Any) -> str:
    """Reject empty/non-speech results and low-confidence metadata if provided."""
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        return ""

    def unreliable(item: dict) -> bool:
        for key, limit, above in (("no_speech_prob", 0.6, True),
                                  ("avg_logprob", -1.0, False),
                                  ("compression_ratio", 2.4, True)):
            value = item.get(key)
            if isinstance(value, (float, int)) and (
                not math.isfinite(value) or (value >= limit if above else value < limit)
            ):
                return True
        return False

    if unreliable(payload):
        return ""
    segments = payload.get("segments")
    if isinstance(segments, list) and segments and all(
        isinstance(s, dict) and unreliable(s) for s in segments
    ):
        return ""
    source = payload['text']
    if isinstance(segments, list) and segments and all(
        isinstance(s, dict) and isinstance(s.get('text'), str) for s in segments
    ):
        source = ' '.join(s['text'] for s in segments if not unreliable(s))
    text = re.sub(r"<\|[^>]*\|>", "", source)
    annotation = (r"blank_audio|silence|music|applause|noise|inaudible|unintelligible|"
                  r"(?:people |background )?(?:chattering|talking)|"
                  r"speaking (?:in (?:a )?)?(?:foreign|another|unknown) language|"
                  r"laughter|laughing|coughing|sighs?|breathing")
    text = re.sub(r"\[\s*(?:" + annotation + r")\s*\]|\(\s*(?:" + annotation + r")\s*\)",
                  "", text, flags=re.IGNORECASE)
    text = " ".join(text.split())
    return text if len(text) <= 4096 and any(c.isalnum() for c in text) else ""


class AudioPipeline:
    def __init__(self, stt_url: str, piper_model: str,
                 input_hint: str = "UM02", output_hint: str = "UACDemo"):
        url = urlsplit(stt_url)
        if url.scheme not in {"http", "https"} or not url.netloc or url.query or url.fragment:
            raise ValueError("stt_url must be an HTTP(S) base URL or /inference URL")
        self.stt_url = stt_url.rstrip("/")
        if not self.stt_url.endswith("/inference"):
            self.stt_url += "/inference"
        self.piper_model = str(piper_model)
        self.language = 'en'
        self._response_endpoint_at = None
        self._synthesis_started_at = None
        self.input_hint, self.output_hint = input_hint, output_hint
        self.status = AudioStatus()
        self.ready = asyncio.Event()
        self._stop = threading.Event()
        self._stopped = asyncio.Event()
        self._echo = threading.Event()
        self._echo_until = 0.0
        self._generation = 0
        self._sequence = 0
        self._capture = NewestBuffer(CAPTURE_FRAMES)
        self._speech: asyncio.Queue[str] = asyncio.Queue(SPEECH_QUEUE_SIZE)
        self._workers: list[asyncio.Task] = []
        self._run_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._input = self._output = self._voice = self._session = None
        self._np = self._resample_poly = self._sd = self._aiohttp = None
        self._output_channels = 1
        self._playback = None
        self._on_utterance: Callable | None = None
        self._on_state: Callable | None = None
        self._transcribing = False

    async def _blocking(self, func: Callable, *args: Any) -> Any:
        # Shield then join: cancelling to_thread alone leaves native work running
        # against streams which shutdown might already have closed.
        task = asyncio.create_task(asyncio.to_thread(func, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                _LOG.exception("Audio worker failed during cancellation")
            raise

    def _error(self, component: str, error: Exception) -> None:
        self.status.errors[component] = str(error) or type(error).__name__
        _LOG.warning("Audio %s: %s", component, error)

    async def _callback(self, callback: Callable | None, value: str) -> None:
        if callback is None:
            return
        try:
            result = callback(value)
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            self._error("callback", error)

    async def _state(self, force: str | None = None) -> None:
        if self.status.state == "closed":
            return
        state = force or ("speaking" if self.status.speaking else
                          "transcribing" if self._transcribing else
                          "degraded" if self.status.errors else "listening")
        if self.status.state != state:
            self.status.state = state
            await self._callback(self._on_state, state)

    def _initialize(self) -> None:
        try:
            self._aiohttp = importlib.import_module("aiohttp")
        except Exception as error:
            self._error("stt", error)
        try:
            self._np = importlib.import_module("numpy")
            self._resample_poly = importlib.import_module("scipy.signal").resample_poly
            self._sd = importlib.import_module("sounddevice")
            devices = self._sd.query_devices()
        except Exception as error:
            self._error("devices", error)
            return
        if self._stop.is_set():
            return
        try:
            self._speech_vad = importlib.import_module('webrtcvad').Vad(2)
            self.status.speech_filter = 'webrtcvad_mode_2'
            index, device = select_device(devices, self.input_hint, "input")
            rate = int(device["default_samplerate"])
            if not 8000 <= rate <= 192000:
                raise ValueError(f"unsupported input sample rate: {rate}")
            self._sd.check_input_settings(device=index, channels=1, dtype="float32", samplerate=rate)
            self.status.input_rate = rate
            self._input = self._sd.InputStream(
                device=index, channels=1, dtype="float32", samplerate=rate,
                blocksize=round(rate * FRAME_SECONDS), callback=self._audio_callback,
            )
            self._input.start()
            self.status.input_device = device["name"]
            self.status.input_ready = True
        except Exception as error:
            self._error("input", error)
        if self._stop.is_set():
            return
        try:
            index, device = select_device(devices, self.output_hint, "output")
            voice_type = importlib.import_module("piper").PiperVoice
            self._voice = voice_type.load(self.piper_model)
            self._voices = {'en': self._voice}
            rate = int(self._voice.config.sample_rate)
            native_rate = int(device["default_samplerate"])
            selected = None
            for candidate in dict.fromkeys((rate, native_rate)):
                for channels in (1, 2):
                    if channels > device["max_output_channels"]:
                        continue
                    try:
                        self._sd.check_output_settings(
                            device=index, channels=channels, dtype="int16", samplerate=candidate)
                        selected = (candidate, channels)
                        break
                    except Exception:
                        continue
                if selected:
                    break
            if selected is None:
                raise RuntimeError("matched output device supports no usable PCM format")
            self.status.output_rate, self._output_channels = selected
            self._playback = PlaybackBuffer(self.status.output_rate * self._output_channels * 2 * 2)
            self._output = self._sd.RawOutputStream(
                device=index, channels=self._output_channels, dtype="int16",
                samplerate=self.status.output_rate,
                blocksize=round(self.status.output_rate * FRAME_SECONDS), latency=0.1,
                callback=self._output_callback,
            )
            self._output.start()
            self.status.output_device = device["name"]
            self.status.output_ready = True
        except Exception as error:
            self._error("output", error)

    def _output_callback(self, outdata, frames, timing, flags):
        if flags:
            self.status.output_underflows += 1
        copied = self._playback.fill(outdata)
        self.status.output_callbacks += 1
        self.status.output_callback_at = time.monotonic()
        if copied:
            now = self.status.output_callback_at
            if self._synthesis_started_at is not None:
                self.status.tts_first_pcm_ms = round((now - self._synthesis_started_at) * 1000, 1)
                self._synthesis_started_at = None
            if self._response_endpoint_at is not None:
                self.status.response_pcm_ms = round((now - self._response_endpoint_at) * 1000, 1)
                self._response_endpoint_at = None

    def _audio_callback(self, indata: Any, frames: int, timing: Any, flags: Any) -> None:
        self._sequence += 1
        generation = self._generation
        now = time.monotonic()
        if flags:
            self.status.capture_overflows += 1
            self._sequence += 1  # Force VAD reset across a hardware gap.
        if (self._stop.is_set() or self._echo.is_set() or now < self._echo_until
                or frames != round(self.status.input_rate * FRAME_SECONDS)):
            self.status.capture_dropped += 1
            return
        samples = indata[:, 0].copy()
        if self._capture.put((self._sequence, generation, now, samples)):
            self.status.capture_dropped += 1

    def _encode_pcm(self, frames: list) -> bytes:
        np = self._np
        samples = np.concatenate(frames).astype(np.float32, copy=False)
        if self.status.input_rate != 16000:
            factor = math.gcd(self.status.input_rate, 16000)
            samples = self._resample_poly(samples, 16000 // factor,
                                          self.status.input_rate // factor)
        samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
        return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()

    def _speech_frame(self, samples) -> bool:
        return self._speech_vad.is_speech(self._encode_pcm([samples]), 16000)

    def _encode_wav(self, frames: list) -> bytes:
        pcm = self._encode_pcm(frames)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        return output.getvalue()

    async def _transcribe(self, wav: bytes) -> str:
        if self._session is None:
            return ""
        started = time.perf_counter()
        self.status.stt_requests += 1
        try:
            form = self._aiohttp.FormData()
            form.add_field("file", wav, filename="utterance.wav", content_type="audio/wav")
            form.add_field("response_format", "verbose_json")
            form.add_field("language", getattr(self, 'language', 'en'))
            async with self._session.post(self.stt_url, data=form) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ValueError("STT response exceeds 64 KiB")
                payload = json.loads(body)
            self.status.errors.pop("stt", None)
            self.status.stt_ready = True
            text = clean_transcription(payload)
            if not text:
                self.status.rejected_transcriptions += 1
            return text
        except Exception as error:
            self._error("stt", error)
            self.status.stt_ready = False
            return ""
        finally:
            self.status.stt_ms = (time.perf_counter() - started) * 1000
            self.status.stt_total_ms += self.status.stt_ms

    async def _capture_loop(self) -> None:
        vad = VoiceActivityDetector(self.status.input_rate, is_speech=self._speech_frame)
        previous = None
        epoch = self._generation
        while not self._stop.is_set():
            item = self._capture.get()
            if item is None:
                if self._echo.is_set() or epoch != self._generation:
                    vad.reset()
                await asyncio.sleep(0.01)
                continue
            sequence, generation, captured_at, samples = item
            if (self._echo.is_set() or generation != self._generation
                    or time.monotonic() - captured_at > 1.0):
                self.status.capture_dropped += 1
                vad.reset()
                continue
            if epoch != generation or (previous is not None and sequence != previous + 1):
                vad.reset()
            previous, epoch = sequence, generation
            utterance = vad.feed(samples)
            if utterance is None:
                # Bound event-loop occupancy when consuming a backlog.
                await asyncio.sleep(0)
                continue
            self._transcribing = True
            try:
                await self._state()
                wav = await self._blocking(self._encode_wav, utterance)
                del utterance
                text = await self._transcribe(wav)
                del wav
                if text and generation == self._generation and not self._echo.is_set():
                    self._response_endpoint_at = captured_at
                    await self._callback(self._on_utterance, text)
            finally:
                self._transcribing = False
                await self._state()

    def _synthesize_and_play(self, text: str) -> float:
        elapsed = 0.0
        language = getattr(self, 'language', 'en')
        voice = self._voice
        if language != 'en':
            if language not in self._voices:
                voice_type = importlib.import_module('piper').PiperVoice
                path = Path(self.piper_model).parent / (LANGUAGES[language][1] + '.onnx')
                self._voices[language] = voice_type.load(path, download_dir=path.parent)
            voice = self._voices[language]
        options = getattr(self, 'synthesis_settings', None)
        if options:
            from piper.config import SynthesisConfig
            chunks = iter(voice.synthesize(text, syn_config=SynthesisConfig(**options)))
        else:
            chunks = iter(voice.synthesize(text))
        while not self._stop.is_set():
            started = time.perf_counter()
            chunk = next(chunks, None)
            elapsed += (time.perf_counter() - started) * 1000
            if chunk is None or self._stop.is_set():
                break
            if chunk.sample_width != 2 or chunk.sample_channels != 1:
                raise ValueError("Piper must produce mono 16-bit PCM")
            if chunk.sample_rate == self.status.output_rate and self._output_channels == 1:
                data = chunk.audio_int16_bytes
            else:
                np = self._np
                audio = np.asarray(chunk.audio_float_array, dtype=np.float32)
                if chunk.sample_rate != self.status.output_rate:
                    factor = math.gcd(chunk.sample_rate, self.status.output_rate)
                    audio = self._resample_poly(audio, self.status.output_rate // factor,
                                                chunk.sample_rate // factor)
                pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("int16")
                if self._output_channels == 2:
                    pcm = np.repeat(pcm[:, None], 2, axis=1)
                data = pcm.tobytes()
            self._playback.put(data, self._stop)
        self._playback.drain(self._stop)
        return elapsed

    async def _speech_loop(self) -> None:
        while not self._stop.is_set():
            text = await self._speech.get()
            self._echo.set()
            self._generation += 1
            self.status.capture_dropped += self._capture.clear()
            self.status.speaking = True
            self._synthesis_started_at = time.monotonic()
            try:
                await self._state()
                if self._stop.is_set():
                    return
                self.status.tts_ms = await self._blocking(self._synthesize_and_play, text)
                self.status.tts_total_ms += self.status.tts_ms
                self.status.tts_sentences += 1
                # The last callback submitted PCM; allow device latency before listening.
                await asyncio.sleep(max(0.0, float(self._output.latency)))
            except Exception as error:
                self._error("output", error)
                self.status.output_ready = False
                self.status.speech_dropped += 1
                self._discard_speech()
                return
            finally:
                self._echo_until = time.monotonic() + ECHO_TAIL_SECONDS
                self._echo.clear()
                self.status.speaking = False
                self._speech.task_done()
                await self._state()

    def _discard_speech(self) -> None:
        while True:
            try:
                self._speech.get_nowait()
            except asyncio.QueueEmpty:
                return
            self.status.speech_dropped += 1
            self._speech.task_done()

    async def say(self, text: str) -> None:
        """Queue a sentence with backpressure; blank/punctuation-only input is ignored."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        text = text.strip()
        if not text or not any(c.isalnum() for c in text):
            return
        if len(text) > MAX_SENTENCE_CHARS:
            raise ValueError(f"split speech into sentences of at most {MAX_SENTENCE_CHARS} characters")
        await self.ready.wait()
        while True:
            if self._stop.is_set():
                raise RuntimeError("audio pipeline is closed")
            if not self.status.output_ready:
                raise RuntimeError(self.status.errors.get("output", "audio output unavailable"))
            try:
                self._speech.put_nowait(text)
                return
            except asyncio.QueueFull:
                # Polling avoids orphaned put tasks inserting after close/error.
                await asyncio.sleep(0.01)

    async def drain(self) -> None:
        """Wait for queued speech and the final hardware playback latency."""
        await self._speech.join()

    async def run(self, on_utterance: Callable, on_state: Callable) -> None:
        """Run until close or cancellation, with optional audio failures degraded."""
        if self._run_task is not None or self._stop.is_set():
            raise RuntimeError("AudioPipeline can only be run once")
        self._run_task = asyncio.current_task()
        self._on_utterance, self._on_state = on_utterance, on_state
        try:
            await self._state("starting")
            if not self._stop.is_set():
                await self._blocking(self._initialize)
            if self._aiohttp is not None and not self._stop.is_set():
                try:
                    self._session = self._aiohttp.ClientSession(
                        timeout=self._aiohttp.ClientTimeout(total=30, connect=5))
                    self.status.stt_ready = True
                except Exception as error:
                    self._error("stt", error)
            if not self._stop.is_set():
                if self.status.input_ready and self._session is not None:
                    self._workers.append(asyncio.create_task(self._capture_loop(), name="audio-capture"))
                if self.status.output_ready:
                    self._workers.append(asyncio.create_task(self._speech_loop(), name="audio-speech"))
                self.ready.set()
                await self._state()
                stop_waiter = asyncio.create_task(self._stopped.wait())
                try:
                    done, _ = await asyncio.wait([stop_waiter, *self._workers],
                                                 return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        if task is not stop_waiter and not task.cancelled():
                            error = task.exception()
                            if error is not None:
                                self._error("worker", error)
                    if not self._stop.is_set():
                        # Output failures leave microphone/STT available.
                        await self._state()
                        await self._stopped.wait()
                finally:
                    stop_waiter.cancel()
                    await asyncio.gather(stop_waiter, return_exceptions=True)
        finally:
            self.ready.set()
            await self.close()

    def _close_streams(self) -> None:
        for stream in (self._input, self._output):
            if stream is None:
                continue
            try:
                stream.abort()
            except Exception as error:
                self._error("shutdown", error)
            finally:
                try:
                    stream.close()
                except Exception as error:
                    self._error("shutdown", error)
        self._input = self._output = self._voice = None

    async def _shutdown(self, caller: asyncio.Task | None) -> None:
        await self.ready.wait()
        workers = [task for task in self._workers if task is not caller]
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._discard_speech()
        self.status.capture_dropped += self._capture.clear()
        try:
            if self._session is not None:
                await self._session.close()
        finally:
            self._session = None
            await self._blocking(self._close_streams)
            self.status.input_ready = self.status.output_ready = self.status.stt_ready = False
            self.status.speaking = False
            await self._state("closed")

    async def close(self) -> None:
        """Idempotent cancellation-safe shutdown; pending speech is discarded."""
        self._stop.set()
        self._stopped.set()
        if asyncio.current_task() is self._run_task and not self.ready.is_set():
            # A startup callback cannot wait for its own run() to finish setup.
            return
        if self._run_task is None:
            self.ready.set()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._shutdown(asyncio.current_task()))
        # An on_state('closed') callback may itself call close.
        if asyncio.current_task() is self._close_task:
            return
        try:
            await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            await self._close_task
            raise
