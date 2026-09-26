"""Bounded Pi-side short-term scene history; no faces, images or disk writes."""
from collections import deque
from copy import deepcopy


class SceneMemory:
    def __init__(self):
        self.frames = deque(maxlen=1800)
        self.last = {}

    def observe(self, objects, captured):
        items = [{k: obj[k] for k in ('label', 'score', 'bbox') if k in obj}
                 for obj in objects if obj.get('score', 0) >= .7][:24]
        self.frames.append((captured, deepcopy(items)))
        for obj in items:
            self.last[obj['label']] = (captured, deepcopy(obj))
        self.last = {label: item for label, item in self.last.items() if captured - item[0] <= 300}

    def summary(self, now):
        return [dict(deepcopy(obj), last_seen_age_s=round(now - stamp, 1))
                for stamp, obj in sorted(self.last.values(), key=lambda item: -item[0])
                if 0 <= now - stamp <= 300][:24]
