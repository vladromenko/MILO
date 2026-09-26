"""Jetson-only acoustic speaker check through the independent C920 microphone.

Plays generated English speech on Pi, captures ten seconds in memory, and checks
the persistent STT server's transcription. No recording is retained. No motion.
"""
import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys
import wave

import aiohttp
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from milo_next.settings import settings


async def main():
    recorder = subprocess.Popen([
        "arecord", "-q", "-D", "hw:C920,0", "-f", "S16_LE", "-r", "16000",
        "-c", "2", "-d", "10", "-t", "wav",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Drain immediately: otherwise the pipe fills before speaker playback starts.
    recording = asyncio.create_task(asyncio.to_thread(recorder.communicate, timeout=15))
    try:
        await asyncio.sleep(1)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
            async with client.post("http://127.0.0.1:8872/control",
                    headers={"Authorization": "Bearer " + settings()["token"]},
                    json={"say": "Hello. My speaker is working. The weather is sunny today.",
                          "drain": True}) as response:
                response.raise_for_status()
            raw, errors = await recording
            if recorder.returncode or errors:
                raise RuntimeError("acoustic capture failed: " + errors.decode())
            with wave.open(io.BytesIO(raw)) as wav:
                pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
            pcm = pcm.reshape(-1, 2).mean(axis=1).astype("<i2")
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(pcm.tobytes())
            form = aiohttp.FormData()
            form.add_field("file", buffer.getvalue(), filename="acoustic.wav", content_type="audio/wav")
            form.add_field("response_format", "json")
            async with client.post("http://127.0.0.1:8783/inference", data=form) as response:
                response.raise_for_status()
                result = await response.json()
            text = result.get("text", "").lower()
            passed = "speaker is working" in text and "sunny today" in text
            print(json.dumps({"passed": passed, "transcription": result.get("text", "")}))
            return 0 if passed else 1
    finally:
        if recorder.poll() is None:
            recorder.kill()
        await asyncio.gather(recording, return_exceptions=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
