"""Ephemeral facial-expression cues, not diagnoses or measurements of feelings.

OpenCV Zoo MobileFaceNet, Apache-2.0; RGB aligned 112px faces, [-1, 1].
SFace alignment uses the same five-point reference as the upstream FER model.
"""
import math
import queue
import threading
import time

LABELS = ('angry', 'disgust', 'fearful', 'happy', 'neutral', 'sad', 'surprised')


class ExpressionFilter:
    def __init__(self):
        self.track = None
        self.label = None
        self.count = 0
        self.last = 0

    def update(self, track, scores, stamp):
        if len(scores) != 7 or not all(math.isfinite(float(v)) for v in scores):
            raise ValueError('invalid expression scores')
        ordered = sorted(range(7), key=lambda i: scores[i], reverse=True)
        index = ordered[0]
        confident = scores[index] >= .45 and scores[index] - scores[ordered[1]] >= .15
        label = LABELS[index] if confident else 'unknown'
        self.count = (self.count + 1 if track == self.track and label == self.label
                      and 0 < stamp - self.last <= 1.5 else 1)
        self.track, self.label, self.last = track, label, stamp
        return {'label': label if self.count >= 3 else 'unknown',
                'model_score': round(float(scores[index]), 3),
                'stable_samples': self.count, 'captured_monotonic': stamp,
                'basis': 'facial_expression_model_not_emotional_state'}


class ExpressionWorker:
    def __init__(self, model_path):
        self.model_path = model_path
        self.queue = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.results = {}
        self.error = None
        self.ready = False
        self.inference_ms = None
        self.thread = threading.Thread(target=self._run, name='milo-expression', daemon=True)
        self.thread.start()

    def submit(self, track, aligned_bgr, captured):
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self.queue.put_nowait((track, aligned_bgr.copy(), captured))
        except queue.Full:
            pass

    def _run(self):
        try:
            import cv2
            import numpy as np
            net = cv2.dnn.readNetFromONNX(str(self.model_path))
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            smoother = ExpressionFilter()
            self.ready = True
            while not self.stop.is_set():
                try:
                    track, crop, captured = self.queue.get(timeout=.25)
                except queue.Empty:
                    continue
                if time.monotonic() - captured > 1:
                    continue
                started = time.monotonic()
                blob = cv2.dnn.blobFromImage(crop, 1 / 127.5, (112, 112),
                                             (127.5, 127.5, 127.5), swapRB=True)
                net.setInput(blob, 'data')
                logits = net.forward('label').reshape(-1)
                if logits.size != 7 or not np.isfinite(logits).all():
                    raise ValueError('invalid expression model output')
                weights = np.exp(logits - logits.max())
                result = smoother.update(track, (weights / weights.sum()).tolist(), captured)
                with self.lock:
                    self.results = {track: result}
                    self.inference_ms = (time.monotonic() - started) * 1000
        except Exception as exc:
            self.error = str(exc)
            self.ready = False

    def latest(self, track, now):
        with self.lock:
            value = self.results.get(track)
            if value and 0 <= now - value['captured_monotonic'] < 1.5:
                return dict(value)
        return {'label': 'unknown', 'basis': 'no_fresh_stable_expression'}

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
