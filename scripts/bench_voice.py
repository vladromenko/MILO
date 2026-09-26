"""Repeatable STT transport benchmark using generated speech, NOT a microphone test.

Run on the edge. No speaker playback or actuator calls. The voice and audio
sample are temporary in memory; reports transcription and end-to-end request time.
"""
import io
import json
from pathlib import Path
import sys
import time
import wave

import aiohttp
import asyncio
from piper import PiperVoice

ROOT = Path(__file__).resolve().parents[1]


async def main():
    voice = PiperVoice.load(str(ROOT / "assets/models/tts/en_US-ryan-low.onnx"))
    samples = list(voice.synthesize("Hello Milo. My favorite drink is green tea."))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(samples[0].sample_rate)
        out.writeframes(b"".join(chunk.audio_int16_bytes for chunk in samples))
    import numpy as np
    from scipy.signal import resample_poly
    pcm = np.frombuffer(b"".join(chunk.audio_int16_bytes for chunk in samples), dtype="int16")
    import math
    factor = math.gcd(samples[0].sample_rate, 16000)
    pcm = resample_poly(pcm.astype("float32"), 16000 // factor,
                        samples[0].sample_rate // factor).clip(-32768, 32767).astype("int16")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(pcm.tobytes())
    async with aiohttp.ClientSession() as client:
        for _ in range(3):
            form = aiohttp.FormData()
            form.add_field("file", buffer.getvalue(), filename="speech.wav", content_type="audio/wav")
            form.add_field("response_format", "json")
            started = time.monotonic()
            async with client.post("http://127.0.0.1:8873/inference", data=form) as response:
                response.raise_for_status()
                result = await response.json()
            print(json.dumps({"ms": round((time.monotonic() - started) * 1000), "result": result}), flush=True)


asyncio.run(main())
