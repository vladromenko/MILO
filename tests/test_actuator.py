"""ROS-free adapter guard/lifecycle tests using local temporary latch files only."""

from dataclasses import replace
import math
from pathlib import Path
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from milo_next.actuator import Actuator, encode_command, encode_manual_command
from milo_next.safety import SafeCommand, SafetyError


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FloatSubclass(float):
    def __float__(self):
        raise AssertionError("must not coerce subclasses")


class StringSubclass(str):
    pass


class FakeROS:
    """In-memory ROS modules: no ROS installation, network, or device access."""

    def __init__(self):
        self.sent = []
        self.calls = []
        self.subscribers = 1
        self.publishers = 1
        self.publish_hook = None
        self.setup_error = None
        self.spin_error = None
        self.shutdown_feedback = False
        self.callback = None
        self.stop = threading.Event()
        self.spin_entered = threading.Event()
        self.callback_completed = threading.Event()
        self.rclpy = ModuleType("rclpy")
        self.rclpy.SignalHandlerOptions = SimpleNamespace(NO=0)
        self.rclpy.init = self.init
        self.rclpy.shutdown = self.shutdown
        self.rclpy.spin = self.spin
        self.node_module = ModuleType("rclpy.node")
        self.node_module.Node = self.make_node
        self.arm_module = ModuleType("arm_msgs")
        self.msg_module = ModuleType("arm_msgs.msg")
        self.msg_module.ArmJoint = SimpleNamespace
        self.msg_module.ArmJoints = SimpleNamespace

    @property
    def modules(self):
        return {"rclpy": self.rclpy, "rclpy.node": self.node_module,
                "arm_msgs": self.arm_module, "arm_msgs.msg": self.msg_module}

    def init(self, *, signal_handler_options):
        assert signal_handler_options == 0
        self.calls.append("init")
        self.stop.clear()

    def shutdown(self):
        self.calls.append("shutdown")
        self.stop.set()

    def spin(self, node):
        self.spin_entered.set()
        self.stop.wait(timeout=5)
        if self.shutdown_feedback:
            self.callback(SimpleNamespace(joint1=120, joint3=100))
            self.callback_completed.set()
        if self.spin_error:
            raise self.spin_error

    def make_node(self, name):
        self.calls.append(("node", name))
        return SimpleNamespace(create_publisher=self.create_publisher,
                               create_subscription=self.create_subscription,
                               count_publishers=self.count_publishers,
                               destroy_node=lambda: self.calls.append("destroy"))

    def create_publisher(self, message_type, topic, depth):
        self.calls.append(("publisher", topic, depth))
        if self.setup_error:
            raise self.setup_error
        return SimpleNamespace(get_subscription_count=lambda: self.subscribers,
                               publish=self.publish)

    def create_subscription(self, message_type, topic, callback, depth):
        self.calls.append(("subscription", topic, depth))
        self.callback = callback
        return object()

    def count_publishers(self, topic):
        assert topic == "/arm_joint"
        return self.publishers

    def publish(self, message):
        if self.publish_hook is not None:
            self.publish_hook(message)
        self.sent.append((message.id, message.joint, message.time))


class EncodingTests(unittest.TestCase):
    def setUp(self):
        self.command = SafeCommand("J1", 120.0, 0.125, 1, 100.0)

    def test_only_verified_ids_and_integer_endpoints_encode(self):
        for joint, identifier, endpoints in (("J1", 1, (0, 9, 180)), ("J3", 3, (75, 135))):
            for target in endpoints:
                with self.subTest(joint=joint, target=target):
                    result = encode_command(replace(self.command, joint=joint, target=float(target)))
                    self.assertEqual(result, (identifier, target, 125))
                    self.assertTrue(all(type(value) is int for value in result))

    def test_locked_ids_never_have_a_serialized_representation(self):
        for joint in ("J2", "J4", "J5", "J6", 1, 3, 6, True, "1", "j1", "J01", [], None, StringSubclass("J1")):
            with self.subTest(joint=repr(joint)), self.assertRaises(ValueError):
                encode_command(replace(self.command, joint=joint))
        with self.assertRaises(ValueError):
            encode_command(SimpleNamespace(joint="J1", target=120, duration_s=0.125))

    def test_numeric_and_fractional_target_tricks_are_rejected_not_rounded(self):
        for target in (True, False, "120", None, math.nan, math.inf, -math.inf,
                       120.1, 120.9, -0.000001, 180.000001, 10 ** 1000, FloatSubclass(120)):
            with self.subTest(target=repr(target)), self.assertRaises(ValueError):
                encode_command(replace(self.command, target=target))

    def test_duration_rounds_up_and_never_below_125_ms(self):
        for duration, expected in ((0.12, 125), (0.124, 125), (0.125, 125),
                                   (0.125001, 126), (0.9999, 1000), (1, 1000)):
            with self.subTest(duration=duration):
                self.assertEqual(encode_command(replace(self.command, duration_s=duration))[2], expected)

    def test_manual_encoder_has_fixed_limits_and_no_servo_six(self):
        for joint, target in [('J2', 115), ('J4', 0), ('J5', 90)]:
            self.assertEqual(encode_manual_command(replace(self.command, joint=joint, target=target, duration_s=.9)),
                             (int(joint[1:]), target, 900))
        for joint, target in [('J6', 40), ('J2', 166), ('J4', -31), ('J4', -10), ('J5', 111)]:
            with self.assertRaises(ValueError):
                encode_manual_command(replace(self.command, joint=joint, target=target, duration_s=.9))
        with self.assertRaises(ValueError):
            encode_manual_command(replace(self.command, joint='J4', target=-39, duration_s=.9), recovery=True)
        for duration in (0, -1, math.nextafter(0.12, -math.inf), 1.001, True, "0.125", None,
                         math.nan, math.inf, -math.inf, 10 ** 1000, FloatSubclass(0.125)):
            with self.subTest(duration=repr(duration)), self.assertRaises(ValueError):
                encode_command(replace(self.command, duration_s=duration))


class ActuatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = Clock()
        self.ros = FakeROS()
        self.enterContext(patch.dict("sys.modules", self.ros.modules))
        self.latch = Path(self.temporary.name) / "data" / "ESTOP"
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch)
        self.addCleanup(self.actuator.close)

    def feedback(self, j1=120, j3=100):
        self.clock.advance(0.01)
        self.actuator._feedback(SimpleNamespace(joint1=j1, joint3=j3))

    def stable(self, j1=120, j3=100):
        for _ in range(3):
            self.feedback(j1, j3)

    def arm(self):
        self.actuator.start()
        self.assertTrue(self.ros.spin_entered.wait(timeout=2))
        self.stable()
        self.actuator.arm()

    def test_camera_hold_preserves_permission_but_never_publishes_or_rearms(self):
        self.arm()
        self.actuator.hold()
        self.assertEqual(self.actuator.status()['state'], 'ARMED')
        self.assertEqual(self.ros.sent, [])
        self.actuator.disarm()
        self.actuator.hold()
        self.assertEqual(self.actuator.status()['state'], 'DISARMED')
        self.assertEqual(self.ros.sent, [])

    def test_integer_tracking_profile_acknowledges_real_progress(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.arm()
        self.actuator.track((.9, .5), True)
        self.assertEqual(self.ros.sent, [(1, 129, 250)])
        self.clock.advance(.38)
        self.feedback(j1=128)
        self.assertIsNone(self.actuator.status()['pending'])
        self.stable(j1=128)
        self.actuator.track((.9, .5), True)
        self.assertEqual(self.ros.sent[-1], (1, 137, 250))
        self.assertTrue(all(joint in (1, 3) for joint, _, _ in self.ros.sent))

    def test_explicit_joystick_speed_is_faster_but_obeys_existing_velocity_cap(self):
        self.actuator.start()
        self.stable()
        self.actuator.nudge('J3', 1, goal=104, speed=8)
        self.assertEqual(self.ros.sent, [(3,104,500)])
        self.clock.advance(1)
        self.stable(j3=104)
        for speed in (3,9,True,8.0):
            with self.assertRaises(SafetyError):
                self.actuator.nudge('J3',1,goal=108,speed=speed)
        self.assertEqual(len(self.ros.sent),1)
        self.actuator.nudge('J3',1,goal=108,speed=4)
        self.assertEqual(self.ros.sent[-1],(3,108,1000))

    def test_tracking_range_warning_does_not_hide_motion_fault(self):
        self.actuator.start()
        self.actuator.error = 'ack_timeout'
        self.stable(j3=26)
        self.assertEqual(self.actuator.status()['error'],'ack_timeout')
        self.assertEqual(self.actuator.status()['tracking_error'],'invalid_feedback')
        self.assertTrue(self.actuator.status()['manual']['J3']['feedback_stable'])

    def test_j1_full_range_accepts_nine_and_endpoint_steps(self):
        self.actuator.start()
        for current in (0, 9, 88, 179, 180):
            self.stable(j1=current)
            self.assertTrue(self.actuator.status()['manual']['J1']['feedback_stable'])
            self.assertEqual(self.actuator.raw_pose['J1'], current)
        self.stable(j1=178)
        self.actuator.nudge('J1', 1, goal=180)
        self.assertEqual(self.ros.sent[-1], (1, 180, 900))
        self.clock.advance(1)
        self.stable(j1=180)
        self.stable(j1=2)
        self.actuator.rebase()
        self.actuator.nudge('J1', -1, goal=0)
        self.assertEqual(self.ros.sent[-1], (1, 0, 900))

    def test_operator_nudge_uses_feedback_not_unacknowledged_target(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.actuator.start()
        self.stable()
        self.actuator.targets['J1'] = 150
        self.actuator.nudge('J1', -1)
        self.assertEqual(self.ros.sent[-1], (1, 117, 900))
        self.assertEqual(self.actuator.status()['state'], 'DISARMED')
        self.clock.advance(1)
        self.feedback(j1=118)
        self.assertIsNone(self.actuator.status()['manual']['J1']['pending'])
        self.stable(j1=118)
        self.actuator.nudge('J3', 1)
        self.assertEqual(self.ros.sent[-1], (3, 104, 900))

    def test_manual_goal_is_not_overshot_and_six_stays_rejected(self):
        self.actuator.start()
        self.stable()
        self.actuator.nudge('J3', 1, goal=103)
        self.assertEqual(self.ros.sent, [(3, 103, 900)])
        for joint, target in [('J6',90), ([],90), ('J1',True), ('J1',189)]:
            with self.assertRaises(SafetyError):
                self.actuator.validate_target(joint, target)
        self.assertEqual(len(self.ros.sent), 1)

    def test_rebase_after_physical_adjustment_sends_nothing_and_clears_old_targets(self):
        self.actuator.start()
        self.stable()
        self.actuator.nudge('J1', 1)
        with self.assertRaises(SafetyError):
            self.actuator.rebase()
        self.clock.advance(1)
        self.feedback(j1=123)
        self.stable(j1=123)
        self.assertEqual(self.actuator.auxiliary['J1'].previous_target('J1'), 123)
        self.stable(j1=140)
        sent = list(self.ros.sent)
        ready = self.actuator.rebase()
        self.assertIn('J1', ready)
        self.assertIsNone(self.actuator.auxiliary['J1'].previous_target('J1'))
        self.assertEqual(self.ros.sent, sent)
        self.assertEqual(self.actuator.status()['state'], 'DISARMED')
        self.actuator.nudge('J1', -1)
        self.assertEqual(self.ros.sent[-1], (1, 137, 900))

    def test_recovery_does_not_claim_out_of_range_j1_is_ready(self):
        self.actuator.start()
        self.stable(j1=189)
        ready = self.actuator.rebase()
        self.assertNotIn('J1', ready)
        self.assertIn('J3', ready)
        self.assertFalse(self.actuator.status()['feedback_stable'])
        self.assertEqual(self.ros.sent, [])

    def test_manual_reversal_preserves_step_from_measurement_and_previous_target(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.actuator.start()
        self.stable()
        self.actuator.nudge('J3', 1)
        self.assertEqual(self.ros.sent[-1], (3, 104, 900))
        self.clock.advance(1)
        self.feedback(j3=102)
        self.stable(j3=102)
        self.actuator.nudge('J3', -1)
        self.assertEqual(self.ros.sent[-1], (3, 100, 900))
        self.assertEqual(self.actuator.gate.previous_target('J3'), 100)

    def test_manual_posture_guards_owner_direction_and_preserves_pending_on_close(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.actuator.start()
        for _ in range(3):
            self.clock.advance(.01)
            self.actuator._feedback(SimpleNamespace(joint1=120, joint2=166, joint3=100, joint4=-43, joint5=90, joint6=40))
        for joint, direction in [('J2', 1), ('J4', 1), ('J6', 1)]:
            with self.assertRaises(SafetyError):
                self.actuator.nudge(joint, direction)
        self.actuator.nudge('J2', -1)
        self.assertEqual(self.ros.sent, [(2, 164, 900)])
        with self.assertRaises(SafetyError):
            self.actuator.arm()
        with self.assertRaises(SafetyError):
            self.actuator.nudge('J5', 1)
        self.actuator.close()
        self.assertTrue(self.latch.exists())

    def test_manual_step_independent_of_tracking_pose(self):
        self.actuator.start()
        for _ in range(3):
            self.clock.advance(.01)
            self.actuator._feedback(SimpleNamespace(
                joint1=161, joint2=169, joint3=25, joint4=5, joint5=94, joint6=40))
        self.assertFalse(self.actuator.status()['feedback_stable'])
        with self.assertRaises(SafetyError):
            self.actuator.arm()
        self.actuator.nudge('J4', -1)
        self.assertEqual(self.ros.sent, [(4, 1, 900)])
        with self.assertRaises(SafetyError):
            self.actuator.nudge('J5', 1)
        with self.assertRaises(SafetyError):
            self.actuator.nudge('J6', 1)

    def test_manual_j3_small_step_below_tracking_range_does_not_enable_tracking(self):
        self.actuator.start()
        self.stable(j1=161, j3=25)
        self.actuator.nudge('J3', 1)
        self.assertEqual(self.ros.sent, [(3, 29, 900)])
        self.assertEqual(self.actuator.status()['state'], 'DISARMED')
        with self.assertRaises(SafetyError):
            self.actuator.arm()

    def test_manual_j2_reenters_range_without_expanding_limits(self):
        self.actuator.start()
        for _ in range(3):
            self.clock.advance(.01)
            self.actuator._feedback(SimpleNamespace(
                joint1=161, joint2=169, joint3=25, joint4=5, joint5=94, joint6=40))
        with self.assertRaises(SafetyError):
            self.actuator.nudge('J2', 1)
        self.actuator.nudge('J2', -1)
        self.assertEqual(self.ros.sent, [(2, 165, 900)])

    def test_manual_step_still_requires_own_fresh_feedback(self):
        self.actuator.start()
        self.stable()
        with self.assertRaisesRegex(SafetyError, 'feedback_unstable'):
            self.actuator.nudge('J4', -1)
        self.assertEqual(self.ros.sent, [])

    def test_manual_step_respects_global_estop_with_valid_joint_feedback(self):
        self.actuator.start()
        for _ in range(3):
            self.clock.advance(.01)
            self.actuator._feedback(SimpleNamespace(
                joint1=161, joint2=169, joint3=25, joint4=5, joint5=94, joint6=40))
        self.actuator.estop()
        with self.assertRaises(SafetyError):
            self.actuator.nudge('J4', -1)
        self.assertEqual(self.ros.sent, [])

    def test_manual_posture_failure_latches_all_and_reset_never_moves(self):
        self.actuator.start()
        for _ in range(3):
            self.clock.advance(.01)
            self.actuator._feedback(SimpleNamespace(joint1=120, joint2=115, joint3=100, joint4=0, joint5=90, joint6=40))
        self.actuator.nudge('J5', 1)
        self.assertEqual(self.ros.sent, [(5, 92, 900)])

        self.assertEqual(self.actuator.status()['published_count'], 1)
        self.assertEqual(self.actuator.status()['tracking_published_count'], 0)
        self.clock.advance(2.1)
        self.assertEqual(self.actuator.status()['state'], 'ESTOP')
        self.assertTrue(self.latch.exists())
        self.assertTrue(all(gate.status().state == 'ESTOP' for gate in self.actuator.auxiliary.values()))
        self.assertEqual(self.actuator.status()['motion_fault'], {
            'reason': 'ack_timeout', 'joint': 'J5', 'target': 92, 'measured': 90,
            'feedback_age_s': self.actuator.status()['manual']['J5']['feedback_age_s'],
            'timeout_s': 2.0,
        })
        self.actuator.reset()
        self.assertEqual(self.actuator.status()['state'], 'DISARMED')
        self.assertIsNone(self.actuator.status()['error'])
        self.assertIsNone(self.actuator.status()['motion_fault'])
        self.assertEqual(self.ros.sent, [(5, 92, 900)])

    def test_scan_rejects_vertical_commands_at_actuator_boundary(self):
        self.actuator.start()
        self.stable()
        with self.assertRaisesRegex(SafetyError, 'scan_joint_not_allowed'):
            self.actuator.nudge('J3', 1, _scan=True)
        self.assertEqual(self.ros.sent, [])

    def test_J4_negative_recovery_rejected_without_motion_or_global_estop(self):
        self.actuator.start()
        def sample(j4):
            self.clock.advance(.01)
            self.actuator._feedback(SimpleNamespace(joint1=120, joint2=115, joint3=100, joint4=j4, joint5=90, joint6=40))
        for _ in range(3):
            sample(-43)
        with self.assertRaisesRegex(SafetyError, 'J4_negative_angles_rejected_by_controller_firmware'):
            self.actuator.recover_j4_step()
        for direction in (-1, 1):
            with self.assertRaisesRegex(SafetyError, 'J4_negative_angles_rejected_by_controller_firmware'):
                self.actuator.nudge('J4', direction)
        self.assertEqual(self.ros.sent, [])
        self.assertEqual(self.actuator.status()['state'], 'DISARMED')
        self.assertEqual(self.actuator.status()['manual']['J4']['command_limits'], [0, 115])
        self.assertEqual(self.actuator.status()['manual']['J4']['blocked_reason'],
                         'J4_negative_angles_rejected_by_controller_firmware')
        self.assertFalse(self.latch.exists())

    def test_operator_nudge_still_requires_progress_and_locked_axes_never_publish(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.actuator.start()
        self.stable()
        for joint, direction in [('J2', 1), ('J4', 1), ('J5', 1), ('J6', 1), ('J1', True), ('J1', 2)]:
            with self.assertRaises(SafetyError):
                self.actuator.nudge(joint, direction)
        self.assertEqual(self.ros.sent, [])
        self.actuator.nudge('J1', -1)
        self.clock.advance(.3)
        self.feedback()
        self.assertIsNotNone(self.actuator.status()['manual']['J1']['pending'])
        self.clock.advance(3)
        self.assertEqual(self.actuator.status()['state'], 'ESTOP')

    def test_integer_lower_stop_does_not_block_horizontal_tracking(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.actuator.start()
        self.stable(j1=122, j3=77)
        self.actuator.arm()
        self.actuator.track((.7, .9), True)
        self.assertEqual(self.ros.sent, [(1, 127, 250)])

    def test_j3_raw_target_acknowledges_without_global_offset(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.actuator.start()
        self.stable(j1=120, j3=81)
        self.actuator.arm()
        self.actuator.track((.5, .67), True)
        self.assertEqual(self.ros.sent, [(3, 77, 250)])
        self.clock.advance(.38)
        self.feedback(j1=120, j3=77)
        self.assertIsNone(self.actuator.status()['pending'])
        self.assertEqual(self.actuator.status()['pose']['J3'], 77)
        self.assertFalse(self.latch.exists())

    def test_j3_tracking_avoids_unresponsive_two_degree_corrections(self):
        self.actuator.close()
        self.actuator = Actuator(clock=self.clock, latch_path=self.latch, integer_feedback=True)
        self.addCleanup(self.actuator.close)
        self.actuator.start()
        self.stable(j1=120, j3=77)
        self.actuator.arm()
        self.actuator.track((.5, .44), True)
        self.assertEqual(self.ros.sent, [(3, 81, 250)])
        self.clock.advance(.38)
        self.feedback(j1=120, j3=79)
        self.assertIsNone(self.actuator.status()['pending'])

    def test_real_mcu_cadence_allows_stable_single_step_only(self):
        self.actuator.start()
        for _ in range(3):
            self.clock.advance(0.38)
            self.actuator._feedback(SimpleNamespace(joint1=123, joint3=73))
        self.assertTrue(self.actuator.status()["feedback_stable"])
        self.actuator.jog("J1", 1)
        self.assertEqual(self.ros.sent, [(1, 124, 125)])
        self.assertEqual(self.actuator.status()["state"], "DISARMED")
        with self.assertRaises(SafetyError):
            self.actuator.jog("J1", 1)
        self.clock.advance(0.38)
        self.actuator._feedback(SimpleNamespace(joint1=124, joint3=73))
        self.assertIsNone(self.actuator.status()["pending"])
        self.assertFalse(self.latch.exists())

    def test_jog_cannot_touch_locked_axes_or_expand_step(self):
        self.actuator.start()
        self.stable()
        for joint, delta in (("J6", 1), ("J2", -1), ("J4", 1), ("J5", 1),
                             ("J1", 2), ("J1", True), ("J1", 1.0)):
            with self.subTest(joint=joint, delta=delta), self.assertRaises(SafetyError):
                self.actuator.jog(joint, delta)
        self.assertEqual(self.ros.sent, [])

    def test_calibrated_feedback_outside_command_limits_is_rejected(self):
        self.actuator.start()
        for j1, j3 in ((-1, 75), (181, 75), (123, 72), (123, 136)):
            self.feedback(j1, j3)
            self.assertEqual(self.actuator.pose, {})
        self.assertEqual(self.ros.sent, [])

    def test_j3_actual_angle_tracks_movement_without_global_offset(self):
        self.actuator.start()
        self.stable(j3=76)
        self.actuator.jog("J3", 1)
        self.assertEqual(self.ros.sent, [(3, 77, 125)])
        self.feedback(j3=76)
        self.assertIsNotNone(self.actuator.status()["pending"])
        self.feedback(j3=77)
        self.assertEqual(self.actuator.status()["raw_pose"]["J3"], 77)
        self.assertIsNone(self.actuator.status()["pending"])

    def test_lower_stop_holds_vertical_without_starving_horizontal(self):
        self.actuator.start()
        self.stable(j3=76)
        with self.assertRaisesRegex(SafetyError, "commissioned_tracking_limit"):
            self.actuator.jog("J3", -1)
        self.actuator.arm()
        self.actuator.track((.7, 1), True)
        self.assertEqual(self.ros.sent, [(1, 121, 125)])

    def test_last_target_prevents_quantization_from_expanding_reverse_step(self):
        self.actuator.start()
        self.stable(j3=76)
        self.actuator.jog("J3", 1)
        self.feedback(j3=77)
        self.stable(j3=76)
        self.clock.advance(.13)
        self.actuator.jog("J3", -1)
        self.assertEqual(self.ros.sent, [(3, 77, 125), (3, 76, 125)])

    def test_constructor_start_feedback_and_close_never_send(self):
        self.assertEqual(self.ros.calls, [])
        self.assertFalse(self.latch.parent.exists())
        self.assertEqual(self.actuator.status()["state"], "DISARMED")
        self.actuator.start()
        self.stable()
        self.assertEqual(self.actuator.status()["state"], "DISARMED")
        self.actuator.track((0.9, 0.5), True)
        self.actuator.close()
        self.actuator.close()
        self.assertEqual(self.ros.sent, [])
        self.assertEqual(self.ros.calls.count("init"), 1)
        self.assertEqual(self.ros.calls.count("shutdown"), 1)
        self.assertEqual(self.ros.calls.count("destroy"), 1)
        self.assertIsNone(self.actuator.lock)
        self.assertFalse(self.latch.exists())

    def test_lifecycle_requires_start_and_forbids_restart_after_close(self):
        with self.assertRaisesRegex(SafetyError, "actuator_not_running"):
            self.actuator.arm()
        with self.assertRaisesRegex(SafetyError, "actuator_not_running"):
            self.actuator.reset()
        self.actuator.start()
        with self.assertRaises(RuntimeError):
            self.actuator.start()
        self.actuator.close()
        with self.assertRaises(RuntimeError):
            self.actuator.start()
        self.actuator.track((0.9, 0.5), True)
        self.assertEqual(self.ros.sent, [])

    def test_one_degree_tracking_encodes_j1_and_j3_at_125_ms(self):
        self.arm()
        self.actuator.track((0.9, 0.5), True)
        self.assertEqual(self.ros.sent, [(1, 121, 125)])
        self.stable(j1=121)
        self.clock.advance(0.13)
        self.actuator.track((0.5, 0.1), True)
        self.assertEqual(self.ros.sent, [(1, 121, 125), (3, 101, 125)])

    def test_negative_steps_have_same_duration_and_hard_limits(self):
        self.arm()
        self.actuator.track((0.1, 0.5), True)
        self.assertEqual(self.ros.sent, [(1, 119, 125)])
        self.stable(j1=119)
        self.clock.advance(0.13)
        self.actuator.track((0.5, 0.9), True)
        self.assertEqual(self.ros.sent[-1], (3, 99, 125))

    def test_encoder_can_accept_gate_noop_without_latching(self):
        self.arm()
        self.actuator.gate.command(self.actuator.token, 1, "J1", 120, received_at=self.clock())
        self.assertEqual(self.ros.sent, [(1, 120, 125)])
        self.assertEqual(self.actuator.status()["state"], "ARMED")

    def test_locked_joint_is_rejected_by_gate_and_independently_by_encoder(self):
        self.arm()
        for joint in ("J2", "J4", "J5", "J6"):
            with self.subTest(joint=joint):
                with self.assertRaises(SafetyError):
                    self.actuator.gate.command(self.actuator.token, 1, joint, 100,
                                               received_at=self.clock())
                with self.assertRaises(ValueError):
                    self.actuator._publish(SafeCommand(joint, 100, 0.125, 1, self.clock()))
        self.assertEqual(self.ros.sent, [])

    def test_feedback_requires_actual_integer_degrees_without_coercion(self):
        self.arm()
        for value in (True, "120", 120.0, 120.5, math.nan, math.inf, FloatSubclass(120)):
            with self.subTest(value=repr(value)):
                self.feedback(j1=value)
                self.assertEqual(self.actuator.status()["state"], "DISARMED")
                self.assertIsNone(self.actuator.token)
                self.assertEqual(self.actuator.pose, {})
                self.stable()
                self.actuator.arm()
        self.actuator._feedback(SimpleNamespace())
        self.assertEqual(self.actuator.status()["state"], "DISARMED")
        self.assertEqual(self.ros.sent, [])

    def test_rc13_lower_stop_keeps_raw_and_normalized_pose_separate(self):
        self.arm()
        self.clock.advance(0.01)
        self.actuator._feedback(SimpleNamespace(
            joint1=123, joint2=116, joint3=73, joint4=0, joint5=90, joint6=40
        ))
        status = self.actuator.status()
        self.assertEqual(status["raw_pose"], {
            "J1": 123, "J2": 116, "J3": 73, "J4": 0, "J5": 90, "J6": 40
        })
        self.assertEqual(status["pose"], {"J1": 123, "J3": 76})
        self.assertEqual(status["state"], "ARMED")
        self.assertIsNone(status["error"])
        self.assertEqual(self.actuator.gate.limits["J3"], (75, 135))
        self.actuator.track((0.5, 0.1), True)
        self.assertEqual(self.ros.sent, [])
        status["raw_pose"]["J3"] = 75
        self.assertEqual(self.actuator.status()["raw_pose"]["J3"], 73)

    def test_tracking_pending_and_velocity_guards(self):
        self.arm()
        self.actuator.track((0.9, 0.5), True)
        issued_at = self.clock()
        self.clock.advance(0.12)
        self.actuator.track((0.9, 0.5), True)
        self.assertIsNone(self.actuator.error)
        self.assertEqual(len(self.ros.sent), 1)
        self.stable(j1=121)
        self.actuator.track((0.9, 0.5), True)
        self.assertEqual(self.ros.sent[-1], (1, 122, 125))
        self.assertGreaterEqual(self.clock() - issued_at, 0.125)

    def test_expired_feedback_or_lease_clears_wrapper_token(self):
        self.arm()
        self.clock.advance(0.501)
        self.actuator.track((0.9, 0.5), True)
        self.assertIsNone(self.actuator.token)
        self.assertEqual(self.actuator.status()["state"], "DISARMED")
        self.stable()
        self.actuator.arm()
        for _ in range(4):
            self.clock.advance(0.25)
            self.feedback()
        self.assertIsNone(self.actuator.token)
        self.assertEqual(self.ros.sent, [])

    def test_stale_control_disarms_and_truthy_values_do_not_count_as_fresh(self):
        self.arm()
        for fresh in (False, None, 1, "true"):
            self.actuator.track((0.9, 0.5), fresh)
            self.assertIsNone(self.actuator.token)
            self.assertEqual(self.actuator.status()["state"], "DISARMED")
            self.actuator.arm()
        self.assertEqual(self.ros.sent, [])

    def test_malformed_centers_cannot_turn_into_valid_direction_commands(self):
        self.arm()
        for center in ((math.nan, 0.5), (math.inf, 0.5), (True, 0.5),
                       (FloatSubclass(0.9), 0.5), ("0.9", 0.5), (-0.1, 0.5),
                       (1.1, 0.5), (0.9,), "hi", {0: 0.9, 1: 0.5}):
            with self.subTest(center=repr(center)):
                self.actuator.track(center, True)
                self.assertIsNone(self.actuator.token)
                self.assertEqual(self.actuator.error, "invalid_tracking_center")
                self.actuator.arm()
        self.assertEqual(self.ros.sent, [])

    def test_none_and_deadband_centers_never_send(self):
        self.arm()
        self.actuator.track(None, True)
        self.actuator.track((0.5, 0.5), True)
        self.assertEqual(self.ros.sent, [])

    def test_supplied_receipt_age_and_future_are_enforced(self):
        self.arm()
        for stamp in (self.clock() - 0.151, self.clock() + 0.001):
            self.actuator.track((0.9, 0.5), True, received_at=stamp)
            self.assertEqual(self.actuator.error, "command_age")
        self.assertEqual(self.ros.sent, [])

    def test_waiting_for_wrapper_lock_does_not_refresh_command_receipt(self):
        self.arm()
        clock_called = threading.Event()
        clock = self.clock

        def receipt_clock():
            stamp = clock()
            clock_called.set()
            return stamp

        self.actuator._clock = receipt_clock
        with self.actuator._control_lock:
            thread = threading.Thread(target=self.actuator.track, args=((0.9, 0.5), True))
            thread.start()
            self.assertTrue(clock_called.wait(timeout=2))
            clock.advance(0.151)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.actuator.error, "command_age")
        self.assertEqual(self.ros.sent, [])

    def test_graph_guards_persist_automatic_stop_without_status_polling(self):
        self.arm()
        for publishers, subscribers in ((0, 1), (2, 1), (1, 0), (1, 2)):
            with self.subTest(publishers=publishers, subscribers=subscribers):
                self.ros.publishers, self.ros.subscribers = publishers, subscribers
                self.actuator.track((0.9, 0.5), True)
                self.assertTrue(self.latch.exists())
                self.assertIsNone(self.actuator.token)
                self.assertEqual(self.actuator.gate.status().reason, "publisher_failure")
                self.actuator.reset()
                self.stable()
                self.actuator.arm()
                self.clock.advance(0.13)
        self.assertEqual(self.ros.sent, [])

    def test_transport_failure_is_latched_and_persisted(self):
        self.arm()

        def fail(message):
            raise RuntimeError("simulated transport failure")

        self.ros.publish_hook = fail
        self.actuator.track((0.9, 0.5), True)
        self.assertTrue(self.latch.exists())
        self.assertIsNone(self.actuator.token)
        self.assertEqual(self.actuator.error, "publisher_failure")
        self.assertEqual(self.ros.sent, [])

    def test_ack_timeout_is_persisted_by_idle_watchdog(self):
        self.arm()
        self.actuator.track((0.9, 0.5), True)
        persisted = threading.Event()
        original = self.actuator._persist_latch

        def persist():
            original()
            persisted.set()

        with patch.object(self.actuator, "_persist_latch", side_effect=persist):
            self.clock.advance(1.0)
            self.assertTrue(persisted.wait(timeout=2), "idle watchdog must latch without UI polling")
        self.assertTrue(self.latch.exists())
        self.assertIsNone(self.actuator.token)
        self.assertEqual(self.actuator.gate.status().reason, "ack_timeout")

    def test_feedback_ack_at_deadline_persists_timeout_immediately(self):
        self.arm()
        self.actuator.track((0.9, 0.5), True)
        self.clock.advance(0.99)
        self.feedback(j1=121)
        self.assertTrue(self.latch.exists())
        self.assertEqual(self.actuator.gate.status().state, "ESTOP")

    def test_latch_survives_close_and_new_instance_until_explicit_reset(self):
        self.arm()
        old_token = self.actuator.token
        self.actuator.estop()
        self.actuator.disarm()
        self.actuator.close()
        replacement = Actuator(clock=self.clock, latch_path=self.latch)
        self.addCleanup(replacement.close)
        self.assertEqual(replacement.gate.status().state, "ESTOP")
        replacement.start()
        with self.assertRaisesRegex(SafetyError, "estop_latched"):
            replacement.arm()
        replacement.reset()
        self.assertFalse(self.latch.exists())
        self.actuator.close()
        self.assertFalse(self.latch.exists(), "closing an old instance twice must not resurrect its latch")
        self.actuator.status()
        self.assertFalse(self.latch.exists(), "diagnostics on a closed instance must not recreate its latch")
        self.assertEqual(replacement.status()["state"], "DISARMED")
        with self.assertRaisesRegex(SafetyError, "feedback_unstable"):
            replacement.arm()
        with self.assertRaisesRegex(SafetyError, "not_armed"):
            replacement.gate.renew(old_token)
        self.assertEqual(self.ros.sent, [])

    def test_close_with_unacknowledged_movement_persists_stop(self):
        self.arm()
        self.actuator.track((0.9, 0.5), True)
        self.actuator.close()
        self.assertTrue(self.latch.exists())
        self.assertEqual(self.actuator.gate.status().state, "ESTOP")

    def test_external_stop_file_blocks_arm_and_tracking(self):
        self.arm()
        self.latch.touch()
        self.actuator.track((0.9, 0.5), True)
        self.assertIsNone(self.actuator.token)
        self.assertEqual(self.actuator.gate.status().state, "ESTOP")
        with self.assertRaisesRegex(SafetyError, "estop_latched"):
            self.actuator.arm()
        self.assertEqual(self.ros.sent, [])

    def test_failed_latch_write_keeps_in_memory_stop(self):
        self.arm()
        with patch("milo_next.actuator.os.open", side_effect=PermissionError("read-only")):
            with self.assertRaisesRegex(SafetyError, "latch_persistence_failed"):
                self.actuator.estop()
            self.assertIsNone(self.actuator.token)
            self.assertEqual(self.actuator.gate.status().state, "ESTOP")
            with self.assertRaises(SafetyError):
                self.actuator.arm()
        self.actuator.status()
        self.assertTrue(self.latch.exists())

    def test_failed_latch_removal_reestops_instead_of_allowing_arm(self):
        self.arm()
        self.actuator.estop()
        with patch.object(Path, "unlink", side_effect=PermissionError("read-only")):
            with self.assertRaisesRegex(SafetyError, "latch_reset_failed"):
                self.actuator.reset()
        self.assertTrue(self.latch.exists())
        self.assertEqual(self.actuator.gate.status().state, "ESTOP")
        with self.assertRaises(SafetyError):
            self.actuator.arm()

    def test_reset_and_concurrent_estop_cannot_remove_new_stop_file(self):
        self.arm()
        self.actuator.estop()
        entered = threading.Event()
        release = threading.Event()
        stop_started = threading.Event()
        original_reset = self.actuator.gate.reset
        failures = []

        def paused_reset():
            original_reset()
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("test reset not released")

        def reset():
            try:
                self.actuator.reset()
            except BaseException as error:
                failures.append(error)

        def stop():
            stop_started.set()
            self.actuator.estop()

        with patch.object(self.actuator.gate, "reset", side_effect=paused_reset):
            resetting = threading.Thread(target=reset)
            stopping = threading.Thread(target=stop)
            resetting.start()
            try:
                self.assertTrue(entered.wait(timeout=2))
                stopping.start()
                self.assertTrue(stop_started.wait(timeout=2))
            finally:
                release.set()
                resetting.join(timeout=2)
                if stopping.ident is not None:
                    stopping.join(timeout=2)
        self.assertFalse(resetting.is_alive())
        self.assertFalse(stopping.is_alive())
        self.assertEqual(failures, [])
        self.assertTrue(self.latch.exists())
        self.assertEqual(self.actuator.gate.status().state, "ESTOP")

    def test_estop_and_feedback_threads_finish_after_inflight_publish(self):
        self.arm()
        entered = threading.Event()
        release = threading.Event()
        failures = []

        def slow_publish(message):
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("test publisher not released")

        def run(operation):
            try:
                operation()
            except BaseException as error:
                failures.append(error)

        self.ros.publish_hook = slow_publish
        tracking = threading.Thread(target=run, args=(lambda: self.actuator.track((0.9, 0.5), True),))
        tracking.start()
        others = []
        try:
            self.assertTrue(entered.wait(timeout=2))
            others = [threading.Thread(target=run, args=(self.actuator.estop,)),
                      threading.Thread(target=run, args=(lambda: self.feedback(j1=121),))]
            for thread in others:
                thread.start()
        finally:
            release.set()
            for thread in [tracking] + others:
                thread.join(timeout=2)
        self.assertTrue(all(not thread.is_alive() for thread in [tracking] + others))
        self.assertEqual(failures, [])
        self.assertEqual(self.actuator.gate.status().state, "ESTOP")
        self.assertTrue(self.latch.exists())
        self.actuator.track((0.9, 0.5), True)
        self.assertEqual(len(self.ros.sent), 1)

    def test_close_joins_outside_locks_so_last_feedback_can_finish(self):
        self.arm()
        self.ros.shutdown_feedback = True
        self.actuator.close()
        self.assertTrue(self.ros.callback_completed.is_set())
        self.assertFalse(self.actuator.thread.is_alive())
        self.assertFalse(self.actuator.watch_thread.is_alive())
        self.assertEqual(self.ros.sent, [])

    def test_second_instance_cannot_start_or_reset_first_instances_latch(self):
        self.arm()
        self.actuator.estop()
        other = Actuator(clock=self.clock, latch_path=self.latch)
        self.addCleanup(other.close)
        with self.assertRaises(BlockingIOError):
            other.start()
        self.assertIsNone(other.lock)
        with self.assertRaisesRegex(SafetyError, "actuator_not_running"):
            other.reset()
        self.assertTrue(self.latch.exists())
        self.assertEqual(self.ros.calls.count("init"), 1)
        self.assertEqual(self.ros.calls.count("shutdown"), 0)

    def test_partial_start_failure_releases_resources_and_ownership(self):
        self.ros.setup_error = RuntimeError("publisher creation failed")
        with self.assertRaisesRegex(RuntimeError, "publisher creation failed"):
            self.actuator.start()
        self.assertIsNone(self.actuator.lock)
        self.assertIsNone(self.actuator.node)
        self.assertEqual(self.ros.calls.count("shutdown"), 1)
        self.assertEqual(self.ros.calls.count("destroy"), 1)
        other = Actuator(clock=self.clock, latch_path=self.latch)
        self.addCleanup(other.close)
        self.ros.setup_error = None
        other.start()
        self.assertEqual(self.ros.sent, [])

    def test_feedback_executor_failure_latches_and_persists_stop(self):
        self.arm()
        persisted = threading.Event()
        original = self.actuator._persist_latch

        def persist():
            original()
            persisted.set()

        self.ros.spin_error = RuntimeError("feedback executor failed")
        with patch.object(self.actuator, "_persist_latch", side_effect=persist):
            self.ros.stop.set()
            self.assertTrue(persisted.wait(timeout=2))
        self.assertIsNone(self.actuator.token)
        self.assertTrue(self.latch.exists())
        self.assertEqual(self.actuator.gate.status().state, "ESTOP")
        self.assertEqual(self.ros.sent, [])


if __name__ == "__main__":
    unittest.main()
