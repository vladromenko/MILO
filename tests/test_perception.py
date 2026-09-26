import math
import time
import unittest
from unittest.mock import MagicMock, patch

from milo_next.perception import (
    COCO_CLASSES, IoUTracker, Letterbox, Perception, box_iou,
    decode_hailo, face_geometry,
)


class GeometryTests(unittest.TestCase):
    def test_letterbox_landscape_and_clipping(self):
        transform = Letterbox(640, 480, 640, 640)
        self.assertEqual(transform.padding, (0, 80))
        self.assertEqual(transform.restore([0.125, 0, 0.875, 1]), [0, 0, 1, 1])
        self.assertEqual(transform.restore([0, -1, 1, 2]), [0, 0, 1, 1])

    def test_letterbox_portrait_odd_dimensions(self):
        transform = Letterbox(333, 777, 640, 640)
        width, height = transform.resized
        left, top = transform.padding
        result = transform.restore([top / 640, left / 640,
                                    (top + height) / 640, (left + width) / 640])
        for actual, expected in zip(result, [0, 0, 1, 1]):
            self.assertAlmostEqual(actual, expected)
        with self.assertRaises(ValueError):
            Letterbox(0, 480, 640, 640)

    def test_coco_mapping_threshold_and_padding(self):
        classes = [[] for _ in COCO_CLASSES]
        classes[0] = [[0.125, 0.25, 0.875, 0.75, 0.9],
                      [0.125, 0.25, 0.875, 0.75, 0.1],
                      [0, 0, 0.1, 1, 0.99]]
        classes[56] = [[0.2, 0.1, 0.6, 0.5, 0.7]]
        result = decode_hailo(classes, Letterbox(640, 480, 640, 640))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["label"], "person")
        self.assertEqual(result[0]["box"], [0.25, 0, 0.75, 1])
        self.assertEqual(result[1]["label"], "chair")
        self.assertEqual(result[1]["class_id"], 56)

    def test_invalid_output_is_not_an_empty_success(self):
        transform = Letterbox(640, 480, 640, 640)
        with self.assertRaises(ValueError):
            decode_hailo([], transform)
        classes = [[] for _ in COCO_CLASSES]
        classes[0] = [[0, 0, 1, 1, math.nan]]
        with self.assertRaises(ValueError):
            decode_hailo(classes, transform)

    def test_yunet_landmarks_unknown_and_geometric_engagement(self):
        face = face_geometry([10, 20, 60, 80, 25, 40, 55, 40,
                              40, 55, 28, 75, 52, 75, 0.95], 100, 200)
        self.assertEqual(face["box"], [0.1, 0.1, 0.7, 0.5])
        for actual, expected in zip(face["bbox"], [0.1, 0.1, 0.6, 0.4]):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(face["landmarks"][2], [0.4, 0.275])
        self.assertEqual(face["engaged"], 1)
        self.assertEqual(face["identity"], "unknown")
        self.assertIsNone(face["embedding"])
        self.assertIsNone(face["embedding_model"])


class TrackingTests(unittest.TestCase):
    def test_reordering_and_one_to_one_matching(self):
        tracker = IoUTracker()
        a = {"box": [0, 0, 0.4, 0.8], "class_id": 0}
        b = {"box": [0.6, 0, 1, 0.8], "class_id": 0}
        first = tracker.update([a, b], 1)
        second = tracker.update([b, a], 1.1)
        self.assertEqual([x["track_id"] for x in second],
                         [first[1]["track_id"], first[0]["track_id"]])
        third = tracker.update([a, a], 1.2)
        self.assertNotEqual(third[0]["track_id"], third[1]["track_id"])

    def test_class_gating_expiry_and_short_occlusion(self):
        tracker = IoUTracker(max_age=0.5)
        person = {"box": [0, 0, 1, 1], "class_id": 0}
        first = tracker.update([person], 1)[0]["track_id"]
        self.assertEqual(tracker.update([], 1.1), [])
        self.assertEqual(tracker.update([person], 1.2)[0]["track_id"], first)
        chair = dict(person, class_id=56)
        self.assertNotEqual(tracker.update([chair], 1.3)[0]["track_id"], first)
        self.assertNotEqual(tracker.update([person], 2)[0]["track_id"], first)

    def test_degenerate_iou(self):
        self.assertEqual(box_iou([0, 0, 0, 0], [0, 0, 0, 0]), 0)
        self.assertEqual(box_iou([0, 0, 1, 1], [0, 0, 1, 1]), 1)


