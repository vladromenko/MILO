import asyncio
from dataclasses import asdict
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
from uuid import uuid4

import aiohttp
from aiohttp import web
from .audio import AudioPipeline
from .audio_controls import AudioControls
from .diagnostics import Diagnostics
from .http import authentication
from .perception import Perception
from .settings import ROOT, settings
from .visual_observer import VisualObserver

LOG = logging.getLogger(__name__)


class Edge:
    def __init__(self, cfg):
        self.cfg = cfg
        self.boot = uuid4().hex
        self.events = asyncio.Queue(maxsize=4)
        self.perception = Perception(camera=cfg["camera"],
            face_model=str(ROOT / "assets/models/vision/face_detection_yunet.onnx"),
            recognition_model=str(ROOT / "assets/models/vision/face_recognition_sface.onnx"),
            expression_model=str(ROOT / "assets/models/vision/facial_expression_mobilefacenet.onnx"),
            face_width=480, face_threshold=.65,
            hailo_model=cfg["hailo_model"])
        self.audio = AudioPipeline("http://127.0.0.1:8873/inference",
            str(ROOT / "assets/models/tts/en_US-ryan-low.onnx"),
            input_hint=cfg["input_hint"], output_hint=cfg["output_hint"])
        self.face = None
        self.audio_controls = AudioControls(self.audio)
        self.controls_lock = asyncio.Lock()
        self.visual = VisualObserver(self.perception)
        self.diagnostics = Diagnostics()
        self.preview_lock = asyncio.Lock()
        self.preview_at = 0
        self.preview_frame = None
        self.display_error = None
        self.last_brain = 0
        self.audio_task = None
        self.audio_missing = False
        self.started_at = time.monotonic()
        self.perception_bad_since = None

    def perception_requires_restart(self, status, now):
        if now - self.started_at < 30 or status['state'] not in {'failed', 'error', 'stale', 'starting'}:
            self.perception_bad_since = None
            return False
        if self.perception_bad_since is None:
            self.perception_bad_since = now
        # A scheduling gap pauses tracking; it must not immediately kill audio/display.
        return now - self.perception_bad_since >= (3 if status['state'] in {'failed','error'} else 8)

    async def utterance(self, text):
        item = {"id": uuid4().hex, "text": text, "created_at": time.monotonic()}
        item['language'] = self.audio.language
        if self.events.full():
            self.events.get_nowait()
        self.events.put_nowait(item)

    def state(self, state):
        if self.face:
            self.face.set_state("neutral" if state == "idle" else state)
            self.face.set_speaking_active(state == "speaking")

    def display(self):
        while not self.closing.is_set():
            try:
                from .cat_face import Face
                os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
                subprocess.run(["wlr-randr", "--output", self.cfg["display_output"],
                                "--transform", self.cfg["display_transform"]], check=True, timeout=5)
                self.face = Face()
                self.display_error = None
                self.face.run()
            except Exception as exc:
                self.display_error = str(exc)
                LOG.exception("display failed; retrying after graphical login")
            self.closing.wait(3)

    async def start(self, app):
        self.closing = threading.Event()
        self.perception.start()
        self.display_thread = threading.Thread(target=self.display, daemon=True)
        self.display_thread.start()
        self.audio_task = asyncio.create_task(self.audio.run(self.utterance, self.state))
        self.watch_task = asyncio.create_task(self.watchdog())

    async def watchdog(self):
        while True:
            if time.monotonic() - self.last_brain > 5:
                self.state("sleeping")
            status = self.perception.latest()["status"]
            if self.perception_requires_restart(status, time.monotonic()):
                # Native camera/Hailo failures require a clean process restart.
                LOG.error("Perception failed: %s", status)
                os._exit(1)
            # PortAudio enumerates devices once per process. Re-enumerate after
            # USB hotplug without repeatedly restarting while hardware is absent.
            cards = Path("/proc/asound/cards").read_text().lower()
            input_present = self.cfg["input_hint"].lower() in cards
            output_present = self.cfg["output_hint"].lower() in cards
            if not (input_present and output_present):
                self.audio_missing = True
                self.audio.status.input_ready &= input_present
                self.audio.status.output_ready &= output_present
                self.audio.status.errors["hardware"] = "configured USB audio device disconnected"
            elif self.audio_missing:
                LOG.warning("USB audio reconnected; restarting native device owners")
                os._exit(1)
            elif (self.audio.status.output_ready
                  and time.monotonic() - self.started_at > 30
                  and time.monotonic() - self.audio.status.output_callback_at > 2):
                LOG.error("Speaker callback stalled; restarting native device owners")
                os._exit(1)
            elif time.monotonic() - self.started_at > 30 and (
                    {"devices", "input", "output", "capture", "tts"} & self.audio.status.errors.keys()):
                LOG.warning("Retrying failed audio initialization")
                os._exit(1)
            await asyncio.sleep(1)

    async def close(self, app):
        self.closing.set()
        self.watch_task.cancel()
        await asyncio.gather(self.watch_task, return_exceptions=True)
        await self.audio.close()
        await asyncio.gather(self.audio_task, return_exceptions=True)
        await asyncio.to_thread(self.perception.close)
        if self.face:
            self.face.stop()
        await asyncio.to_thread(self.display_thread.join, 3)

    async def status(self, request):
        self.last_brain = time.monotonic()
        result = self.perception.latest()
        stamp = result.get("captured_monotonic", 0)
        result["capture_age_ms"] = (time.monotonic() - stamp) * 1000 if stamp else None
        utterances = []
        while not self.events.empty():
            event = self.events.get_nowait()
            if time.monotonic() - event["created_at"] < 5:
                utterances.append(event)
        return web.json_response({"boot": self.boot, "perception": result,
            "audio": asdict(self.audio.status), "utterances": utterances,
            "audio_settings": self.audio_controls.values,
            "resources": self.diagnostics.snapshot(),
            "display": {"running": bool(self.face and self.face.running), "error": self.display_error}})

    async def snapshot(self, request):
        if request.query.get('preview') == '1':
            async with self.preview_lock:
                if time.monotonic() - self.preview_at > .25:
                    self.preview_frame = await asyncio.to_thread(self.perception.preview_jpeg)
                    self.preview_at = time.monotonic()
                frame = self.preview_frame
        else:
            frame = await asyncio.to_thread(self.perception.jpeg)
        if not frame:
            raise web.HTTPServiceUnavailable(text="camera unavailable")
        return web.Response(body=frame, content_type="image/jpeg")

    async def observations(self, request):
        return web.json_response(self.perception.latest())

    async def visual_request(self, request):
        body = await request.json()
        try:
            if body.get('action') == 'clear':
                self.visual.frames.clear()
            elif body.get('action') == 'capture':
                await self.visual.capture()
            elif body.get('action') == 'ask':
                return web.json_response(await self.visual.ask(body.get('question')))
            else:
                raise ValueError('unknown visual action')
            return web.json_response({'ok': True})
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return web.json_response({'error': str(exc) or 'visual request timed out'}, status=503)

    async def control(self, request):
        body = await request.json()
        if 'audio_settings' in body:
            async with self.controls_lock:
                try:
                    values = await asyncio.to_thread(self.audio_controls.apply, body['audio_settings'])
                except (ValueError, subprocess.SubprocessError) as exc:
                    raise web.HTTPConflict(text='audio settings could not be applied') from exc
            return web.json_response({'ok': True, 'audio_settings': values})
        if "state" in body:
            self.state(body["state"])
        if "gaze" in body and self.face:
            self.face.set_gaze(*body["gaze"])
        if "say" in body:
            await self.audio.say(body["say"])
        if body.get("drain"):
            await self.audio.drain()
        return web.json_response({"ok": True})


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = settings()
    edge = Edge(cfg)
    app = web.Application(middlewares=[authentication(cfg["token"])], client_max_size=16384)
    app.add_routes([web.get("/status", edge.status), web.get("/snapshot", edge.snapshot),
                    web.get("/perception", edge.observations),
                    web.post("/control", edge.control), web.post('/visual', edge.visual_request)])
    app.on_startup.append(edge.start)
    app.on_cleanup.append(edge.close)
    web.run_app(app, host="127.0.0.1", port=8782, access_log=None)


if __name__ == "__main__":
    main()
