"""Run with python -m unittest discover -s tests -p test_audio.py.

All audio, native inference and network dependencies are fakes. No device is
opened and no sound is produced, including when this suite runs on the Pi.
"""

import asyncio
import builtins
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

from milo_next.audio import (
    AudioPipeline, CAPTURE_FRAMES, NewestBuffer, PlaybackBuffer, SPEECH_QUEUE_SIZE,
    VoiceActivityDetector, clean_transcription, select_device,
)


DEVICES = [
    dict(name="Built-in Audio", max_input_channels=1, max_output_channels=2,
         default_samplerate=48000),
    dict(name="USB UM02", max_input_channels=1, max_output_channels=0,
         default_samplerate=48000),
    dict(name="USB UACDemoV1.0", max_input_channels=0, max_output_channels=2,
         default_samplerate=48000),
]


def frame(rate=16000, amplitude=0.08):
    return [amplitude, -amplitude] * (round(rate * 0.02) // 2)


class MonoInput:
    def __init__(self, samples):
        self.samples = samples

    def __getitem__(self, key):
        return self.samples


class FakeStream:
    latency = 0.001

    def __init__(self, **settings):
        self.settings = settings
        self.started = self.aborted = self.closed = False
        self.writes = []
        self.write_threads = []
        self.fail_write = False

    def start(self):
        self.started = True

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True

    def write(self, data):
        if self.fail_write:
            raise OSError("speaker unplugged")
        if self.closed:
            raise AssertionError("write after close")
        self.write_threads.append(threading.get_ident())
        self.writes.append(bytes(data))
        return False


class FakeOutputStream(FakeStream):
    def start(self):
        super().start()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.pump)
        self.thread.start()

    def pump(self):
        while not self.stop.wait(0.005):
            if self.fail_write:
                return
            data = bytearray(self.settings["blocksize"] * self.settings["channels"] * 2)
            self.settings["callback"](data, self.settings["blocksize"], None, False)
            if any(data):
                self.write(data)

    def abort(self):
        self.stop.set()
        self.thread.join(1)
        super().abort()


class FakeSoundDevice:
    def __init__(self):
        self.devices = DEVICES
        self.inputs = []
        self.outputs = []
        self.checks = []
        self.native_stereo_only = False

    def query_devices(self):
        return self.devices

    def check_input_settings(self, **settings):
        self.checks.append(settings)

    def check_output_settings(self, **settings):
        self.checks.append(settings)
        if self.native_stereo_only and (settings["samplerate"], settings["channels"]) != (48000, 2):
            raise ValueError("unsupported format")

    def InputStream(self, **settings):
        stream = FakeStream(**settings)
        self.inputs.append(stream)
        return stream

    def RawOutputStream(self, **settings):
        stream = FakeOutputStream(**settings)
        self.outputs.append(stream)
        return stream


class FakeVoice:
    config = SimpleNamespace(sample_rate=16000)

    def __init__(self):
        self.texts = []
        self.threads = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def synthesize(self, text):
        self.texts.append(text)
        self.threads.append(threading.get_ident())
        self.entered.set()
        if not self.release.wait(3):
            raise RuntimeError("fake synthesis timed out")
        yield SimpleNamespace(sample_rate=16000, sample_width=2, sample_channels=1,
                              audio_int16_bytes=b"\x01\x00" * 1600)


class FakeForm:
    def __init__(self):
        self.fields = {}

    def add_field(self, name, value, **kwargs):
        self.fields[name] = (value, kwargs)


class FakeResponse:
    def __init__(self, session):
        self.session = session
        self.content = self

    async def __aenter__(self):
        self.session.entered.set()
        await self.session.release.wait()
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        if self.session.error:
            raise self.session.error

    async def iter_chunked(self, size):
        data = self.session.body
        for start in range(0, len(data), size):
            yield data[start:start + size]


class FakeSession:
    def __init__(self):
        self.body = json.dumps({"text": "Hello there."}).encode()
        self.error = None
        self.closed = False
        self.requests = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    def post(self, url, data):
        self.requests.append((url, data))
        return FakeResponse(self)

    async def close(self):
        self.closed = True


class Array:
    """Tiny fake only for exercising the WAV conversion boundary."""

    def __init__(self, values):
        self.values = list(values)

    def astype(self, dtype, **kwargs):
        return self

    def __mul__(self, value):
        return Array(x * value for x in self.values)

    def tobytes(self):
        return struct.pack("<" + "h" * len(self.values), *(int(x) for x in self.values))