class LifecycleTests(unittest.TestCase):
    def test_hailo_initialization_failure_does_not_stop_faces_or_capture(self):
        perception = Perception()
        perception._snapshot.update(faces=[{'track_id': 1}])
        perception._snapshot['status'].update(state='running', healthy=True)
        with patch('milo_next.perception.HailoDetector', side_effect=RuntimeError('device unavailable')):
            perception._infer_objects_once()
        self.assertFalse(perception._stop.is_set())
        self.assertEqual(perception._snapshot['faces'], [{'track_id': 1}])
        self.assertEqual(perception._snapshot['status']['state'], 'running')
        self.assertEqual(perception._object_status(time.monotonic(), 0)['state'], 'error')

    def test_hailo_runtime_failure_is_isolated_and_discards_objects(self):
        perception = Perception()
        perception._camera_healthy = True
        perception._capture_seq = 1
        perception._frame = object()
        with patch('milo_next.perception.HailoDetector') as detector:
            detector.return_value.detect.side_effect = RuntimeError('ioctl failed')
            perception._infer_objects_once()
            detector.return_value.close.assert_called_once()
        self.assertFalse(perception._stop.is_set())
        self.assertEqual(perception._object_snapshot['objects'], [])
        self.assertIn('ioctl failed', perception._object_snapshot['error'])

    def test_hailo_recovery_is_bounded_and_does_not_restart_faces(self):
        perception = Perception()
        def fail():
            perception._object_snapshot.update(state='error', error='timeout', objects=[])
        with patch.object(perception, '_infer_objects_once', side_effect=fail) as attempt, \
                patch.object(perception._stop, 'wait', return_value=False) as wait:
            perception._infer_objects()
        self.assertEqual(attempt.call_count, 7)
        self.assertEqual([call.args[0] for call in wait.call_args_list], [1, 3, 10, 60, 60, 60])
        self.assertFalse(perception._stop.is_set())

    def test_hailo_recovery_success_stops_retrying(self):
        perception = Perception()
        states = iter(['error', 'running'])
        def attempt():
            perception._object_snapshot['state'] = next(states)
        with patch.object(perception, '_infer_objects_once', side_effect=attempt) as run, \
                patch.object(perception._stop, 'wait', return_value=False):
            perception._infer_objects()
        self.assertEqual(run.call_count, 2)

    def test_stale_or_previous_camera_objects_are_not_reused(self):
        perception = Perception()
        perception._object_snapshot.update(state='running', captured_monotonic=10,
            epoch=1, objects=[{'track_id': 2}], fps=5)
        fresh = perception._object_status(11, 1)
        self.assertEqual(fresh['state'], 'running')
        fresh['objects'].clear()
        self.assertEqual(len(perception._object_snapshot['objects']), 1)
        for now, epoch in [(13, 1), (11, 2)]:
            stale = perception._object_status(now, epoch)
            self.assertEqual(stale['objects'], [])
            self.assertEqual(stale['state'], 'stale')

    def test_snapshot_is_detached_and_empty_is_not_healthy(self):
        perception = Perception()
        snapshot = perception.latest()
        self.assertFalse(snapshot["status"]["healthy"])
        snapshot["faces"].append({"box": []})
        snapshot["status"]["backends"]["faces"] = "changed"
        self.assertEqual(perception.latest()["faces"], [])
        self.assertEqual(perception.latest()["status"]["backends"]["faces"], "opencv_yunet_cpu")
        self.assertIsNone(perception.jpeg())
        perception.close()
        perception.close()
        with self.assertRaises(RuntimeError):
            perception.start()

    def test_failure_and_stale_results_are_unhealthy(self):
        perception = Perception()
        perception._capture_time = time.monotonic() - 3
        perception._snapshot["captured_monotonic"] = perception._capture_time
        perception._snapshot["status"].update(state="running", healthy=True)
        perception._snapshot["faces"] = [{"track_id": 1}]
        self.assertEqual(perception.latest()["status"]["state"], "stale")
        self.assertEqual(perception.latest()["faces"], [])
        perception._fail("inference", RuntimeError("missing weights"))
        self.assertIn("missing weights", perception.latest()["status"]["error"])
        perception.close()
        self.assertEqual(perception.latest()["status"]["state"], "error")

    def test_camera_retries_are_bounded_and_invalidate_results(self):
        perception = Perception(camera_retries=2)
        perception._snapshot.update(faces=[{"track_id": 1}], objects=[{"track_id": 2}])
        cv2 = MagicMock()
        cap = cv2.VideoCapture.return_value
        cap.isOpened.return_value = True
        cap.read.return_value = (False, None)
        with patch.dict("sys.modules", {"cv2": cv2}), patch.object(perception._stop, "wait"):
            perception._capture()
        self.assertEqual(cv2.VideoCapture.call_count, 3)
        self.assertEqual(cap.release.call_count, 3)
        result = perception.latest()
        self.assertEqual(result["faces"], [])
        self.assertEqual(result["objects"], [])
        self.assertIsNone(result["captured_monotonic"])
        self.assertIn("retries exhausted", result["status"]["error"])
        self.assertFalse(result["status"]["healthy"])
        self.assertIsNone(perception.jpeg())

    def test_camera_reopens_after_failure(self):
        perception = Perception(camera_retries=1)
        cv2 = MagicMock()
        cap = cv2.VideoCapture.return_value
        cap.isOpened.return_value = True
        frame = MagicMock(size=1)

        def recovered_frame():
            perception._stop.set()
            return True, frame

        reads = iter([lambda: (False, None), recovered_frame])
        cap.read.side_effect = lambda: next(reads)()
        with patch.dict("sys.modules", {"cv2": cv2}), patch.object(perception._stop, "wait"):
            perception._capture()
        self.assertEqual(cv2.VideoCapture.call_count, 2)
        self.assertEqual(cap.release.call_count, 2)
        self.assertEqual(perception._capture_seq, 1)
        self.assertEqual(perception._capture_epoch, 1)
        self.assertIs(perception._frame, frame)
        # Recovery is not healthy until inference consumes the new camera epoch.
        self.assertFalse(perception.latest()["status"]["healthy"])

    def test_config_validation(self):
        for settings in ({"face_fps": 0}, {"object_fps": math.nan},
                         {"face_width": 0}, {"face_threshold": 2}):
            with self.assertRaises(ValueError):
                Perception(**settings)


if __name__ == "__main__":
    unittest.main()
