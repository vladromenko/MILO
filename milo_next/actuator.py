"""ROS adapter for the safety gate, with an on-disk ESTOP latch.

Construction is inert. start() exclusively owns the local actuator lock, starts
ROS feedback and a 50 ms watchdog, and remains DISARMED. Only explicit arm()
enables tracking. close() is terminal and never publishes a movement.

The wrapper lock is always acquired before the gate lock. _publish() runs under
the gate lock and must never acquire the wrapper lock. Thread joins happen
outside both locks. Reset, stop persistence, tracking, and feedback are serialized
so reset cannot erase a concurrent stop. Persistent latch failures fail closed
and are reported to the caller. No code here clears ESTOP implicitly.

Tests inject a clock and temporary latch_path and replace ROS modules with fakes.
track() stamps local receipt before acquiring locks; queued integrations must
provide received_at from their trusted local ingress, in that same clock domain.
"""
import fcntl
import math
import logging
import os
from pathlib import Path
import threading
import time
from dataclasses import asdict
from types import MappingProxyType

from .safety import ActuatorSafety, SafeCommand, SafetyError
from .settings import ROOT
from .firmware_link import FirmwareLink

LOG = logging.getLogger(__name__)
TRACKING_LIMITS = MappingProxyType({"J1": (0, 180), "J3": (76, 135)})
MANUAL_LIMITS = MappingProxyType({'J1': (0, 180), 'J2': (90, 165),
                                'J3': (0, 135), 'J4': (-30, 115), 'J5': (70, 110)})
J4_FIRMWARE_BLOCK = 'J4_negative_angles_rejected_by_controller_firmware'


def encode_manual_command(command, *, recovery=False, firmware_v6=False):
    if type(command) is not SafeCommand or type(command.joint) is not str or command.joint not in MANUAL_LIMITS:
        raise ValueError('not a permitted manual posture joint')
    low, high = MANUAL_LIMITS[command.joint]
    if recovery is True and command.joint == 'J4':
        low, high = -43, -30
    # MILO2 V4's arm_angle_valid accepts J4 commands only at 0..180,
    # although Arm_Position_To_Angle can report negative measured angles.
    if command.joint == 'J4' and not firmware_v6:
        low = max(0, low)
    if (type(command.target) not in (int, float) or not math.isfinite(command.target)
            or command.target != int(command.target) or not low <= command.target <= high):
        raise ValueError('invalid manual target')
    if type(command.duration_s) not in (int, float) or not .25 <= command.duration_s <= 1:
        raise ValueError('invalid manual duration')
    return int(command.joint[1:]), int(command.target), math.ceil(command.duration_s * 1000)


def encode_command(command):
    """Independent fixed guard: integer J1/J3 only, duration rounded UP to ms.

    The gate enforces the chosen profile's step and speed. A 125 ms floor also
    preserves the strict single-degree commissioning limit. Never round angles.
    """
    bounds = {"J1": (1, 0, 180), "J3": (3, 75, 135)}
    if type(command) is not SafeCommand:
        raise ValueError("expected a SafeCommand")
    if type(command.joint) is not str or command.joint not in bounds:
        raise ValueError("permanently locked joint")
    joint, low, high = bounds[command.joint]
    target = command.target
    if type(target) not in (int, float) or not low <= target <= high:
        raise ValueError("invalid absolute integer target")
    if not math.isfinite(target) or target != int(target):
        raise ValueError("invalid absolute integer target")
    duration = command.duration_s
    if type(duration) not in (int, float) or not 0.12 <= duration <= 1:
        raise ValueError("unsafe travel duration")
    if not math.isfinite(duration):
        raise ValueError("unsafe travel duration")
    return joint, int(target), max(125, math.ceil(duration * 1000))


