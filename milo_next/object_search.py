"""Explicit speech intents and bounded, cancellable camera sweeps, never LLM motion."""
import asyncio
import math
import re
import time

from .safety import SafetyError


ALIASES = {'cup': ('cup', 'mug', 'кружк', 'чашк'),
           'bottle': ('bottle', 'бутыл'), 'cell phone': ('phone', 'телефон'),
           'book': ('book', 'книг'), 'laptop': ('laptop', 'ноутбук'),
           'chair': ('chair', 'стул'), 'remote': ('remote', 'пульт'),
           'apple': ('apple', 'яблок'), 'banana': ('banana', 'банан'),
           'person': ('person', 'человек'), 'keyboard': ('keyboard', 'клавиатур')}


def search_intent(text):
    text = text.lower().strip()
    if re.fullmatch(r'(?:milo[, ]+|майло[, ]+)?(?:stop|стоп|остановись|stop searching|останови поиск)[.!?]*', text):
        return {'stop': True}
    if re.search(r'\b(don.t|do not|не|нет)\b', text):
        return None
    if not re.match(r'^(?:(?:milo|майло|please|пожалуйста)[, ]+)*(?:find|look for|look around|search for|найди|поищи|посмотри по сторонам|осмотрись)\b', text):
        return None
    labels = [label for label, aliases in ALIASES.items()
              if any(re.search(r'\b' + re.escape(alias) + r'\w*', text) for alias in aliases)]
    if len(labels) > 1:
        return {'unsupported': True}
    if labels:
        return {'target': labels[0]}
    if re.fullmatch(r'(?:(?:milo|майло|please|пожалуйста)[, ]+)*(?:look around|посмотри по сторонам|осмотрись)[.!?]*', text):
        return {'target': None}
    return {'unsupported': True}


def waypoints(pose):
    x = pose.get('J1')
    if type(x) is not int or not 0 <= x <= 180:
        raise SafetyError('valid_base_position_required')
    return [('J1', max(0, x - 36)), ('J1', min(180, x + 36)), ('J1', x)]


class ObjectSearch:
    def __init__(self, motion, observe, allowed, complete, on_view=None):
        self.motion, self.observe = motion, observe
        self.allowed, self.complete = allowed, complete
        self.on_view = on_view
        self.task = None
        self.status = {'state': 'idle'}

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def start(self, target):
        if self.active or self.motion.lock.locked():
            raise SafetyError('operator_motion_busy')
        if not self.allowed():
            raise SafetyError('fresh_camera_required')
        path = waypoints(self.motion.actuator.status()['raw_pose'])
        self.status = {'state': 'searching', 'target': target, 'found': [], 'error': None}
        self.task = asyncio.create_task(self._run(target, path))

    def cancel(self):
        self.motion.cancel()
        if self.active:
            self.status.update(state='cancelled')
            self.task.cancel()

    def _observe(self):
        if not self.allowed():
            raise SafetyError('camera_or_operator_stop')
        p = self.observe()
        t = p.get('timings', {})
        age = p.get('captured_monotonic', 0) - (t.get('objects_captured_monotonic') or 0)
        if (p.get('status', {}).get('backends', {}).get('objects') != 'hailort_h10'
                or not math.isfinite(age) or not -.1 <= age <= 1):
            raise SafetyError('object_detector_unavailable')
        return t.get('objects_frame_seq'), p.get('objects', [])

    async def _run(self, target, path):
        found, counts, sequence = {}, {}, None
        generation = self.motion.generation
        try:
            async with self.motion.lock:
                self.motion.actuator.disarm(force=True)
                path = [(None, None)] + path
                async with asyncio.timeout(120):
                    for joint, goal in path:
                        while True:
                            if generation != self.motion.generation:
                                raise SafetyError('operator_cancelled')
                            self.motion.check(self.motion.actuator.status())
                            # Inspect several distinct Hailo frames at each small stop.
                            until = time.monotonic() + .65
                            while time.monotonic() < until:
                                # Hold during brief camera/network gaps; never move on stale input.
                                deadline = time.monotonic() + 3
                                while not self.allowed():
                                    if generation != self.motion.generation:
                                        raise SafetyError('operator_cancelled')
                                    if time.monotonic() >= deadline:
                                        raise SafetyError('camera_unavailable_for_3_seconds')
                                    await asyncio.sleep(.1)
                                try:
                                    seq, objects = self._observe()
                                except SafetyError as exc:
                                    if target is not None or str(exc) != 'object_detector_unavailable':
                                        raise
                                    seq, objects = None, []
                                    self.status['detector_warning'] = str(exc)
                                if seq is not None and seq != sequence:
                                    sequence = seq
                                    visible = {o['label']: o for o in objects
                                               if .7 <= o.get('score', 0) <= 1}
                                    counts = {label: counts.get(label, 0) + 1 for label in visible}
                                    for label, obj in visible.items():
                                        if counts[label] >= 3:
                                            found[label] = obj
                                            if label == target:
                                                self.status.update(state='found', found=[obj])
                                                return
                                await asyncio.sleep(.08)
                            if joint is None:
                                if self.on_view:
                                    await self.on_view()
                                break
                            current = self.motion.actuator.status()['raw_pose'][joint]
                            if abs(current - goal) <= 2:
                                if self.on_view and joint == 'J1':
                                    await self.on_view()
                                break
                            if not self.allowed():
                                continue
                            await self.motion._step(joint, 1 if goal > current else -1, generation,
                                                    scan=True, goal=goal, allowed=self.allowed)
                self.status.update(state='not_found' if target else 'complete', found=list(found.values()))
        except asyncio.CancelledError:
            self.status.update(state='cancelled')
            raise
        except Exception as exc:
            self.status.update(state='stopped', error=str(exc) or type(exc).__name__)
        finally:
            self.motion.actuator.disarm(force=True)
            if self.status['state'] != 'cancelled':
                await self.complete(dict(self.status))
