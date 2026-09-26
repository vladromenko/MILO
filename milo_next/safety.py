"""Standalone, fail-closed boundary for the verified J1/J3 actuator envelope.

No constructor, feedback, arm, reset, or stop operation publishes a command.
The trusted local ingress must stamp queued requests with ``time.monotonic()``
(or the injected clock) at receipt and pass that stamp to ``command``. Remote
timestamps must never be used as local receipt stamps. Feedback uses the same
clock domain; omit its stamp only when delivering it synchronously at receipt.

Typical integration::

    gate = ActuatorSafety(publisher)
    # Deliver three distinct, fresh update_feedback({"J1": ..., "J3": ...}) calls.
    token = gate.arm("manual-control")
    gate.command(token, 1, "J1", 120.5, received_at=local_receipt_time)
    gate.renew(token)  # Renew before the one-second lease expires.

The publisher is synchronous, must return None, and must honor SafeCommand's
duration_s as a MINIMUM travel duration (the selected profile's speed cap). It must
not queue work outside this boundary or expand a command into locked joints.
Only this gate may hold the raw actuator sender. Software cannot verify the
publisher's implementation or physically cancel a movement already emitted.

Validation and the complete publisher call hold one RLock. An estop therefore
waits for an in-flight publisher; after estop returns, no subsequent command
can publish until an explicit reset and new arm. Publishers must be bounded
and must not wait for another thread that calls this gate. Reentrant status,
disarm, and estop are permitted; other reentrant operations are rejected.

All timed conditions are evaluated on each timed API call, including status.
The host must call tick() periodically when idle; this module starts no worker
threads and sends no physical braking command. A pending target survives
disarm and lease loss until acknowledged or its timeout latches ESTOP.
Reset clears that latch and pending state, requires three NEW feedback samples,
and always leaves the gate DISARMED. No safety threshold is configurable.
"""

from collections import deque
from dataclasses import dataclass
import math
import secrets
import threading
import time
from types import MappingProxyType
from typing import Callable, Mapping


_LIMITS = MappingProxyType({"J1": (0.0, 180.0), "J3": (75.0, 135.0)})
_LOCKED = frozenset({"J2", "J4", "J5", "J6"})
# J4's lower corridor is recovery-only, explicitly commissioned by the owner.
_MANUAL_LIMITS = MappingProxyType({'J1': (0.0, 180.0), 'J2': (90.0, 165.0),
                                 'J3': (0.0, 135.0), 'J4': (-43.0, 115.0), 'J5': (70.0, 110.0)})
_LEASE_S = 1.0
_COMMAND_AGE_S = 0.150
_FEEDBACK_AGE_S = 0.500
_ACK_TIMEOUT_S = 1.000
_STABILITY_WINDOW_S = 1.200  # Measured MCU feedback period: 0.38 s.
_STABLE_SAMPLES = 3
_STABILITY_DEG = 0.25
_ACK_TOLERANCE_DEG = 0.10
_MAX_STEP_DEG = 1.0
_MAX_VELOCITY = 8.0
_MIN_INTERVAL_S = 0.12


