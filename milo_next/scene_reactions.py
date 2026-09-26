"""Conservative, temporal object events. Words are generated only by the LLM."""
import math
import time


class SceneReactions:
    INTERESTING = frozenset({'cup', 'bottle', 'wine glass', 'book', 'cell phone',
        'laptop', 'banana', 'apple', 'orange', 'sandwich', 'pizza', 'cake',
        'sports ball', 'teddy bear', 'potted plant'})

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.boot = None
        self.sequence = None
        self.seen = {}
        self.spoken = {}
        self.last_event = clock() - 75

    def observe(self, perception, *, boot, fresh):
        if boot != self.boot:
            self.boot, self.sequence, self.seen = boot, None, {}
        if not fresh:
            self.seen.clear()
            return
        timings = perception.get('timings', {})
        sequence = timings.get('objects_frame_seq')
        captured = perception.get('captured_monotonic')
        stamp = timings.get('objects_captured_monotonic')
        if (type(captured) not in (int, float) or type(stamp) not in (int, float)
                or not math.isfinite(captured - stamp) or not -.1 <= captured - stamp <= 1
                or perception.get('status', {}).get('backends', {}).get('objects') != 'hailort_h10'):
            self.seen.clear()
            return
        if sequence == self.sequence:
            return
        self.sequence = sequence
        now, labels = self.clock(), {}
        for obj in perception.get('objects', []):
            label, score = obj.get('label'), obj.get('score')
            if label in self.INTERESTING and type(score) in (int, float) and .7 <= score <= 1:
                labels[label] = max(labels.get(label, 0), score)
        updated = {}
        for label, score in labels.items():
            previous = self.seen.get(label)
            if previous and now - previous['last'] <= 1:
                updated[label] = dict(previous, count=previous['count'] + 1, last=now, score=score)
            else:
                updated[label] = dict(first=now, last=now, count=1, score=score)
        self.seen = updated

    def take(self, *, mode, proactivity, present, idle, last_utterance):
        now = self.clock()
        cooldown = 45 if proactivity == 'social' else 90
        if (mode != 'active' or proactivity == 'quiet' or not present or not idle
                or now - last_utterance < 15 or now - self.last_event < cooldown):
            return None
        choices = [(label, item) for label, item in self.seen.items()
                   if item['count'] >= 3 and now - item['first'] >= 1
                   and now - item['last'] < 1 and now - self.spoken.get(label, -1e9) >= 600]
        if not choices:
            return None
        label, item = max(choices, key=lambda pair: pair[1]['score'])
        self.last_event = now
        self.spoken[label] = now
        return {'label': label, 'confidence': round(item['score'], 2)}

    @staticmethod
    def prompt(event):
        return ('A persistent object detector observation near the person is ' + event['label'] +
            '. Generate one brief, natural, warm remark suitable for this situation. '
            'Do not announce object detection or repeat a fixed phrase. '
            'Do not assume ownership, what a cup contains, or that the person is eating or drinking. '
            'A gentle conditional wish is appropriate; do not claim to act physically. '
            'Do not ask a question unless it is genuinely useful.')