class FakeNumpy:
    float32 = "float32"

    @staticmethod
    def concatenate(frames):
        return Array(x for values in frames for x in values)

    @staticmethod
    def nan_to_num(samples, **kwargs):
        return samples

    @staticmethod
    def clip(samples, low, high):
        return Array(max(low, min(high, x)) for x in samples.values)


class HelpersTest(unittest.TestCase):
    def test_playback_wrap_and_idle_silence(self):
        ring = PlaybackBuffer(8)
        stop = threading.Event()
        output = bytearray(6)
        ring.fill(output)
        self.assertEqual(output, bytes(6))
        ring.put(b"abcdef", stop)
        ring.fill(output)
        self.assertEqual(output, b"abcdef")
        ring.put(b"ghijk", stop)
        ring.fill(output)
        self.assertEqual(output, b"ghijk\0")
        ring.drain(stop)

    def test_playback_bounded_backpressure_and_stop(self):
        ring = PlaybackBuffer(4)
        stop = threading.Event()
        producer = threading.Thread(target=ring.put, args=(b"abcdefgh", stop))
        producer.start()
        time.sleep(0.03)
        self.assertTrue(producer.is_alive())
        output = bytearray(4)
        ring.fill(output)
        self.assertEqual(output, b"abcd")
        producer.join(1)
        ring.fill(output)
        self.assertEqual(output, b"efgh")
        stop.set()
        ring.put(b"ignored", stop)
        ring.fill(output)
        self.assertEqual(output, bytes(4))

    def test_import_does_not_load_optional_dependencies(self):
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"numpy", "scipy", "sounddevice", "piper", "aiohttp"}:
                raise AssertionError(f"eager optional import: {name}")
            return original(name, *args, **kwargs)

        path = Path(__file__).resolve().parents[1] / "milo_next" / "audio.py"
        spec = importlib.util.spec_from_file_location("isolated_audio", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, isolated_audio=module), patch("builtins.__import__", guarded):
            spec.loader.exec_module(module)
            self.assertEqual(module.AudioPipeline("http://localhost:8873", "voice.onnx").status.health, "idle")

    def test_vad_silence_dc_and_clicks_do_not_emit(self):
        for values in ([0.0] * 320, [0.5] * 320, [float("nan")] * 320):
            vad = VoiceActivityDetector(16000)
            for _ in range(1000):
                self.assertIsNone(vad.feed(values))
            self.assertLessEqual(vad.buffered_frames, 15)
        vad = VoiceActivityDetector(16000)
        self.assertIsNone(vad.feed(frame()))
        for _ in range(40):
            self.assertIsNone(vad.feed([0.0] * 320))

    def test_first_pcm_metrics_ignore_silence_and_capture_once(self):
        pipeline = AudioPipeline('http://localhost:8873', 'voice.onnx')
        pipeline._playback = PlaybackBuffer(640)
        pipeline._response_endpoint_at = 8.0
        pipeline._synthesis_started_at = 9.0
        with patch('milo_next.audio.time.monotonic', return_value=10.0):
            pipeline._output_callback(bytearray(320), 160, None, None)
            self.assertIsNone(pipeline.status.response_pcm_ms)
            pipeline._playback.put(b'\x01\x00' * 160, threading.Event())
            pipeline._output_callback(bytearray(320), 160, None, None)
            self.assertEqual(pipeline.status.response_pcm_ms, 2000)
            self.assertEqual(pipeline.status.tts_first_pcm_ms, 1000)
        with patch('milo_next.audio.time.monotonic', return_value=20.0):
            pipeline._output_callback(bytearray(320), 160, None, None)
            self.assertEqual(pipeline.status.response_pcm_ms, 2000)

    def test_vad_preroll_end_silence_and_maximum(self):
        vad = VoiceActivityDetector(16000)
        silence = [0.0] * 320
        for _ in range(30):
            vad.feed(silence)
        for _ in range(10):
            self.assertIsNone(vad.feed(frame()))
        for _ in range(29):
            self.assertIsNone(vad.feed(silence))
        recording = vad.feed(silence)
        self.assertEqual(len(recording), 55)
        self.assertEqual(recording[:15], [silence] * 15)
        for _ in range(599):
            self.assertIsNone(vad.feed(frame()))
        self.assertEqual(len(vad.feed(frame())), 600)
        self.assertEqual(vad.buffered_frames, 0)

    def test_vad_reset_and_invalid_frame(self):
        vad = VoiceActivityDetector(16000)
        for _ in range(10):
            vad.feed(frame())
        vad.reset()
        for _ in range(40):
            self.assertIsNone(vad.feed([0.0] * 320))
        with self.assertRaises(ValueError):
            vad.feed([0.0])
        self.assertEqual(vad.buffered_frames, 0)

    def test_newest_queue_overflow(self):
        buffer = NewestBuffer(2)
        self.assertFalse(buffer.put(1))
        self.assertFalse(buffer.put(2))
        self.assertTrue(buffer.put(3))
        self.assertEqual([buffer.get(), buffer.get(), buffer.get()], [2, 3, None])
        self.assertEqual(buffer.clear(), 0)

    def test_device_matching_never_uses_default_or_ambiguous_match(self):
        self.assertEqual(select_device(DEVICES, "um02", "input")[0], 1)
        self.assertEqual(select_device(DEVICES, "UACDemo", "output")[0], 2)
        for hint, direction in (("missing", "output"), ("UACDemo", "input")):
            with self.assertRaises(RuntimeError):
                select_device(DEVICES, hint, direction)
        with self.assertRaises(RuntimeError):
            select_device(DEVICES + [DEVICES[2]], "UACDemo", "output")

    def test_empty_and_non_speech_transcriptions(self):
        for payload in (None, [], {}, {"text": None}, {"text": " ... "},
                        {"text": "[BLANK_AUDIO]"}, {"text": "[Music]"},
                        {"text": "<|endoftext|>"},
                        {"text": "Invented text", "no_speech_prob": 0.99},
                        {"text": "Invented text", "avg_logprob": -2},
                        {"text": "Invented text", "compression_ratio": 3},
                        {"text": "Invented text", "segments": [{"no_speech_prob": 0.9}]}):
            with self.subTest(payload=payload):
                self.assertEqual(clean_transcription(payload), "")
        self.assertEqual(clean_transcription({"text": "  Thank you.\n"}), "Thank you.")
        self.assertEqual(clean_transcription({"text": "<|en|>Hello world."}), "Hello world.")

    def test_sound_annotations_and_unreliable_segments_are_not_user_requests(self):
        for text in ('(speaking in foreign language)', '(people chattering)', '[NOISE]',
                     '[ Inaudible ]', '(  people chattering  )'):
            self.assertEqual(clean_transcription({'text': text}), '')
        self.assertEqual(clean_transcription({'text': '(people chattering) Who are you?'}), 'Who are you?')
        self.assertEqual(clean_transcription({'text': 'Invented. Hello.', 'segments': [
            {'text': 'Invented.', 'no_speech_prob': .9},
            {'text': 'Hello.', 'no_speech_prob': .1}]}), 'Hello.')

    def test_loud_noise_rejected_by_speech_classifier(self):
        vad = VoiceActivityDetector(16000, is_speech=lambda samples: False)
        for _ in range(650):
            self.assertIsNone(vad.feed(frame(16000)))
        self.assertLessEqual(vad.buffered_frames, 15)


class PipelineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sd = FakeSoundDevice()
        self.voice = FakeVoice()
        self.session = FakeSession()
        self.loads = []

        def load(path):
            self.loads.append(path)
            return self.voice

        modules = {
            "numpy": FakeNumpy,
            "webrtcvad": SimpleNamespace(Vad=lambda mode: SimpleNamespace(is_speech=lambda pcm, rate: True)),
            "scipy.signal": SimpleNamespace(resample_poly=lambda data, up, down: data),
            "sounddevice": self.sd,
            "piper": SimpleNamespace(PiperVoice=SimpleNamespace(load=load)),
            "aiohttp": SimpleNamespace(FormData=FakeForm,
                                       ClientTimeout=lambda **kwargs: kwargs,
                                       ClientSession=lambda **kwargs: self.session),
        }
        self.import_patch = patch("milo_next.audio.importlib.import_module", side_effect=modules.__getitem__)
        self.mock_import = self.import_patch.start()
        self.addCleanup(self.import_patch.stop)
        self.pipeline = AudioPipeline("http://127.0.0.1:8873", "assets/models/tts/en_US-ryan-low.onnx")
        self.states = []
        self.heard = []
        self.running = None

    async def asyncTearDown(self):
        self.voice.release.set()
        await asyncio.wait_for(self.pipeline.close(), 5)
        if self.running is not None:
            await asyncio.gather(self.running, return_exceptions=True)

    async def start(self, on_utterance=None, on_state=None):
        self.running = asyncio.create_task(self.pipeline.run(
            on_utterance or self.heard.append, on_state or self.states.append))
        await asyncio.wait_for(self.pipeline.ready.wait(), 2)

    async def until(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0.005)

    def capture(self, samples=None, flags=False):
        samples = samples if samples is not None else frame(48000)
        self.pipeline._audio_callback(MonoInput(samples), len(samples), None, flags)

    async def utterance(self):
        for _ in range(10):
            self.capture()
            await asyncio.sleep(0)
        for _ in range(30):
            self.capture([0.0] * 960)
            await asyncio.sleep(0)

    async def test_persistent_resources_and_sentence_playback_off_loop(self):
        await self.start()
        self.assertEqual(self.pipeline.status.health, "ok")
        await self.pipeline.say("First sentence.")
        await self.pipeline.say("Second sentence.")
        await asyncio.wait_for(self.pipeline.drain(), 2)
        self.assertEqual(len(self.loads), 1)
        self.assertEqual(len(self.sd.inputs), 1)
        self.assertEqual(len(self.sd.outputs), 1)
        self.assertEqual(self.voice.texts, ["First sentence.", "Second sentence."])
        self.assertEqual(self.sd.outputs[0].settings["device"], 2)
        self.assertTrue(all(t != threading.get_ident() for t in self.voice.threads))
        self.assertTrue(all(t != threading.get_ident() for t in self.sd.outputs[0].write_threads))
        self.assertEqual(self.pipeline.status.tts_sentences, 2)
        self.assertGreater(self.pipeline.status.tts_ms, 0)
        self.assertIn("speaking", self.states)

    async def test_non_speech_never_reaches_stt(self):
        await self.start()
        self.pipeline._speech_vad.is_speech = lambda pcm, rate: False
        await self.utterance()
        await self.until(lambda: not len(self.pipeline._capture))
        self.assertEqual(self.pipeline.status.stt_requests, 0)
        self.assertEqual(self.heard, [])

    async def test_output_format_falls_back_on_same_usb_sink(self):
        self.sd.native_stereo_only = True
        await self.start()
        stream = self.sd.outputs[0]
        self.assertEqual((stream.settings["samplerate"], stream.settings["channels"]), (48000, 2))
        self.assertEqual(stream.settings["device"], 2)
        self.assertTrue(self.pipeline.status.output_ready)

    async def test_missing_output_degrades_without_wrong_device(self):
        self.sd.devices = DEVICES[:2]
        with self.assertLogs("milo_next.audio", level="WARNING"):
            await self.start()
        self.assertTrue(self.pipeline.status.input_ready)
        self.assertEqual(self.pipeline.status.health, "degraded")
        self.assertEqual(self.sd.outputs, [])
        self.assertEqual(self.loads, [])
        with self.assertRaises(RuntimeError):
            await self.pipeline.say("Hello.")

    async def test_missing_optional_dependencies_degrades_and_stops(self):
        self.mock_import.side_effect = ImportError("not installed")
        with self.assertLogs("milo_next.audio", level="WARNING"):
            await self.start()
        self.assertEqual(self.pipeline.status.health, "degraded")
        await self.pipeline.close()
        self.assertEqual(self.pipeline.status.health, "closed")

    async def test_microphone_overflow_keeps_newest_and_echo_drops(self):
        self.pipeline.status.input_rate = 48000
        for _ in range(CAPTURE_FRAMES + 7):
            self.capture()
        self.assertEqual(len(self.pipeline._capture), CAPTURE_FRAMES)
        self.assertEqual(self.pipeline.status.capture_dropped, 7)
        self.assertEqual(self.pipeline._capture.get()[0], 8)
        self.pipeline._echo.set()
        count = len(self.pipeline._capture)
        self.capture()
        self.assertEqual(len(self.pipeline._capture), count)
        self.pipeline._echo.clear()
        self.pipeline._echo_until = time.monotonic() + 1
        self.capture()
        self.assertEqual(len(self.pipeline._capture), count)

    async def test_speech_queue_backpressure_and_close_releases_waiters(self):
        await self.start()
        self.voice.release.clear()
        await self.pipeline.say("In flight.")
        await self.until(self.voice.entered.is_set)
        for n in range(SPEECH_QUEUE_SIZE):
            await self.pipeline.say(f"Sentence {n}.")
        pending = asyncio.create_task(self.pipeline.say("Overflow sentence."))
        await asyncio.sleep(0.03)
        self.assertFalse(pending.done())
        self.assertEqual(self.pipeline._speech.qsize(), SPEECH_QUEUE_SIZE)
        closing = asyncio.create_task(self.pipeline.close())
        await asyncio.sleep(0.02)
        self.assertFalse(closing.done())
        self.assertFalse(self.sd.outputs[0].closed)
        self.voice.release.set()
        await asyncio.wait_for(closing, 2)
        with self.assertRaises(RuntimeError):
            await pending
        await asyncio.wait_for(self.pipeline.drain(), 1)
        self.assertEqual(self.pipeline._speech.qsize(), 0)
        self.assertTrue(self.sd.outputs[0].closed)
        self.assertEqual(self.sd.outputs[0].writes, [])

    async def test_echo_guard_clears_preroll_and_suppresses_playback_capture(self):
        await self.start()
        for _ in range(5):
            self.capture()
        self.voice.release.clear()
        await self.pipeline.say("Do not transcribe me.")
        await self.until(self.voice.entered.is_set)
        for _ in range(80):
            self.capture()
        self.assertEqual(len(self.pipeline._capture), 0)
        self.voice.release.set()
        await self.pipeline.drain()
        for _ in range(40):
            self.capture()
        await asyncio.sleep(0.02)
        self.assertEqual(self.session.requests, [])
        self.assertEqual(self.heard, [])

    async def test_stt_persistent_session_multipart_and_empty_filter(self):
        await self.start()
        for payload, expected in (({"text": "Hello."}, "Hello."), ({"text": " "}, ""),
                                  ({"text": "[BLANK_AUDIO]"}, "")):
            self.session.body = json.dumps(payload).encode()
            self.assertEqual(await self.pipeline._transcribe(b"RIFF-fake"), expected)
        self.assertEqual(len(self.session.requests), 3)
        url, form = self.session.requests[0]
        self.assertEqual(url, "http://127.0.0.1:8873/inference")
        self.assertEqual(form.fields["response_format"][0], "verbose_json")
        self.assertEqual(form.fields["language"][0], "en")
        self.assertEqual(form.fields["file"][1]["filename"], "utterance.wav")
        self.assertGreater(self.pipeline.status.stt_total_ms, 0)

    async def test_stt_uses_selected_language_without_auto_switching(self):
        await self.start()
        self.pipeline.language = 'fr'
        self.session.body = b'{"text": "Hello.", "language": "english"}'
        await self.pipeline._transcribe(b"wav")
        self.assertEqual(self.session.requests[-1][1].fields['language'][0], 'fr')
        self.assertEqual(self.pipeline.language, 'fr')
        await self.pipeline._transcribe(b"wav")
        self.assertEqual(self.session.requests[-1][1].fields['language'][0], 'fr')

    async def test_stt_errors_recover_and_response_size_is_bounded(self):
        await self.start()
        for body in (b"not json", b"x" * 70000):
            self.session.body = body
            with self.assertLogs("milo_next.audio", level="WARNING"):
                self.assertEqual(await self.pipeline._transcribe(b"wav"), "")
            self.assertFalse(self.pipeline.status.stt_ready)
        self.session.body = b'{"text": "Recovered."}'
        self.assertEqual(await self.pipeline._transcribe(b"wav"), "Recovered.")
        self.assertTrue(self.pipeline.status.stt_ready)
        self.assertNotIn("stt", self.pipeline.status.errors)

    async def test_full_capture_path_and_no_silence_requests(self):
        await self.start()
        for _ in range(45):
            self.capture([0.0] * 960)
        await asyncio.sleep(0.03)
        self.assertEqual(self.session.requests, [])
        await self.utterance()
        await self.until(lambda: bool(self.heard))
        self.assertEqual(self.heard, ["Hello there."])
        self.assertEqual(self.pipeline.status.stt_requests, 1)

    async def test_speaking_invalidates_inflight_stt(self):
        await self.start()
        self.session.release.clear()
        await self.utterance()
        await asyncio.wait_for(self.session.entered.wait(), 2)
        await self.pipeline.say("Speaking now.")
        await self.pipeline.drain()
        self.session.release.set()
        await self.until(lambda: not self.pipeline._transcribing)
        self.assertEqual(self.heard, [])

    async def test_capture_gap_resets_partial_speech(self):
        await self.start()
        for _ in range(9):
            self.capture()
        await self.until(lambda: len(self.pipeline._capture) == 0)
        self.pipeline._sequence += 1
        for _ in range(9):
            self.capture()
        for _ in range(30):
            self.capture([0.0] * 960)
        await self.until(lambda: len(self.pipeline._capture) == 0)
        self.assertEqual(self.session.requests, [])

    async def test_stale_capture_frames_are_discarded(self):
        await self.start()
        for n in range(40):
            self.pipeline._capture.put((n, 0, time.monotonic() - 2, frame(48000)))
        await self.until(lambda: len(self.pipeline._capture) == 0)
        self.assertEqual(self.session.requests, [])
        self.assertEqual(self.pipeline.status.capture_dropped, 40)

    async def test_cancel_during_startup_joins_native_setup(self):
        entered = threading.Event()
        release = threading.Event()
        initialize = self.pipeline._initialize

        def delayed_initialize():
            initialize()
            entered.set()
            release.wait(3)

        self.pipeline._initialize = delayed_initialize
        self.running = asyncio.create_task(self.pipeline.run(self.heard.append, self.states.append))
        await self.until(entered.is_set)
        self.running.cancel()
        await asyncio.sleep(0.02)
        self.assertFalse(self.running.done())
        self.assertFalse(self.sd.inputs[0].closed)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.running, 2)
        self.assertTrue(self.sd.inputs[0].closed)
        self.assertTrue(self.sd.outputs[0].closed)

    async def test_cancellation_during_http_closes_every_resource(self):
        await self.start()
        self.session.release.clear()
        await self.utterance()
        await asyncio.wait_for(self.session.entered.wait(), 2)
        self.running.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.running, 2)
        self.assertTrue(self.session.closed)
        self.assertTrue(self.sd.inputs[0].closed)
        self.assertTrue(self.sd.outputs[0].closed)
        self.assertEqual(self.pipeline.status.state, "closed")

    async def test_speaker_failure_does_not_hang_drain(self):
        await self.start()
        self.sd.outputs[0].fail_write = True
        with self.assertLogs("milo_next.audio", level="WARNING"):
            await self.pipeline.say("First.")
            await self.pipeline.say("Second.")
            await asyncio.wait_for(self.pipeline.drain(), 3)
        self.assertFalse(self.pipeline.status.output_ready)
        self.assertTrue(self.pipeline.status.input_ready)
        with self.assertRaises(RuntimeError):
            await self.pipeline.say("Third.")

    async def test_close_from_utterance_callback(self):
        async def heard(text):
            self.heard.append(text)
            await self.pipeline.close()

        await self.start(on_utterance=heard)
        await self.utterance()
        await asyncio.wait_for(self.running, 2)
        self.assertEqual(self.heard, ["Hello there."])
        self.assertEqual(self.pipeline.status.state, "closed")

    async def test_close_from_starting_callback(self):
        async def state(value):
            if value == "starting":
                await self.pipeline.close()

        await self.start(on_state=state)
        await asyncio.wait_for(self.running, 2)
        self.assertEqual(self.sd.inputs, [])
        self.assertEqual(self.pipeline.status.state, "closed")

    async def test_close_from_speaking_callback_prevents_playback(self):
        async def state(value):
            if value == "speaking":
                await self.pipeline.close()

        await self.start(on_state=state)
        await self.pipeline.say("Must not play.")
        await asyncio.wait_for(self.running, 2)
        await asyncio.wait_for(self.pipeline.drain(), 2)
        self.assertEqual(self.sd.outputs[0].writes, [])
        self.assertEqual(self.pipeline.status.state, "closed")

    async def test_close_before_run_and_idempotent(self):
        await self.pipeline.close()
        await self.pipeline.close()
        with self.assertRaises(RuntimeError):
            await self.pipeline.run(self.heard.append, self.states.append)
        with self.assertRaises(RuntimeError):
            await self.pipeline.say("Hello.")

    async def test_empty_and_oversized_speech(self):
        await self.pipeline.say(" ")
        await self.pipeline.say("...")
        with self.assertRaises(ValueError):
            await self.pipeline.say("a" * 501)
        self.assertEqual(self.pipeline._speech.qsize(), 0)

    async def test_wav_resamples_mono_to_16k_pcm(self):
        self.pipeline._np = FakeNumpy
        self.pipeline.status.input_rate = 48000
        calls = []

        def resample(samples, up, down):
            calls.append((up, down))
            return Array(samples.values[::3])

        self.pipeline._resample_poly = resample
        data = self.pipeline._encode_wav([[0.5, -0.5, 0.25] * 480])
        with wave.open(io.BytesIO(data), "rb") as wav:
            self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
            self.assertEqual(wav.getnframes(), 480)
        self.assertEqual(calls, [(1, 3)])


if __name__ == "__main__":
    unittest.main()