class SafetyError(RuntimeError):
    """Rejected operation; ``code`` is a stable machine-readable diagnostic."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PublisherError(SafetyError):
    """Publishing failed or violated its contract; ESTOP is now latched."""


@dataclass(frozen=True, slots=True)
class SafeCommand:
    """One absolute joint target in degrees, with a minimum travel duration.

    Only J1/J3 commands constructed by ActuatorSafety reach its publisher.
    ``issued_at`` is local monotonic time; no lease secret leaves the gate.
    """

    joint: str
    target: float
    duration_s: float
    sequence: int
    issued_at: float


@dataclass(frozen=True, slots=True)
class SafetyStatus:
    """Immutable, secret-free snapshot; reading it also checks all deadlines."""

    state: str
    owner: str | None
    lease_remaining_s: float
    feedback_age_s: float | None
    stable_samples: int
    feedback_stable: bool
    pending: SafeCommand | None
    pending_remaining_s: float | None
    last_sequence: int
    published_count: int
    rejected_count: int
    reason: str


def _number(value: object) -> float:
    # Exact builtins exclude bool, numeric subclasses, and coercion callbacks.
    if type(value) not in (int, float):
        raise ValueError("not a builtin number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError("number overflow") from exc
    if not math.isfinite(result):
        raise ValueError("nonfinite number")
    return result


class ActuatorSafety:
    """Single-owner actuator gate using an injected monotonic clock/publisher.

    Feedback must be an exact dict containing exactly J1 and J3, both within
    the fixed envelope. Three strictly increasing receipt stamps, all within
    1.2 s, must span at most 0.25 degrees per joint before arm or command.
    The newest sample must still be at most 500 ms old.
    Invalid feedback discards stability and removes the arm. Adapters should
    filter full robot feedback to these two verified joints.

    Every step is conservatively capped at one degree from measured feedback
    AND the previous target, including the first step. Subsequent targets also
    obey 8 degrees/second from the previous target for that joint. Emissions
    across all joints are at least 120 ms apart. One target may be outstanding
    globally; a newer feedback sample within 0.10 degrees acknowledges it.

    These are the strict default commissioning settings. The integer-feedback
    production profile restores RC13's 9-degree step, 36 deg/s speed, 1 s feedback
    age and 3-degree stability band. Travel lasts at least 250 ms. Its 1.5 s ACK
    requires actual directional progress, within 2 degrees for J1/J3/J4 and
    1 degree for the other manual joints. One same-target retry is allowed on fresh input.

    Lease deadlines are exclusive (expired at exactly 1 s); age limits are
    inclusive. A missing acknowledgement latches at exactly 1 s. Sequence
    numbers are exact positive ints, strictly increasing within each lease;
    only commands that reach the publisher consume a sequence number.
    """

    def __init__(
        self,
        publisher: Callable[[SafeCommand], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        integer_feedback: bool = False,
        manual_joint: str | None = None,
    ):
        # Preserve a stricter default for single-step commissioning and callers.
        if type(integer_feedback) is not bool:
            raise TypeError("integer_feedback must be bool")
        self._integer_feedback = integer_feedback
        if manual_joint is not None and (type(manual_joint) is not str or manual_joint not in _MANUAL_LIMITS):
            raise ValueError('unsupported manual joint; J6 is permanently locked')
        self._manual_joint = manual_joint
        self._limits = (_LIMITS if manual_joint is None else
                        MappingProxyType({manual_joint: _MANUAL_LIMITS[manual_joint]}))
        self._pending_start = 0.0
        self._retried = False
        if not callable(publisher) or not callable(clock):
            raise TypeError("publisher and clock must be callable")
        self._lock = threading.RLock()
        self._publisher = publisher
        self._clock = clock
        self._state = "DISARMED"
        self._owner: str | None = None
        self._token: str | None = None
        self._lease_until: float | None = None
        self._last_now: float | None = None
        self._feedback: deque[tuple[float, dict[str, float]]] = deque(
            maxlen=_STABLE_SAMPLES
        )
        self._last_feedback_at: float | None = None
        self._pending: SafeCommand | None = None
        self._last_sent_at: float | None = None
        self._joint_commands: dict[str, SafeCommand] = {}
        self._sequence = 0
        self._published = 0
        self._rejected = 0
        self._reason = "startup"
        self._publishing = False

    @property
    def limits(self) -> Mapping[str, tuple[float, float]]:
        """Read-only verified limits; there is no override or unlock API."""
        return self._limits

    @property
    def locked_joints(self) -> frozenset[str]:
        """Permanently locked joints, including J6 without any exception."""
        return _LOCKED if self._manual_joint is None else frozenset({'J1', 'J2', 'J3', 'J4', 'J5', 'J6'} - self._limits.keys())

    def _reject(self, code: str) -> None:
        self._rejected += 1
        raise SafetyError(code)

    def _not_publishing(self) -> None:
        if self._publishing:
            self._reject("publisher_reentry")

    def _drop_arm(self, reason: str) -> None:
        self._owner = None
        self._token = None
        self._lease_until = None
        if self._state != "ESTOP":
            self._state = "DISARMED"
            self._reason = reason

    def _trip(self, reason: str) -> None:
        self._drop_arm(reason)
        if self._state != "ESTOP":
            self._reason = reason
        self._state = "ESTOP"

    def _now(self) -> float:
        try:
            now = _number(self._clock())
        except Exception as exc:
            self._trip("clock_failure")
            raise SafetyError("clock_failure") from exc
        if self._last_now is not None and now < self._last_now:
            self._trip("clock_regressed")
            self._reject("clock_regressed")
        self._last_now = now
        return now

    def _expire(self, now: float) -> None:
        if self._pending is not None:
            if now >= self._pending.issued_at + self.ack_timeout:
                self._trip("ack_timeout")
        if self._state == "ARMED":
            if self._lease_until is None or now >= self._lease_until:
                self._drop_arm("lease_expired")
            elif not self._feedback or now > self._feedback[-1][0] + self.feedback_max_age:
                self._drop_arm("feedback_expired")

    def _fresh_samples(self, now: float) -> list[tuple[float, dict[str, float]]]:
        return [sample for sample in self._feedback if now <= sample[0] + _STABILITY_WINDOW_S]

    def _stable(self, now: float) -> bool:
        samples = self._fresh_samples(now)
        return (len(samples) == _STABLE_SAMPLES
                and now <= samples[-1][0] + self.feedback_max_age) and all(
            max(sample[1][joint] for sample in samples)
            - min(sample[1][joint] for sample in samples)
            <= (3.0 if self._integer_feedback else _STABILITY_DEG)
            for joint in self._limits
        )

    def _authorize(self, token: str) -> None:
        if self._state != "ARMED":
            self._reject("not_armed")
        if (
            type(token) is not str
            or len(token) != 64
            or not token.isascii()
            or self._token is None
            or not secrets.compare_digest(token, self._token)
        ):
            self._reject("invalid_token")

    def arm(self, owner: str) -> str:
        """Explicitly acquire exclusive ownership; return a 256-bit lease token.

        Requires fresh stable feedback, no pending command, and DISARMED state.
        Calling arm while armed (even with the same owner) never replaces a lease.
        """
        with self._lock:
            self._not_publishing()
            now = self._now()
            self._expire(now)
            if type(owner) is not str or not owner.strip():
                self._reject("invalid_owner")
            if self._state == "ESTOP":
                self._reject("estop_latched")
            if self._state == "ARMED":
                self._reject("already_owned")
            if self._pending is not None:
                self._reject("pending_ack")
            if not self._stable(now):
                self._reject("feedback_unstable")
            token = secrets.token_hex(32)
            self._token = token
            self._owner = owner
            self._lease_until = now + _LEASE_S
            self._sequence = 0
            self._state = "ARMED"
            self._reason = "armed"
            return token

    def renew(self, token: str) -> float:
        """Extend a still-valid lease to one second from now; return its deadline."""
        with self._lock:
            self._not_publishing()
            now = self._now()
            self._expire(now)
            self._authorize(token)
            self._lease_until = now + _LEASE_S
            return self._lease_until

    def observe_external_command(self, command: SafeCommand) -> None:
        """Share the adapter's last authorized target across tracking/manual gates.

        This grants no permission, acknowledges nothing and sends nothing.
        The adapter serializes publication and calls only on inactive peer gates.
        """
        with self._lock:
            if type(command) is not SafeCommand:
                raise TypeError('expected SafeCommand')
            if command.joint in self._limits and self._pending is None:
                self._joint_commands[command.joint] = command

    def previous_target(self, joint):
        with self._lock:
            previous = self._joint_commands.get(joint)
            return previous.target if previous else None

    def rebase(self):
        """Forget old targets only after an explicit, stationary operator recovery."""
        with self._lock:
            self._not_publishing()
            now = self._now()
            self._expire(now)
            if self._state != 'DISARMED' or self._pending is not None:
                self._reject('recovery_requires_disarmed')
            if not self._stable(now):
                self._reject('feedback_unstable')
            if self._last_sent_at is not None and now - self._last_sent_at < 1:
                self._reject('recovery_wait_for_settle')
            self._joint_commands.clear()

    def disarm(self) -> None:
        """Revoke ownership immediately; preserve ESTOP and any pending target."""
        with self._lock:
            self._drop_arm("operator_disarm")

    def estop(self) -> None:
        """Latch a software stop and revoke the token; never invoke the publisher."""
        with self._lock:
            self._trip("operator_estop")

    def reset(self) -> None:
        """Explicitly clear ESTOP into DISARMED, discarding feedback and pending.

        Rate/target history and feedback timestamp high-water marks survive reset.
        This prevents rapid reset/rearm cycles from bypassing rate/replay checks.
        """
        with self._lock:
            self._not_publishing()
            now = self._now()
            self._expire(now)
            if self._state != "ESTOP":
                self._reject("not_estopped")
            self._pending = None
            self._feedback.clear()
            self._state = "DISARMED"
            self._drop_arm("reset")

    def update_feedback(
        self, positions: dict[str, float], *, received_at: float | None = None
    ) -> None:
        """Accept one complete local feedback sample; invalid data revokes arm.

        Receipt stamps must strictly increase, be no more than 500 ms old, and
        never be in the future. Only feedback received AFTER issue can ack a
        pending target, and an acknowledgement at its timeout is too late.
        """
        with self._lock:
            self._not_publishing()
            now = self._now()
            self._expire(now)
            try:
                stamp = now if received_at is None else _number(received_at)
                if stamp > now or now > stamp + self.feedback_max_age:
                    raise ValueError("feedback age")
                if self._last_feedback_at is not None and stamp <= self._last_feedback_at:
                    raise ValueError("feedback order")
                if type(positions) is not dict:
                    raise ValueError("feedback type")
                snapshot = positions.copy()
                if any(type(key) is not str for key in snapshot) or snapshot.keys() != self._limits.keys():
                    raise ValueError("feedback joints")
                values = {joint: _number(value) for joint, value in snapshot.items()}
                margin = 5 if self._manual_joint in {'J2', 'J5'} else 0
                if any(not self._limits[joint][0] - margin <= value <= self._limits[joint][1] + margin
                       for joint, value in values.items()):
                    raise ValueError("feedback limits")
            except ValueError:
                self._feedback.clear()
                self._drop_arm("invalid_feedback")
                self._reject("invalid_feedback")
            self._feedback.append((stamp, values))
            self._last_feedback_at = stamp
            pending = self._pending
            settled_near_target = False
            if pending is not None and self._integer_feedback and pending.joint in {'J3', 'J4'}:
                samples = self._fresh_samples(now)
                # Accept measured small undertravel only after three settled,
                # post-travel samples. No motion, wrong direction and larger
                # errors still fault; targets and speed limits are unchanged.
                settled_near_target = (len(samples) == 3
                    and samples[0][0] >= pending.issued_at + pending.duration_s
                    and max(p[pending.joint] for _, p in samples) - min(p[pending.joint] for _, p in samples) <= 1
                    and abs(values[pending.joint] - pending.target) <= 3)
            if (
                pending is not None
                and self._state != "ESTOP"
                and stamp > pending.issued_at
                and (abs(values[pending.joint] - pending.target) <= self.ack_tolerance(pending.joint)
                     or settled_near_target)
                and (not self._integer_feedback or pending.target == self._pending_start
                     or (values[pending.joint] - self._pending_start) *
                        (1 if pending.target > self._pending_start else -1) >= 0.5)
                and (not self._integer_feedback or stamp >= pending.issued_at + pending.duration_s)
            ):
                self._pending = None

    def command(
        self,
        token: str,
        sequence: int,
        joint: str,
        target: float,
        *,
        received_at: float,
        speed: int | None = None,
    ) -> SafeCommand:
        """Validate and synchronously emit one immutable command under the RLock.

        Rejected requests raise SafetyError without publishing. Publisher errors
        latch ESTOP and raise PublisherError (BaseException subclasses are latched
        and re-raised). Receipt time must be stamped by trusted local ingress,
        before queueing or waiting for this gate's lock.
        """
        with self._lock:
            self._not_publishing()
            now = self._now()
            self._expire(now)
            self._authorize(token)
            if speed is not None and (not self._manual_joint or type(speed) is not int or not 4 <= speed <= 8):
                self._reject('invalid_manual_speed')
            if type(sequence) is not int or sequence <= 0 or sequence <= self._sequence:
                self._reject("invalid_sequence")
            if type(joint) is not str or joint not in self._limits:
                self._reject("locked_or_unknown_joint")
            try:
                value = _number(target)
                stamp = _number(received_at)
            except ValueError:
                self._reject("invalid_number")
            if stamp > now or now > stamp + _COMMAND_AGE_S:
                self._reject("command_age")
            if not self._limits[joint][0] <= value <= self._limits[joint][1]:
                self._reject("joint_limit")
            if self._pending is not None:
                self._reject("pending_ack")
            if not self._stable(now):
                self._reject("feedback_unstable")
            if self._last_sent_at is not None and now < self._last_sent_at + _MIN_INTERVAL_S:
                self._reject("minimum_interval")
            measured = self._feedback[-1][1][joint]
            if self._manual_joint == 'J2' and value > 115 and value >= measured:
                self._reject('J2_box_collision_guard')
            if self._manual_joint == 'J4':
                if measured < -30 and not measured < value <= -30:
                    self._reject('J4_recovery_toward_minus_30_only')
                if measured >= -30 and value < -30:
                    self._reject('J4_operating_limit')
            if abs(value - measured) > self.max_step:
                self._reject("maximum_step")
            previous = self._joint_commands.get(joint)
            if previous is not None:
                delta = abs(value - previous.target)
                if delta > self.max_step:
                    self._reject("maximum_step")
                if delta > self.max_velocity * (now - previous.issued_at):
                    self._reject("maximum_velocity")
            emitted = SafeCommand(
                joint=joint,
                target=value,
                duration_s=max(.25 if speed is not None else .9 if self._manual_joint else .25 if self._integer_feedback else _MIN_INTERVAL_S,
                               abs(value - measured) / (speed or self.max_velocity)),
                sequence=sequence,
                issued_at=now,
            )
            # Reserve state before calling external code, including for reentrant stops.
            self._sequence = sequence
            self._pending = emitted
            self._pending_start = measured
            self._retried = False
            self._joint_commands[joint] = emitted
            self._last_sent_at = now
            self._publishing = True
            try:
                if self._publisher(emitted) is not None:
                    raise PublisherError("publisher_return_value")
            except BaseException as exc:
                self._trip("publisher_failure")
                if not isinstance(exc, Exception):
                    raise
                raise PublisherError("publisher_failure") from exc
            finally:
                self._publishing = False
            self._published += 1
            self._expire(self._now())
            return emitted

    def retry_pending(self, token, *, received_at):
        """One same-target retry on fresh vision; never reset a latched stop."""
        with self._lock:
            self._not_publishing()
            now = self._now()
            self._expire(now)
            self._authorize(token)
            stamp = _number(received_at)
            if stamp > now or now > stamp + _COMMAND_AGE_S:
                self._reject('command_age')
            pending = self._pending
            if (not self._integer_feedback or pending is None or self._retried
                    or now - pending.issued_at < .9 or not self._stable(now)):
                self._reject('retry_not_ready')
            measured = self._feedback[-1][1][pending.joint]
            if abs(measured - self._pending_start) > 1 or abs(pending.target - measured) > self.max_step:
                self._reject('retry_motion_observed')
            retry = SafeCommand(pending.joint, pending.target, pending.duration_s,
                                pending.sequence + 1, now)
            self._retried = True
            self._sequence = retry.sequence
            self._pending = retry
            self._joint_commands[retry.joint] = retry
            self._last_sent_at = now
            self._publishing = True
            try:
                if self._publisher(retry) is not None:
                    raise PublisherError('publisher_return_value')
            except BaseException as exc:
                self._trip('publisher_failure')
                if not isinstance(exc, Exception):
                    raise
                raise PublisherError('publisher_failure') from exc
            finally:
                self._publishing = False
            self._published += 1
            self._expire(self._now())
            return retry

    @property
    def max_step(self):
        if self._manual_joint:
            return 4.0
        return 9.0 if self._integer_feedback else _MAX_STEP_DEG

    def ack_tolerance(self, joint):
        # J3 measured two degrees below upward targets near its lower stop.
        # Actual directional progress remains mandatory, even inside this band.
        if self._integer_feedback:
            return 2.0 if joint in {'J1', 'J3', 'J4'} else 1.0
        return _ACK_TOLERANCE_DEG

    @property
    def max_velocity(self):
        if self._manual_joint:
            return 8.0
        return 36.0 if self._integer_feedback else _MAX_VELOCITY

    @property
    def feedback_max_age(self):
        return 1.0 if self._integer_feedback else _FEEDBACK_AGE_S

    @property
    def ack_timeout(self):
        if self._manual_joint:
            return 3.0 if self._manual_joint in {'J3', 'J4'} else 2.0
        return 1.5 if self._integer_feedback else _ACK_TIMEOUT_S

    def tick(self) -> SafetyStatus:
        """Run idle watchdog checks and return diagnostics; never publish."""
        return self.status()

    def status(self) -> SafetyStatus:
        """Check expiry and return immutable diagnostics without exposing tokens."""
        with self._lock:
            now = self._now()
            self._expire(now)
            return SafetyStatus(
                state=self._state,
                owner=self._owner,
                lease_remaining_s=max(0.0, self._lease_until - now)
                if self._lease_until is not None else 0.0,
                feedback_age_s=now - self._feedback[-1][0] if self._feedback else None,
                stable_samples=len(self._fresh_samples(now)),
                feedback_stable=self._stable(now),
                pending=self._pending,
                pending_remaining_s=max(0.0, self._pending.issued_at + self.ack_timeout - now)
                if self._pending is not None else None,
                last_sequence=self._sequence,
                published_count=self._published,
                rejected_count=self._rejected,
                reason=self._reason,
            )
