"""Pi-local visual Q&A. Bounded RAM frames and an independent CPU VLM."""
import asyncio
import base64
from collections import deque
import json
import math
import re
import time

import aiohttp


def detector_evidence(perception):
    captured = perception.get('captured_monotonic')
    objects_at = perception.get('timings', {}).get('objects_captured_monotonic')
    age = captured - objects_at if type(captured) in (int, float) and type(objects_at) in (int, float) else math.inf
    available = (perception.get('status', {}).get('objects', {}).get('state') == 'running'
                 and math.isfinite(age) and -.1 <= age <= 1)
    objects = [{'label': obj['label'], 'bbox': obj['bbox'], 'score': round(obj['score'], 2)}
               for obj in perception.get('objects', []) if available and obj.get('score', 0) >= .7]
    people = [o for o in objects if o['label'] == 'person']
    phones = [o for o in objects if o['label'] == 'cell phone']
    associated = set()
    for phone in phones:
        x, y, w, h = phone['bbox']
        center = (x + w / 2, y + h / 2)
        candidates = []
        for index, person in enumerate(people):
            px, py, pw, ph = person['bbox']
            if px <= center[0] <= px + pw and py <= center[1] <= py + ph:
                candidates.append(index)
        if len(candidates) == 1:
            associated.add(candidates[0])
    return {'objects': objects[:24], 'faces': len(perception.get('faces', [])),
            'object_detector_available': available,
            'people_detected': len(people) if available else None,
            'phones_detected': len(phones) if available else None,
            'people_near_phone_candidates': len(associated) if available else None,
            'note': 'Box overlap only, not proof of holding or using a phone. Missed detections are possible.'}


class VisualObserver:
    def __init__(self, perception):
        self.perception = perception
        self.frames = deque(maxlen=3)
        self.lock = asyncio.Lock()

    async def capture(self):
        snapshot = self.perception.latest()
        if snapshot.get('status', {}).get('healthy') is not True:
            raise ValueError('fresh camera required')
        evidence = detector_evidence(snapshot)
        def encode():
            import cv2
            import numpy as np
            frame = cv2.imdecode(np.frombuffer(self.perception.jpeg(), dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError('camera unavailable')
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (int(w * min(1, 384 / max(h, w))), int(h * min(1, 384 / max(h, w)))))
            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if not ok:
                raise ValueError('camera encoding failed')
            return base64.b64encode(jpeg).decode('ascii')
        self.frames.append((time.monotonic(), await asyncio.to_thread(encode), evidence))

    async def ask(self, question):
        if not isinstance(question, str) or not 1 <= len(question) <= 1200:
            raise ValueError('invalid visual question')
        if self.lock.locked():
            raise ValueError('visual observer busy')
        async with self.lock:
            if not self.frames:
                await self.capture()
            frames = [frame for frame in self.frames if time.monotonic() - frame[0] < 150]
            self.frames.clear()
            if not frames:
                raise ValueError('visual frames expired')
            content = [{'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + data}}
                       for _, data, _ in frames]
            instructions = '\nAnswer the question in English using only what is visible. If unsure, say so.'
            if re.search(r'\b(?:how many|count|number of)\b', question, re.I):
                instructions += (' Give the visible count. Views overlap: do not sum counts. '
                                 'Unknown detector counts are not zero. Detector hints: ' + json.dumps([
                                     {k: v for k, v in f[2].items() if k not in {'objects', 'note'}} for f in frames]))
            if re.search(r'phone|mobile', question, re.I):
                instructions += ' A hand is not a phone; phone use must be visible.'
            content.append({'type': 'text', 'text': question + instructions})
            started = time.monotonic()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=150)) as http:
                async with http.post('http://127.0.0.1:8785/v1/chat/completions', json={
                        'messages': [{'role': 'user', 'content': content}], 'max_tokens': 160,
                        'temperature': .1, 'stream': False}) as response:
                    response.raise_for_status()
                    result = await response.json()
            answer = result['choices'][0]['message']['content'].strip()
            if not answer:
                raise ValueError('visual model returned no answer')
            return {'description': answer, 'views': len(frames),
                    'detector_evidence': [f[2] for f in frames],
                    'oldest_view_age_s': round(time.monotonic() - frames[0][0], 1),
                    'inference_ms': round((time.monotonic() - started) * 1000),
                    'model': 'SmolVLM2-500M-Q8', 'approximate': True}
