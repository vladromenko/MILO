"""Local camera perception. No frames leave this process except through jpeg().

``box`` is xyxy; ``bbox`` is xywh. Both and the landmarks are normalized to the
original camera image. Track IDs are
session-local associations, never identities. ``engaged`` is a frontal geometry
score, not an emotion or a claim about attention. Missing recognition weights
explicitly disable embeddings; supplied but invalid weights fail health.
"""

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import threading
import time
from .scene_memory import SceneMemory


COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)


def _clip(value):
    return min(1.0, max(0.0, float(value)))


def _xywh(box):
    return [box[0], box[1], box[2] - box[0], box[3] - box[1]]


def box_iou(a, b):
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = lambda box: max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    union = area(a) + area(b) - intersection
    return intersection / union if union > 0 else 0.0


@dataclass(frozen=True)
class Letterbox:
    width: int
    height: int
    target_width: int
    target_height: int

    def __post_init__(self):
        if min(self.width, self.height, self.target_width, self.target_height) <= 0:
            raise ValueError("Image dimensions must be positive")

    @property
    def resized(self):
        scale = min(self.target_width / self.width, self.target_height / self.height)
        return max(1, round(self.width * scale)), max(1, round(self.height * scale))

    @property
    def padding(self):
        width, height = self.resized
        return (self.target_width - width) // 2, (self.target_height - height) // 2

    def restore(self, yxyx):
        """Hailo normalized padded yxyx -> camera normalized xyxy."""
        width, height = self.resized
        left, top = self.padding
        y1, x1, y2, x2 = yxyx
        return [_clip((x1 * self.target_width - left) / width),
                _clip((y1 * self.target_height - top) / height),
                _clip((x2 * self.target_width - left) / width),
                _clip((y2 * self.target_height - top) / height)]


def decode_hailo(classes, transform, threshold=0.35):
    """Decode HailoRT get_buffer() NMS_BY_CLASS output, without a batch axis."""
    if len(classes) != len(COCO_CLASSES):
        raise ValueError("Expected an 80-class COCO NMS model")
    detections = []
    for class_id, rows in enumerate(classes):
        for row in rows:
            if len(row) != 5:
                raise ValueError("Expected Hailo NMS rows [y1,x1,y2,x2,score]")
            if not all(math.isfinite(float(value)) for value in row):
                raise ValueError("Nonfinite Hailo detection")
            if float(row[4]) < threshold:
                continue
            box = transform.restore(row[:4])
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            detections.append({"class_id": class_id, "label": COCO_CLASSES[class_id],
                               "box": box, "bbox": _xywh(box), "score": float(row[4])})
    return detections


class IoUTracker:
    """Greedy, one-to-one IoU association with class gating and timed expiry.

    This intentionally does not promise identity preservation through crossings
    or long occlusions. IDs are never recycled within a session.
    """

    def __init__(self, threshold=0.25, max_age=0.8, first_id=1):
        self.threshold, self.max_age = threshold, max_age
        self.next_id = first_id
        self.tracks = {}

    def update(self, detections, now):
        self.tracks = {key: value for key, value in self.tracks.items()
                       if now - value[1] <= self.max_age}
        candidates = []
        for index, detection in enumerate(detections):
            for track_id, (previous, _) in self.tracks.items():
                if previous.get("class_id") == detection.get("class_id"):
                    overlap = box_iou(previous["box"], detection["box"])
                    if overlap >= self.threshold:
                        candidates.append((-overlap, track_id, index))
        matched, used = {}, set()
        for _, track_id, index in sorted(candidates):
            if index not in matched and track_id not in used:
                matched[index] = track_id
                used.add(track_id)
        result = []
        for index, detection in enumerate(detections):
            track_id = matched.get(index)
            if track_id is None:
                track_id = self.next_id
                self.next_id += 1
            item = dict(detection, track_id=track_id)
            self.tracks[track_id] = (item, now)
            result.append(item)
        return result