class Actuator:
    def __init__(self, *, clock=time.monotonic, latch_path=None, integer_feedback=False,
                 firmware_v6=False):
        self._clock = clock
        self._control_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._watch_stop = threading.Event()
        self._started = False
        self._closed = False
        self._cleanup_complete = False
        self._ros_initialized = False
        self.gate = ActuatorSafety(self._publish, clock=clock, integer_feedback=integer_feedback)
        self.auxiliary = {joint: ActuatorSafety(self._publish_manual, clock=clock,
                         integer_feedback=True, manual_joint=joint) for joint in MANUAL_LIMITS}
        self.pose = {}
        self.raw_pose = {}
        self.targets = {}
        self.node = None
        self.publisher = None
        self.thread = None
        self.watch_thread = None
        self.lock = None
        self.token = None
        self.sequence = 0
        self.error = None
        self.tracking_error = None
        self.firmware = FirmwareLink(self._publish_control, clock=clock) if firmware_v6 else None
        self.control_publisher = None
        self._firmware_enable_until = 0
        self.latch = Path(latch_path) if latch_path is not None else ROOT / "data/ESTOP"
        if self.latch.exists():
            self.gate.estop()
            for gate in self.auxiliary.values():
                gate.estop()

    def start(self):
        with self._lifecycle_lock:
            with self._control_lock:
                if self._closed or self._started:
                    raise RuntimeError("actuator already started or closed")
            try:
                with self._control_lock:
                    self.latch.parent.mkdir(parents=True, exist_ok=True)
                    self.lock = open(self.latch.parent / "actuator.lock", "a")
                    fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    import rclpy
                    from rclpy.node import Node
                    from arm_msgs.msg import ArmJoint, ArmJoints
                    self.rclpy = rclpy
                    # aiohttp owns process signals and closes us before ROS shutdown.
                    rclpy.init(signal_handler_options=rclpy.SignalHandlerOptions.NO)
                    self._ros_initialized = True
                    self.message_type = ArmJoint
                    self.node = Node("milo_next_actuator")
                    self.publisher = self.node.create_publisher(ArmJoint, "/arm_joint", 10)
                    self.subscription = self.node.create_subscription(
                        ArmJoints, "/arm6_feedback", self._feedback, 1
                    )
                    if self.firmware is not None:
                        from std_msgs.msg import Int32
                        self.control_message_type = Int32
                        self.control_publisher = self.node.create_publisher(Int32, '/arm_control', 1)
                        self.version_subscription = self.node.create_subscription(
                            Int32, '/arm_firmware_version', self._firmware_version, 1)
                        self.state_subscription = self.node.create_subscription(
                            Int32, '/arm_control_state', self._firmware_state, 1)
                    self.thread = threading.Thread(
                        target=self._spin, name="actuator-feedback", daemon=True
                    )
                    self.watch_thread = threading.Thread(
                        target=self._watchdog, name="actuator-watchdog", daemon=True
                    )
                    self._sync_state()
                    self._started = True
                    self.thread.start()
                    self.watch_thread.start()
            except BaseException:
                self._close()
                raise

    def _require_running(self):
        if not self._started or self._closed:
            raise SafetyError("actuator_not_running")

    def _publish_control(self, action):
        self._require_running()
        if (self.control_publisher.get_subscription_count() != 1
                or self.node.count_publishers('/arm_control') != 1):
            raise SafetyError('controller_control_ownership_unavailable')
        msg = self.control_message_type()
        msg.data = action
        self.control_publisher.publish(msg)

    def _firmware_version(self, message):
        with self._control_lock:
            if self._started and not self._closed:
                self.firmware.receive_version(message.data)

    def _firmware_state(self, message):
        with self._control_lock:
            if self._started and not self._closed:
                self.firmware.receive_state(message.data)

    def _prepare_firmware(self):
        if self.firmware is not None and not self.firmware.requested:
            self.firmware.enable()
            # Manual HTTP commissioning is a two-phase request: the first call
            # asks the MCU for a lease, the second consumes its fresh ACK.
            self._firmware_enable_until = self._clock() + 5

    def _tick_firmware(self, state):
        if self.firmware is None:
            return
        if state.state == 'ESTOP':
            self.firmware.inhibit('host_estop')
        elif (state.state != 'ARMED' and state.pending is None
              and not any(g.status().pending for g in self.auxiliary.values())
              and self._clock() >= self._firmware_enable_until):
            self.firmware.inhibit()
        self.firmware.tick()

    def _persist_latch(self):
        try:
            self.latch.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.latch, os.O_WRONLY | os.O_CREAT, 0o600)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._sync_directory()
        except OSError as exc:
            self.gate.estop()
            self.token = None
            self.error = "latch_persistence_failed"
            raise SafetyError(self.error) from exc

    def _sync_directory(self):
        descriptor = os.open(self.latch.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _sync_state(self):
        """Caller holds the wrapper lock; persist ALL gate stop paths immediately."""
        if self._cleanup_complete:
            return self.gate.status()
        if self.latch.exists():
            self.gate.estop()
        try:
            state = self.gate.tick()
            auxiliary = [gate.tick() for gate in self.auxiliary.values()]
            if state.state == 'ESTOP' or any(item.state == 'ESTOP' for item in auxiliary):
                if state.state != 'ESTOP':
                    self.error = next(item.reason for item in auxiliary if item.state == 'ESTOP')
                    self.gate.estop()
                for gate in self.auxiliary.values():
                    gate.estop()
                state = self.gate.status()
        except SafetyError:
            self.token = None
            self.gate.estop()
            self._persist_latch()
            raise
        if state.state != "ARMED":
            self.token = None
        if state.state == "ESTOP" and not self.latch.exists():
            self._persist_latch()
        self._tick_firmware(state)
        return state

    def _spin(self):
        try:
            self.rclpy.spin(self.node)
        except Exception as exc:
            with self._control_lock:
                self.error = str(exc)
        finally:
            with self._control_lock:
                if not self._closed:
                    self.gate.estop()
                    self.token = None
                    self._persist_latch()

    def _watchdog(self):
        while not self._watch_stop.wait(0.05):
            try:
                self.status()
            except Exception as exc:
                with self._control_lock:
                    self.gate.estop()
                    self.token = None
                    self.error = str(exc)

    def _feedback(self, message):
        received_at = self._clock()
        with self._control_lock:
            if not self._started or self._closed:
                return
            self.raw_pose = {
                f"J{joint}": getattr(message, f"joint{joint}", None)
                for joint in range(1, 7)
            }
            for joint, gate in self.auxiliary.items():
                value = self.raw_pose[joint]
                try:
                    gate.update_feedback({joint: value} if type(value) is int else {}, received_at=received_at)
                except SafetyError:
                    pass
            try:
                pose = {"J1": message.joint1, "J3": message.joint3}
                if any(type(value) is not int for value in pose.values()):
                    pose = {}
                else:
                    # The measured lower-stop band is local, not a global offset.
                    # Outside it, preserve the MCU's actual angle unchanged.
                    if 73 <= pose['J3'] < 76:
                        pose['J3'] = 76
            except AttributeError:
                pose = {}
            try:
                self.gate.update_feedback(pose, received_at=received_at)
                self.pose = pose
                self.tracking_error = None
                if self.error == "invalid_feedback":
                    self.error = None
            except SafetyError as exc:
                self.pose = {}
                self.tracking_error = str(exc)
            finally:
                self._sync_state()

    def _publish_manual(self, command):
        return self._publish(command, manual=True)

    def _publish(self, command, *, manual=False):
        self._require_running()
        auxiliary = manual
        recovering = (command.joint == 'J4' and type(self.raw_pose.get('J4')) is int
                      and -43 <= self.raw_pose['J4'] < command.target <= -30)
        joint, target, duration = (encode_manual_command(command, recovery=recovering,
            firmware_v6=self.firmware is not None) if auxiliary else encode_command(command))
        gate = self.auxiliary[command.joint] if auxiliary else self.gate
        low, high = (MANUAL_LIMITS if auxiliary else TRACKING_LIMITS)[command.joint]
        if recovering:
            low, high = -43, -30
        if not low <= target <= high:
            raise RuntimeError("outside commissioned tracking range")
        raw = self.raw_pose.get(command.joint)
        if type(raw) is not int:
            raise RuntimeError("raw feedback unavailable")
        distance = abs(target - raw) if auxiliary else max(abs(target - raw), abs(target - self.pose[command.joint]))
        if distance > gate.max_step:
            raise RuntimeError("raw feedback step exceeded")
        duration = max(duration, math.ceil(distance / gate.max_velocity * 1000))
        if self.publisher.get_subscription_count() != 1:
            raise RuntimeError("expected exactly one hardware subscriber")
        if self.node.count_publishers("/arm_joint") != 1:
            raise RuntimeError("another actuator publisher exists")
        msg = self.message_type()
        msg.id, msg.joint, msg.time = joint, target, duration
        if self.firmware is not None:
            self.firmware.require_idle()
        self.publisher.publish(msg)
        for peer in (self.gate, *self.auxiliary.values()):
            if peer is not gate:
                peer.observe_external_command(command)
        if self.firmware is not None:
            # The callback shares _control_lock, so it cannot observe the MCU's
            # acceptance before the host records this successful publication.
            self.firmware.command_sent()
        self.targets[command.joint] = target
        LOG.info("Motion %s raw=%s target=%s duration_ms=%s sequence=%s",
                 command.joint, raw, target, duration, command.sequence)

    def arm(self):
        with self._control_lock:
            self._require_running()
            self._sync_state()
            if any(gate.status().pending is not None for gate in self.auxiliary.values()):
                raise SafetyError('pending_manual_motion')
            self.token = self.gate.arm("operator-tracking")
            try:
                self._prepare_firmware()
            except Exception:
                self.gate.disarm()
                self.token = None
                raise
            self.sequence = 0

    def disarm(self, *, force=False):
        with self._control_lock:
            manual_handshake = (self.firmware is not None and self.firmware.requested
                                and self.token is None
                                and self._clock() < self._firmware_enable_until)
            if self.firmware is not None and (force or not manual_handshake):
                self.firmware.inhibit()
            self.token = None
            self.gate.disarm()
            for gate in self.auxiliary.values():
                gate.disarm()
            self._sync_state()

    def hold(self):
        """Keep existing permission during a bounded camera gap; never publish."""
        with self._control_lock:
            self._require_running()
            self._sync_state()
            if self.token is not None:
                self.gate.renew(self.token)

    def jog(self, joint, delta):
        """Explicit single-degree commissioning step; never leaves tracking armed."""
        received_at = self._clock()
        with self._control_lock:
            self._require_running()
            state = self._sync_state()
            if any(gate.status().pending is not None for gate in self.auxiliary.values()):
                raise SafetyError('pending_manual_motion')
            if type(joint) is not str or joint not in {"J1", "J3"}:
                raise SafetyError("locked_or_unknown_joint")
            if type(delta) is not int or delta not in {-1, 1}:
                raise SafetyError("jog_requires_single_degree")
            if state.state != "DISARMED":
                raise SafetyError("jog_requires_disarmed")
            self._prepare_firmware()
            if self.firmware is not None:
                self.firmware.require_idle()
            target = self.targets.get(joint, self.pose.get(joint, 0)) + delta
            low, high = TRACKING_LIMITS[joint]
            if not low <= target <= high:
                raise SafetyError("commissioned_tracking_limit")
            token = self.gate.arm("operator-single-step")
            try:
                self.gate.command(token, 1, joint, target,
                                  received_at=received_at)
            finally:
                self.gate.disarm()
                self._sync_state()

    def estop(self):
        with self._control_lock:
            self.gate.estop()
            for gate in self.auxiliary.values():
                gate.estop()
            self.token = None
            self._persist_latch()
            if self.firmware is not None:
                self.firmware.inhibit('host_estop')

    def nudge(self, joint, direction, *, _recovery=False, _scan=False, goal=None, speed=None):
        """Manual steps use the tracking profile's measured minimum movement."""
        received_at = self._clock()
        with self._control_lock:
            self._require_running()
            state = self._sync_state()
            if type(joint) is not str or joint not in {'J1', 'J2', 'J3', 'J4', 'J5'}:
                raise SafetyError('locked_or_unknown_joint')
            if type(direction) is not int or direction not in {-1, 1}:
                raise SafetyError('invalid_nudge_direction')
            if state.state != 'DISARMED':
                raise SafetyError('nudge_requires_disarmed')
            if state.pending is not None or any(gate.status().pending is not None for gate in self.auxiliary.values()):
                raise SafetyError('pending_manual_motion')
            auxiliary = joint in self.auxiliary
            gate = self.auxiliary[joint] if auxiliary else self.gate
            # A posture step needs its own feedback, not the face-tracking pose.
            # Global ESTOP and pending-motion checks above still apply to all axes.
            if not gate.status().feedback_stable:
                raise SafetyError('feedback_unstable')
            current = self.raw_pose.get(joint) if auxiliary else self.pose.get(joint)
            if type(current) is not int:
                raise SafetyError('feedback_unavailable')
            if _scan and joint != 'J1':
                raise SafetyError('scan_joint_not_allowed')
            step = 2 if _recovery else (4 if joint in {'J3', 'J4'} or _scan else 3 if joint == 'J1' else 2)
            target = current + direction * step
            if goal is not None:
                self.validate_target(joint, goal)
                if (goal - current) * direction <= 0:
                    raise SafetyError('invalid_target_direction')
                target = min(target, goal) if direction > 0 else max(target, goal)
            previous = gate.previous_target(joint)
            previous = current if previous is None else previous
            target = max(previous - int(gate.max_step), min(previous + int(gate.max_step), target))
            if (target - current) * direction <= 0:
                raise SafetyError('feedback_unstable')
            if joint == 'J4' and self.firmware is None and 0 < current < 4 and direction == -1:
                target = 0
            if joint == 'J4' and (current < 0 or target < 0) and self.firmware is None:
                raise SafetyError(J4_FIRMWARE_BLOCK)
            low, high = (MANUAL_LIMITS if auxiliary else TRACKING_LIMITS)[joint]
            if joint == 'J2' and direction == -1 and target > high and current <= high + gate.max_step:
                target = high
            if _recovery and joint == 'J4' and self.firmware is not None:
                if not -43 <= current < -30 or direction != 1:
                    raise SafetyError('J4_recovery_toward_minus_30_only')
                low, high = -43, -30
                target = min(target, -30)
            if not low <= target <= high:
                raise SafetyError('commissioned_tracking_limit')
            if goal is not None and (target - goal) * direction > 0:
                raise SafetyError('operator_recovery_required')
            self._prepare_firmware()
            if self.firmware is not None:
                self.firmware.require_idle()
            token = gate.arm('operator-bounded-step')
            try:
                gate.command(token, 1, joint, target, received_at=received_at, speed=speed)
            finally:
                gate.disarm()
                self._sync_state()

    def validate_target(self, joint, target):
        if type(joint) is not str or joint not in MANUAL_LIMITS or type(target) is not int:
            raise SafetyError('invalid_manual_target')
        low, high = MANUAL_LIMITS[joint]
        if joint == 'J4' and self.firmware is None:
            low = max(0, low)
        if not low <= target <= high:
            raise SafetyError('commissioned_tracking_limit')
        current = self.raw_pose.get(joint)
        if joint == 'J2' and target > 115 and (current is None or target >= current):
            raise SafetyError('J2_box_collision_guard')

    def rebase(self):
        with self._control_lock:
            self._require_running()
            state = self._sync_state()
            if state.state != 'DISARMED' or state.pending is not None:
                raise SafetyError('recovery_requires_disarmed')
            if any(g.status().pending is not None for g in self.auxiliary.values()):
                raise SafetyError('pending_manual_motion')
            ready = [joint for joint, gate in self.auxiliary.items() if gate.status().feedback_stable]
            if not ready:
                raise SafetyError('feedback_unstable')
            for joint in ready:
                self.auxiliary[joint].rebase()
            if self.gate.status().feedback_stable:
                self.gate.rebase()
            self.targets = {j: self.raw_pose[j] for j in ready}
            return ready

    def reset(self):
        with self._control_lock:
            self._require_running()
            self._sync_state()
            if self.firmware is not None:
                self.firmware.inhibit()
                self.firmware.reset_fault()
            self.gate.reset()
            for gate in self.auxiliary.values():
                gate.reset()
            self.token = None
            self.pose = {}
            try:
                self.latch.unlink(missing_ok=True)
                self._sync_directory()
            except OSError as exc:
                self.gate.estop()
                self._persist_latch()
                self.error = "latch_reset_failed"
                raise SafetyError(self.error) from exc
            self.error = None

    def recover_j4_step(self):
        """An explicit small recovery step, available only with V6 telemetry."""
        with self._control_lock:
            self._require_running()
            if self.firmware is None:
                raise SafetyError(J4_FIRMWARE_BLOCK)
            return self.nudge('J4', 1, _recovery=True)

    def status(self):
        with self._control_lock:
            state = self._sync_state()
            manual = {joint: {**asdict(gate.status()), 'limits': list(MANUAL_LIMITS[joint]),
                             'pose': self.raw_pose.get(joint)} for joint, gate in self.auxiliary.items()}
            manual['J4']['command_limits'] = [0, MANUAL_LIMITS['J4'][1]]
            manual['J4']['blocked_reason'] = (J4_FIRMWARE_BLOCK
                if type(self.raw_pose.get('J4')) is int and self.raw_pose['J4'] < 0 else None)
            if self.firmware is not None:
                manual['J4']['command_limits'] = list(MANUAL_LIMITS['J4'])
                manual['J4']['blocked_reason'] = None
            motion_fault = None
            for joint, item in manual.items():
                if item['reason'] == 'ack_timeout' and item['pending'] is not None:
                    target, measured = item['pending']['target'], item['pose']
                    motion_fault = {'reason': 'ack_timeout', 'joint': joint,
                                    'target': target, 'measured': measured,
                                    'feedback_age_s': item['feedback_age_s'],
                                    'timeout_s': self.auxiliary[joint].ack_timeout}
                    break
            return {**asdict(state), "pose": dict(self.pose),
                    "firmware": self.firmware.snapshot() if self.firmware is not None else None,
                    "tracking_published_count": state.published_count,
                    "published_count": state.published_count + sum(item['published_count'] for item in manual.values()),
                    "raw_pose": dict(self.raw_pose), "targets": dict(self.targets), "error": self.error,
                    "tracking_error": self.tracking_error,
                    "motion_fault": motion_fault,
                    "manual": manual}

    def track(self, center, fresh, *, received_at=None):
        received_at = self._clock() if received_at is None else received_at
        with self._control_lock:
            if fresh is not True:
                self.disarm()
                return
            if not self._started or self._closed:
                return
            self._sync_state()
            if self.token is None:
                return
            if center is not None and (
                type(center) not in (tuple, list) or len(center) != 2
                or any(type(value) not in (int, float) or not 0 <= value <= 1
                       for value in center)
            ):
                self.error = "invalid_tracking_center"
                self.disarm()
                return
            try:
                self.gate.renew(self.token)
                if center is None:
                    return
                state = self.gate.status()
                if not state.feedback_stable:
                    return
                x, y = center
                errors = {"J1": x - 0.5, "J3": 0.5 - y}
                if state.pending is not None:
                    if self.firmware is not None:
                        return
                    pending = state.pending
                    error = errors[pending.joint]
                    direction = pending.target - self.pose[pending.joint]
                    if abs(error) >= .05 and error * direction > 0:
                        try:
                            retry = self.gate.retry_pending(self.token, received_at=received_at)
                            self.sequence = retry.sequence
                            LOG.warning('One same-target retry: %s -> %s', retry.joint, retry.target)
                        except SafetyError as exc:
                            if str(exc) not in {'retry_not_ready', 'retry_motion_observed'}:
                                raise
                    return
                if self.firmware is not None:
                    self.firmware.require_idle()
                for joint in sorted(errors, key=lambda j: abs(errors[j]), reverse=True):
                    if abs(errors[joint]) < 0.05 or joint not in self.pose:
                        continue
                    step = int(self.gate.max_step)
                    if step > 1:
                        minimum = 4 if joint == 'J3' else 3
                        step = min(step, max(minimum, round(abs(errors[joint]) * 24)))
                    direction = 1 if errors[joint] > 0 else -1
                    previous = self.targets.get(joint, self.pose[joint])
                    target = self.pose[joint] + direction * step
                    target = max(previous - step, min(previous + step, target))
                    raw = self.raw_pose.get(joint, self.pose[joint])
                    target = max(raw - int(self.gate.max_step),
                                 min(raw + int(self.gate.max_step), target))
                    low, high = TRACKING_LIMITS[joint]
                    target = max(low, min(high, target))
                    if target == previous or (target - self.pose[joint]) * direction <= 0:
                        continue
                    # Do not command boundary corrections inside the measured
                    # precision band: those may produce no observable motion.
                    if (self.gate.max_step > 1
                            and abs(target - self.pose[joint]) <= self.gate.ack_tolerance(joint)):
                        continue
                    self.sequence += 1
                    self.gate.command(self.token, self.sequence, joint, target, received_at=received_at)
                    self.error = None
                    break
            except SafetyError as exc:
                self.error = str(exc)
            finally:
                self._sync_state()

    def close(self):
        with self._lifecycle_lock:
            self._close()

    def _close(self):
        with self._control_lock:
            if self._cleanup_complete:
                return
            if self.firmware is not None and self._started:
                try:
                    self.firmware.inhibit('host_close')
                except Exception as exc:
                    self.error = str(exc)
            self._closed = True
            self._started = False
            self.token = None
            self.gate.disarm()
            for gate in self.auxiliary.values():
                gate.disarm()
            self._watch_stop.set()
            try:
                state = self._sync_state()
                if state.pending is not None or any(gate.status().pending is not None for gate in self.auxiliary.values()):
                    # A restart must not forget an unacknowledged physical movement.
                    self.gate.estop()
                    self._persist_latch()
            except SafetyError as exc:
                self.error = str(exc)
        # A feedback callback or watchdog may need the wrapper lock to finish.
        if self._ros_initialized:
            self.rclpy.shutdown()
            self._ros_initialized = False
        for thread in (self.thread, self.watch_thread):
            if thread is not None and thread.ident is not None:
                thread.join(timeout=3)
                if thread.is_alive():
                    raise RuntimeError("actuator thread did not stop; ownership lock retained")
        if self.node is not None:
            self.node.destroy_node()
            self.node = None
            self.publisher = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None
        self._cleanup_complete = True