def face_geometry(row, width, height):
    """Map a YuNet 15-element row; its five landmark pairs precede score."""
    if len(row) != 15 or not all(math.isfinite(float(v)) for v in row):
        raise ValueError("Invalid YuNet detection")
    x, y, w, h = map(float, row[:4])
    landmarks = [[_clip(row[i] / width), _clip(row[i + 1] / height)]
                 for i in range(4, 14, 2)]
    eye_distance = math.hypot(row[4] - row[6], row[5] - row[7])
    left = math.hypot(row[8] - row[4], row[9] - row[5])
    right = math.hypot(row[8] - row[6], row[9] - row[7])
    frontal = _clip(1.0 - abs(left - right) / max(eye_distance, 1e-6))
    if eye_distance < 1:
        frontal = 0.0
    box = [_clip(x / width), _clip(y / height),
           _clip((x + w) / width), _clip((y + h) / height)]
    return {"box": box, "bbox": _xywh(box),
            "landmarks": landmarks, "score": float(row[14]),
            "engaged": frontal, "engaged_basis": "landmark_symmetry",
            "identity": "unknown", "embedding": None, "embedding_model": None}


def _model_path(path, name):
    if not path or not Path(path).is_file():
        raise FileNotFoundError(f"{name} model not found: {path}")
    return str(path)


class HailoDetector:
    """Persistent HailoRT 5 InferModel with native COCO NMS postprocessing."""

    def __init__(self, path, threshold=0.35):
        import cv2
        import numpy as np
        import hailo_platform as hailo

        path = _model_path(path, "Hailo")
        # Check HEF metadata, not just the filename (renamed H8 HEFs still fail).
        parsed = subprocess.run(["hailortcli", "parse-hef", path], check=True,
                                capture_output=True, text=True, timeout=10)
        compatible = next((line for line in parsed.stdout.splitlines()
                           if "HEF Compatible for:" in line), "")
        if "HAILO10H" not in compatible:
            raise ValueError(f"HAILO10H HEF required; {compatible or 'unknown architecture'}")
        self.cv2, self.np, self.threshold = cv2, np, threshold
        self._resources = ExitStack()
        try:
            params = hailo.VDevice.create_params()
            params.scheduling_algorithm = hailo.HailoSchedulingAlgorithm.ROUND_ROBIN
            self.device = self._resources.enter_context(hailo.VDevice(params))
            self.model = self.device.create_infer_model(path)
            self.model.set_batch_size(1)
            if len(self.model.inputs) != 1 or len(self.model.outputs) != 1:
                raise ValueError("Expected one RGB input and one COCO NMS output")
            self.height, self.width, channels = self.model.input().shape
            if channels != 3:
                raise ValueError("Expected RGB Hailo input")
            output = self.model.output()
            if output.format.order != hailo.FormatOrder.HAILO_NMS_BY_CLASS:
                raise ValueError("Expected HAILO_NMS_BY_CLASS; raw/pose HEFs unsupported")
            infos = self.model.hef.get_output_vstream_infos()
            if infos[0].nms_shape.number_of_classes != 80:
                raise ValueError("Expected 80 COCO classes")
            self.model.input().set_format_type(hailo.FormatType.UINT8)
            output.set_format_type(hailo.FormatType.FLOAT32)
            self.configured = self._resources.enter_context(self.model.configure())
            self.bindings = self.configured.create_bindings(output_buffers={
                output.name: np.empty(output.shape, dtype=np.float32)})
        except Exception:
            self._resources.close()
            raise

    def detect(self, frame):
        cv2 = self.cv2
        geometry = Letterbox(frame.shape[1], frame.shape[0], self.width, self.height)
        resized = cv2.resize(frame, geometry.resized)
        left, top = geometry.padding
        width, height = geometry.resized
        padded = cv2.copyMakeBorder(resized, top, self.height - height - top,
                                   left, self.width - width - left,
                                   cv2.BORDER_CONSTANT, value=(114, 114, 114))
        self.bindings.input().set_buffer(cv2.cvtColor(padded, cv2.COLOR_BGR2RGB))
        begin = time.monotonic()
        self.configured.run([self.bindings], 2000)
        inference_ms = (time.monotonic() - begin) * 1000
        result = decode_hailo(self.bindings.output().get_buffer(), geometry, self.threshold)
        return result, inference_ms

    def close(self):
        self._resources.close()


class Perception:
    """Latest-only capture with independent face and object inference workers.

    latest() returns detached JSON-compatible data. status is a dictionary with
    state/healthy/error/backends; startup and runtime failures are visible there.
    Face and object observations each carry their own timestamps because Hailo
    runs at a lower cadence. jpeg() alone encodes the latest captured frame.
    A closed instance cannot be restarted. Neither model is downloaded here.
    """

    def __init__(self, camera="/dev/video0", face_model=None, recognition_model=None,
                 hailo_model="/usr/share/hailo-models/yolov8m_h10.hef", *,
                 face_fps=15.0, object_fps=5.0, face_width=320,
                 face_threshold=0.8, object_threshold=0.35,
                 recognition_interval=0.5, stale_after=2.0, camera_retries=3,
                 expression_model=None):
        if (not all(math.isfinite(v) and v > 0 for v in
                    (face_fps, object_fps, recognition_interval, stale_after))
                or face_width < 32 or not 0 < face_threshold <= 1
                or not 0 < object_threshold <= 1 or not isinstance(camera_retries, int)
                or camera_retries < 0):
            raise ValueError("Invalid perception rates, size or thresholds")
        self.camera, self.face_model = camera, face_model
        self.recognition_model, self.hailo_model = recognition_model, hailo_model
        self.expression_model = expression_model
        self.face_fps, self.object_fps, self.face_width = face_fps, object_fps, face_width
        self.face_threshold, self.object_threshold = face_threshold, object_threshold
        self.recognition_interval, self.stale_after = recognition_interval, stale_after
        self.camera_retries = camera_retries
        self._condition = threading.Condition()
        self._lifecycle = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        self._closed = False
        self._frame = None
        self._capture_seq = 0
        self._capture_time = 0.0
        self._capture_ms = 0.0
        self._camera_healthy = False
        self._capture_epoch = 0
        self.scene_memory = SceneMemory()
        self._object_snapshot = {"objects": [], "state": "starting", "error": None,
                                 "captured_monotonic": None, "frame_seq": 0,
                                 "epoch": -1, "hailo_ms": None, "object_ms": None,
                                 "fps": 0.0}
        self._snapshot = {"frame_seq": 0, "captured_monotonic": None,
                          "faces": [], "objects": [], "timings": {},
                          "status": {"state": "stopped", "healthy": False,
                                     "error": None, "backends": {
                                         "faces": "opencv_yunet_cpu",
                                         "objects": "hailort_h10",
                                         "recognition": "opencv_sface_cpu" if recognition_model
                                         else "disabled_no_model"}}}

    def start(self):
        with self._lifecycle:
            if self._closed:
                raise RuntimeError("Perception is closed; create a new instance")
            if self._threads:
                return self
            with self._condition:
                self._snapshot["status"]["state"] = "starting"
            self._threads = [threading.Thread(target=self._capture, name="milo-camera", daemon=True),
                             threading.Thread(target=self._infer, name="milo-inference", daemon=True),
                             threading.Thread(target=self._infer_objects, name="milo-objects", daemon=True)]
            for thread in self._threads:
                thread.start()
        return self

    def _fail(self, component, error):
        with self._condition:
            self._snapshot["faces"] = []
            self._snapshot["objects"] = []
            self._snapshot["status"].update(state="error", healthy=False,
                error=f"{component}: {type(error).__name__}: {error}")
            self._stop.set()
            self._condition.notify_all()

    def _capture(self):
        cap = None
        try:
            import cv2
            retries, consecutive_frames = 0, 0
            while not self._stop.is_set():
                try:
                    if cap is None:
                        cap = cv2.VideoCapture(self.camera, cv2.CAP_V4L2)
                        if not cap.isOpened():
                            raise RuntimeError(f"Cannot open V4L2 camera {self.camera}")
                        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                        cap.set(cv2.CAP_PROP_FPS, 30)
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    begin = time.monotonic()
                    ok, frame = cap.read()
                    captured = time.monotonic()
                    if not ok or frame is None or frame.size == 0:
                        raise RuntimeError("Camera frame read failed")
                except Exception as error:
                    with self._condition:
                        self._camera_healthy = False
                        self._capture_epoch += 1
                        self._frame = None
                        self._snapshot.update(faces=[], objects=[], captured_monotonic=None)
                        self._snapshot["status"].update(state="recovering", healthy=False,
                                                       error=f"camera: {error}")
                        self._condition.notify_all()
                    if cap is not None:
                        cap.release()
                        cap = None
                    if retries >= self.camera_retries:
                        raise RuntimeError(f"Camera retries exhausted: {error}") from error
                    self._stop.wait(min(1.0, 0.25 * 2 ** retries))
                    retries += 1
                    consecutive_frames = 0
                    continue
                consecutive_frames += 1
                if consecutive_frames >= 30:
                    retries = 0
                with self._condition:
                    self._frame = frame
                    self._camera_healthy = True
                    self._capture_seq += 1
                    self._capture_time = captured
                    self._capture_ms = (captured - begin) * 1000
                    self._condition.notify_all()
        except Exception as error:
            self._fail("camera", error)
        finally:
            if cap is not None:
                cap.release()

    def _infer_objects(self):
        # Later attempts allow the independent system driver recovery to finish.
        delays = (1, 3, 10, 60, 60, 60)
        for attempt in range(len(delays) + 1):
            self._infer_objects_once()
            if self._stop.is_set() or self._object_snapshot['state'] != 'error' or attempt == len(delays):
                return
            with self._condition:
                self._object_snapshot['state'] = 'recovering'
                self._object_snapshot['recovery_attempt'] = attempt + 1
            if self._stop.wait(delays[attempt]):
                return

    def _infer_objects_once(self):
        """A native accelerator failure must not stop faces, audio or capture."""
        hailo = None
        try:
            hailo = HailoDetector(self.hailo_model, self.object_threshold)
            tracker = IoUTracker()
            last_seq, epoch, previous = 0, -1, None
            while not self._stop.is_set():
                with self._condition:
                    self._condition.wait_for(lambda: self._stop.is_set() or
                        (self._camera_healthy and self._capture_seq > last_seq), timeout=.5)
                    if self._stop.is_set():
                        break
                    if not self._camera_healthy or self._capture_seq == last_seq:
                        continue
                    frame, seq, captured, current_epoch = (
                        self._frame, self._capture_seq, self._capture_time, self._capture_epoch)
                if current_epoch != epoch:
                    tracker.tracks.clear()
                    epoch = current_epoch
                started = time.monotonic()
                detected, hailo_ms = hailo.detect(frame)
                objects = tracker.update(detected, captured)
                for obj in objects:
                    obj.update(frame_seq=seq, captured_monotonic=captured)
                elapsed = time.monotonic() - started
                with self._condition:
                    if self._camera_healthy and epoch == self._capture_epoch:
                        self.scene_memory.observe(objects, captured)
                        self._object_snapshot = dict(objects=objects, state="running", error=None,
                            captured_monotonic=captured, frame_seq=seq, epoch=epoch,
                            hailo_ms=hailo_ms, object_ms=elapsed * 1000,
                            fps=1 / (started - previous) if previous is not None else 0.0)
                previous, last_seq = started, seq
                self._stop.wait(max(0, 1 / self.object_fps - elapsed))
        except Exception as error:
            with self._condition:
                self._object_snapshot.update(objects=[], state="error",
                    error=f"{type(error).__name__}: {error}", captured_monotonic=None, fps=0.0)
        finally:
            if hailo is not None:
                try:
                    hailo.close()
                except Exception as error:
                    with self._condition:
                        self._object_snapshot.update(objects=[], state="error",
                            error=f"close: {error}", captured_monotonic=None, fps=0.0)

    def _object_status(self, now, epoch):
        with self._condition:
            result = deepcopy(self._object_snapshot)
        stamp = result['captured_monotonic']
        if result['state'] == 'running' and (
                stamp is None or now - stamp > self.stale_after or result['epoch'] != epoch):
            result.update(objects=[], state='stale', error='Object inference is stale', fps=0.0)
        return result

    def _infer(self):
        expression = None
        try:
            import cv2
            import numpy as np
            face_path = _model_path(self.face_model, "YuNet")
            detector = cv2.FaceDetectorYN.create(face_path, "", (320, 240),
                self.face_threshold, 0.3, 5000, cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
            recognizer = None
            if self.recognition_model is not None:
                recognizer = cv2.FaceRecognizerSF.create(
                    _model_path(self.recognition_model, "SFace"), "",
                    cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU)
            if self.expression_model:
                from .expression import ExpressionWorker
                expression = ExpressionWorker(self.expression_model)
            face_tracks = IoUTracker(first_id=1_000_000)
            embeddings = {}
            last_seq, next_face = 0, 0.0
            previous_face = None
            face_rate = 0.0
            previous_epoch = -1
            while not self._stop.is_set():
                with self._condition:
                    self._condition.wait_for(lambda: self._stop.is_set() or
                                             (self._camera_healthy and self._capture_seq > last_seq),
                                             timeout=0.5)
                    if self._stop.is_set():
                        break
                    if not self._camera_healthy or self._capture_seq == last_seq:
                        continue
                    frame, seq, captured, capture_ms = (self._frame, self._capture_seq,
                                                        self._capture_time, self._capture_ms)
                    epoch = self._capture_epoch
                delay = next_face - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
                    continue
                last_seq = seq
                if epoch != previous_epoch:
                    face_tracks.tracks.clear()
                    embeddings.clear()
                    previous_epoch = epoch
                begin = time.monotonic()
                next_face = begin + 1 / self.face_fps
                height, width = frame.shape[:2]
                small_width = min(self.face_width, width)
                small_height = max(1, round(height * small_width / width))
                small = cv2.resize(frame, (small_width, small_height))
                detector.setInputSize((small_width, small_height))
                _, rows = detector.detect(small)
                rows = [] if rows is None else rows
                faces = face_tracks.update([face_geometry(row, small_width, small_height)
                                            for row in rows], captured)
                face_ms = (time.monotonic() - begin) * 1000
                recognition_start = time.monotonic()
                for face, row in zip(faces, rows):
                    track_id = face["track_id"]
                    cached = embeddings.get(track_id)
                    if recognizer is not None and (cached is None or
                            captured - cached[0] >= self.recognition_interval):
                        full_row = row.copy()
                        full_row[[0, 2, 4, 6, 8, 10, 12]] *= width / small_width
                        full_row[[1, 3, 5, 7, 9, 11, 13]] *= height / small_height
                        crop = recognizer.alignCrop(frame, full_row)
                        if (expression is not None and len(faces) == 1
                                and full_row[2] >= 48 and face['engaged'] >= .5):
                            expression.submit(track_id, crop, captured)
                        feature = recognizer.feature(crop).reshape(-1)
                        norm = float(np.linalg.norm(feature))
                        if feature.size != 128 or not np.isfinite(feature).all() or norm <= 0:
                            raise ValueError("SFace returned an invalid embedding")
                        cached = (captured, (feature / norm).tolist())
                        embeddings[track_id] = cached
                    if cached is not None:
                        face["embedding"] = cached[1]
                        face["embedding_model"] = "opencv_sface_2021dec"
                        face["embedding_monotonic"] = cached[0]
                    face.update(frame_seq=seq, captured_monotonic=captured)
                    if expression is not None:
                        face['expression'] = expression.latest(track_id, time.monotonic())
                embeddings = {key: value for key, value in embeddings.items()
                              if key in face_tracks.tracks}
                recognition_ms = (time.monotonic() - recognition_start) * 1000
                object_state = self._object_status(time.monotonic(), epoch)
                if previous_face is not None:
                    face_rate = 1 / (begin - previous_face)
                previous_face = begin
                timings = {"capture_ms": capture_ms, "face_ms": face_ms,
                           "recognition_ms": recognition_ms, "hailo_ms": object_state['hailo_ms'],
                           "object_ms": object_state['object_ms'], "total_ms": (time.monotonic() - begin) * 1000,
                           "face_fps": face_rate, "object_fps": object_state['fps'],
                           "objects_frame_seq": object_state['frame_seq'],
                           "objects_captured_monotonic": object_state['captured_monotonic']}
                with self._condition:
                    if self._stop.is_set():
                        break
                    if not self._camera_healthy or epoch != self._capture_epoch:
                        continue
                    status = dict(self._snapshot["status"], state="running", healthy=True, error=None)
                    status['objects'] = {key: object_state[key] for key in ('state', 'error')}
                    status['backends'] = dict(status['backends'], objects=(
                        'hailort_h10' if object_state['state'] == 'running' else 'unavailable'))
                    if expression is not None:
                        status['expression'] = {'ready': expression.ready,
                            'error': expression.error, 'inference_ms': expression.inference_ms}
                    self._snapshot = dict(frame_seq=seq, captured_monotonic=captured,
                                          faces=faces, objects=object_state['objects'], timings=timings, status=status)
        except Exception as error:
            self._fail("inference", error)
        finally:
            if expression is not None:
                expression.close()

    def latest(self):
        with self._condition:
            snapshot = deepcopy(self._snapshot)
            capture_time = self._capture_time
            snapshot['recent_objects'] = self.scene_memory.summary(time.monotonic())
        now = time.monotonic()
        timestamp = snapshot["captured_monotonic"]
        snapshot["timings"]["age_ms"] = (now - timestamp) * 1000 if timestamp else None
        if snapshot["status"]["state"] == "running" and (
                now - capture_time > self.stale_after or now - timestamp > self.stale_after):
            snapshot["status"].update(state="stale", healthy=False, error="Camera or inference is stale")
            snapshot.update(faces=[], objects=[])
        return snapshot

    def jpeg(self, quality=85):
        if not isinstance(quality, int) or not 1 <= quality <= 100:
            raise ValueError("JPEG quality must be an integer from 1 to 100")
        with self._condition:
            frame = self._frame
        if frame is None:
            return None
        import cv2
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return encoded.tobytes()

    def preview_jpeg(self):
        """A bounded operator preview; never expose a frozen camera as live."""
        import cv2
        now = time.monotonic()
        with self._condition:
            if self._frame is None or now - self._capture_time > .5:
                return None
            frame = self._frame.copy()
            snapshot = deepcopy(self._snapshot)
        height, width = frame.shape[:2]
        if width > 640:
            frame = cv2.resize(frame, (640, round(height * 640 / width)))
            height, width = frame.shape[:2]
        detected = snapshot.get('captured_monotonic')
        if detected and now - detected < .3:
            for group, color in (('objects', (70, 190, 240)), ('faces', (140, 230, 70))):
                for item in snapshot.get(group, [])[:20]:
                    box = item.get('bbox', [])
                    if len(box) != 4:
                        continue
                    x, y, w, h = box
                    left, top = max(0, int(x * width)), max(0, int(y * height))
                    right, bottom = min(width - 1, int((x + w) * width)), min(height - 1, int((y + h) * height))
                    label = item.get('label', 'Face')
                    if group == 'faces':
                        expression = item.get('expression', {}).get('label', 'unknown')
                        label = 'Face' + (f' | {expression}?' if expression != 'unknown' else '')
                    label = str(label)[:40]
                    cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                    text_y = max(18, min(height - 4, top - 5))
                    cv2.putText(frame, label, (left, text_y), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 3)
                    cv2.putText(frame, label, (left, text_y), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1)
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if not ok:
            raise RuntimeError('preview encoding failed')
        return encoded.tobytes()

    def close(self):
        with self._lifecycle:
            self._closed = True
            self._stop.set()
            with self._condition:
                self._condition.notify_all()
            deadline = time.monotonic() + 5.0
            for thread in self._threads:
                thread.join(max(0.0, deadline - time.monotonic()))
            blocked = [thread.name for thread in self._threads if thread.is_alive()]
            if blocked:
                self._fail("shutdown", RuntimeError(f"Workers did not stop: {blocked}"))
                return
            with self._condition:
                self._frame = None
                self._snapshot.update(faces=[], objects=[])
                if self._snapshot["status"]["state"] != "error":
                    self._snapshot["status"].update(state="closed", healthy=False)
