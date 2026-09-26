"""Stdlib-only safety tests. No transport, machine access, or real actuators.

Run from the repository root with:
    python3 -m unittest discover -s tests -p test_safety.py -v
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal
import math
import random
import threading
import unittest

from milo_next.safety import ActuatorSafety, PublisherError, SafetyError


class FakeClock:
    def __init__(self):
        self.value = 10.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class SneakyFloat(float):
    def __float__(self):
        raise AssertionError("numeric coercion must never run")


class SneakyInt(int):
    pass


class SneakyString(str):
    pass


class SneakyDict(dict):
    def copy(self):
        raise AssertionError("mapping callbacks must never run")


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sent = []
        self.gate = ActuatorSafety(self.sent.append, clock=self.clock)

    def feedback(self, j1=120.0, j3=100.0, *, stamp=None):
        self.gate.update_feedback({"J1": j1, "J3": j3}, received_at=stamp)

    def stable(self, j1=120.0, j3=100.0):
        for _ in range(3):
            self.clock.advance(0.01)
            self.feedback(j1, j3)

    def arm(self):
        self.stable()
        return self.gate.arm("test-owner")

    def command(self, token, sequence=1, joint="J1", target=120.0, *, stamp=None):
        return self.gate.command(
            token, sequence, joint, target,
            received_at=self.clock() if stamp is None else stamp,
        )

    def rejected(self, code, operation, *args, **kwargs):
        before = len(self.sent)
        with self.assertRaises(SafetyError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(len(self.sent), before)

    def test_startup_and_state_changes_never_publish(self):
        self.assertEqual(self.gate.status().state, "DISARMED")
        self.rejected("not_armed", self.command, "x" * 64)
        self.rejected("feedback_unstable", self.gate.arm, "owner")
        token = self.arm()
        self.gate.renew(token)
        self.gate.disarm()
        self.gate.estop()
        self.gate.reset()
        self.gate.tick()
        self.assertEqual(self.gate.status().state, "DISARMED")
        self.assertEqual(self.sent, [])

    def test_lease_has_256_bits_and_is_not_disclosed_in_status(self):
        token = self.arm()
        self.assertEqual(len(token), 64)
        self.assertEqual(len(bytes.fromhex(token)), 32)
        self.assertNotIn(token, repr(self.gate.status()))
        self.assertEqual(self.gate.status().owner, "test-owner")
        self.assertEqual(self.gate.status().lease_remaining_s, 1.0)
        self.gate.disarm()
        replacement = self.gate.arm("new-owner")
        self.assertNotEqual(replacement, token)
        self.rejected("invalid_token", self.command, token)
        self.rejected("invalid_token", self.gate.renew, token)

    def test_single_owner_including_same_name_cannot_replace_lease(self):
        token = self.arm()
        for owner in ("other", "test-owner"):
            self.rejected("already_owned", self.gate.arm, owner)
        self.command(token)

    def test_owner_and_token_type_tricks_are_rejected(self):
        self.stable()
        for owner in (None, True, 1, "", "  ", [], SneakyString("owner")):
            with self.subTest(owner=repr(owner)):
                self.rejected("invalid_owner", self.gate.arm, owner)
        token = self.gate.arm("owner")
        for bad in (None, True, 1, [], token.encode(), SneakyString(token), "x" * 64, "\u00e9" * 64):
            with self.subTest(token=repr(bad)):
                self.rejected("invalid_token", self.command, bad)
                self.rejected("invalid_token", self.gate.renew, bad)
        self.command(token)

    def test_renewal_moves_deadline_from_now_not_old_deadline(self):
        token = self.arm()
        self.clock.advance(0.4)
        self.feedback()
        expiry = self.gate.renew(token)
        self.assertEqual(expiry, self.clock() + 1.0)
        for _ in range(2):
            self.clock.advance(0.4)
            self.feedback()
        self.assertEqual(self.gate.status().state, "ARMED")
        self.clock.value = expiry
        self.feedback()
        self.assertEqual(self.gate.status().state, "DISARMED")
        self.assertEqual(self.gate.status().reason, "lease_expired")
        self.rejected("not_armed", self.gate.renew, token)

    def test_expired_lease_cannot_be_revived_by_command_renew_or_arm(self):
        token = self.arm()
        expiry = self.clock() + 1.0
        self.clock.advance(0.4)
        self.feedback()
        self.clock.advance(0.4)
        self.feedback()
        self.clock.value = expiry
        self.rejected("not_armed", self.command, token)
        self.rejected("not_armed", self.gate.renew, token)
        self.stable()
        replacement = self.gate.arm("next")
        self.assertNotEqual(token, replacement)

    def test_feedback_expiry_revokes_lease_and_new_feedback_cannot_restore_it(self):
        token = self.arm()
        self.clock.value = math.nextafter(self.clock() + 0.5, math.inf)
        status = self.gate.tick()
        self.assertEqual((status.state, status.reason), ("DISARMED", "feedback_expired"))
        self.assertIsNone(status.owner)
        self.assertEqual(status.lease_remaining_s, 0)
        self.stable()
        self.rejected("not_armed", self.command, token)
        self.rejected("not_armed", self.gate.renew, token)

    def test_feedback_expiry_checked_before_accepting_recovery_sample(self):
        token = self.arm()
        self.clock.advance(0.501)
        self.feedback()
        self.rejected("not_armed", self.gate.renew, token)

    def test_estop_is_latched_and_reset_requires_new_feedback_and_arm(self):
        token = self.arm()
        self.gate.estop()
        self.gate.disarm()
        self.rejected("estop_latched", self.gate.arm, "owner")
        self.rejected("not_armed", self.gate.renew, token)
        self.rejected("not_armed", self.command, token)
        self.stable()
        self.assertEqual(self.gate.status().state, "ESTOP")
        self.gate.reset()
        self.assertEqual(self.gate.status().state, "DISARMED")
        self.rejected("feedback_unstable", self.gate.arm, "owner")
        self.stable()
        new_token = self.gate.arm("owner")
        self.rejected("invalid_token", self.command, token)
        self.command(new_token)

    def test_reset_cannot_be_used_as_ordinary_arm_or_disarm(self):
        self.rejected("not_estopped", self.gate.reset)
        self.arm()
        self.rejected("not_estopped", self.gate.reset)
        self.assertEqual(self.gate.status().state, "ARMED")

    def test_locked_joints_and_limits_are_immutable(self):
        self.assertEqual(self.gate.locked_joints, frozenset({"J2", "J4", "J5", "J6"}))
        with self.assertRaises(TypeError):
            self.gate.limits["J6"] = (0, 360)
        with self.assertRaises(AttributeError):
            self.gate.locked_joints = frozenset()
        with self.assertRaises(AttributeError):
            self.gate.limits = {"J6": (0, 360)}
        token = self.arm()
        for joint in ("J2", "J4", "J5", "J6", "j1", "J01", "1", 1, True, None, [], SneakyString("J1")):
            with self.subTest(joint=repr(joint)):
                self.rejected("locked_or_unknown_joint", self.command, token, joint=joint)

    def test_joint_limit_endpoints_are_inclusive(self):
        for joint, bounds in self.gate.limits.items():
            for endpoint in bounds:
                with self.subTest(joint=joint, endpoint=endpoint):
                    self.setUp()
                    self.stable(endpoint if joint == "J1" else 120,
                                endpoint if joint == "J3" else 100)
                    token = self.gate.arm("limits")
                    emitted = self.command(token, joint=joint, target=endpoint)
                    self.assertEqual((emitted.joint, emitted.target), (joint, endpoint))

    def test_outside_limits_even_one_ulp_is_rejected(self):
        token = self.arm()
        for joint, (low, high) in self.gate.limits.items():
            for target in (math.nextafter(low, -math.inf), math.nextafter(high, math.inf), -1e100, 1e100):
                with self.subTest(joint=joint, target=target):
                    self.rejected("joint_limit", self.command, token, joint=joint, target=target)

    def test_numeric_edge_cases_never_reach_publisher(self):
        token = self.arm()
        cases = (math.nan, math.inf, -math.inf, True, False, "120", None,
                 10 ** 1000, Decimal("120"), SneakyFloat(120), SneakyInt(120), [], object())
        for bad in cases:
            for field in ("target", "stamp"):
                if bad is None and field == "stamp":
                    # The helper's None means 'now'; use the API directly here.
                    self.rejected("invalid_number", self.gate.command, token, 1, "J1", 120, received_at=None)
                    continue
                with self.subTest(field=field, value=repr(bad)):
                    self.rejected("invalid_number", self.command, token, **{field: bad})
        self.command(token, target=120)
        self.assertIs(type(self.sent[0].target), float)

    def test_positive_strictly_increasing_sequences(self):
        token = self.arm()
        for sequence in (0, -1, True, False, 1.0, "1", None, math.nan, SneakyInt(1), []):
            with self.subTest(sequence=repr(sequence)):
                self.rejected("invalid_sequence", self.command, token, sequence=sequence)
        first = self.command(token, sequence=5)
        self.clock.value = first.issued_at + 0.12
        self.feedback()
        for sequence in (1, 4, 5):
            self.rejected("invalid_sequence", self.command, token, sequence=sequence)
        self.command(token, sequence=6)

    def test_failed_validation_does_not_consume_sequence(self):
        token = self.arm()
        self.rejected("maximum_step", self.command, token, sequence=50, target=122)
        self.command(token, sequence=1)
        self.assertEqual(self.gate.status().last_sequence, 1)

    def test_command_age_rejects_stale_and_future_including_one_ulp(self):
        token = self.arm()
        self.rejected("command_age", self.command, token, stamp=self.clock() - 0.151)
        self.rejected("command_age", self.command, token, stamp=math.nextafter(self.clock(), math.inf))
        self.rejected("command_age", self.command, token, stamp=self.clock() + 10)
        self.command(token, stamp=self.clock() - 0.150)

    def test_feedback_stability_requires_three_distinct_recent_samples(self):
        for _ in range(2):
            self.clock.advance(0.01)
            self.feedback()
            self.rejected("feedback_unstable", self.gate.arm, "owner")
        self.clock.advance(0.01)
        self.feedback()
        self.assertEqual(self.gate.status().stable_samples, 3)
        self.assertTrue(self.gate.status().feedback_stable)
        self.gate.arm("owner")

    def test_feedback_must_be_stable_on_both_axes(self):
        for joint in ("J1", "J3"):
            with self.subTest(joint=joint):
                self.setUp()
                for offset in (0, 0.4, 0):
                    self.clock.advance(0.01)
                    self.feedback(120 + (offset if joint == "J1" else 0),
                                  100 + (offset if joint == "J3" else 0))
                self.rejected("feedback_unstable", self.gate.arm, "owner")
                self.stable()
                self.gate.arm("owner")

    def test_old_samples_do_not_count_towards_stability(self):
        self.stable()
        self.clock.advance(1.195)
        self.feedback()
        self.assertEqual(self.gate.status().stable_samples, 2)
        self.rejected("feedback_unstable", self.gate.arm, "owner")

    def test_feedback_age_limit_is_inclusive(self):
        for _ in range(3):
            self.clock.advance(0.01)
            self.feedback(stamp=self.clock() - 0.5)
        self.assertEqual(self.gate.status().stable_samples, 3)
        self.assertTrue(self.gate.status().feedback_stable)
        self.clock.advance(0.001)
        self.rejected("feedback_unstable", self.gate.arm, "owner")

    def test_invalid_feedback_removes_arm_and_stability(self):
        samples = (
            {}, {"J1": 120}, {"J1": 120, "J3": 100, "J6": 0},
            {"J1": -1, "J3": 100}, {"J1": 120, "J3": 136},
            {"J1": math.nan, "J3": 100}, {"J1": 120, "J3": math.inf},
            {"J1": True, "J3": 100}, {"J1": 120, "J3": "100"},
            {"J1": 10 ** 1000, "J3": 100}, {SneakyString("J1"): 120, "J3": 100},
            {"J1": SneakyFloat(120), "J3": 100}, SneakyDict(J1=120, J3=100),
            [("J1", 120), ("J3", 100)], None,
        )
        for sample in samples:
            with self.subTest(sample=repr(sample)):
                self.setUp()
                self.arm()
                self.clock.advance(0.01)
                self.rejected("invalid_feedback", self.gate.update_feedback, sample)
                status = self.gate.status()
                self.assertEqual(status.state, "DISARMED")
                self.assertEqual(status.stable_samples, 0)

    def test_stale_future_replayed_and_invalid_feedback_stamps(self):
        for mode in ("stale", "future", "replay", "out_of_order", "nan", "bool", "subclass"):
            with self.subTest(mode=mode):
                self.setUp()
                self.arm()
                last = self.clock()
                self.clock.advance(0.01)
                stamp = {"stale": self.clock() - 0.501,
                         "future": math.nextafter(self.clock(), math.inf),
                         "replay": last, "out_of_order": last - 0.005,
                         "nan": math.nan, "bool": True,
                         "subclass": SneakyFloat(self.clock())}[mode]
                self.rejected("invalid_feedback", self.feedback, stamp=stamp)
                self.assertEqual(self.gate.status().state, "DISARMED")

    def test_feedback_snapshot_is_defensively_copied(self):
        sample = {"J1": 120, "J3": 100}
        for _ in range(3):
            self.clock.advance(0.01)
            self.gate.update_feedback(sample)
        sample["J1"] = 179
        token = self.gate.arm("owner")
        self.command(token, target=121)

    def test_initial_step_and_minimum_duration_bound_speed(self):
        token = self.arm()
        self.rejected("maximum_step", self.command, token, target=math.nextafter(121.0, math.inf))
        emitted = self.command(token, target=121)
        self.assertEqual(emitted.duration_s, 0.125)
        self.assertLessEqual(1.0 / emitted.duration_s, 8.0)
        with self.assertRaises(FrozenInstanceError):
            emitted.joint = "J6"

    def test_minimum_interval_applies_across_joints(self):
        token = self.arm()
        emitted = self.command(token)
        self.clock.advance(0.01)
        self.feedback()
        self.clock.value = math.nextafter(emitted.issued_at + 0.12, -math.inf)
        self.rejected("minimum_interval", self.command, token, sequence=2, joint="J3", target=100)
        self.clock.value = emitted.issued_at + 0.12
        self.command(token, sequence=2, joint="J3", target=100)
        self.assertEqual([c.joint for c in self.sent], ["J1", "J3"])

    def test_velocity_checked_against_previous_target(self):
        token = self.arm()
        previous = self.command(token)
        self.clock.advance(0.01)
        self.feedback()
        self.clock.value = previous.issued_at + 0.12
        self.rejected("maximum_velocity", self.command, token, sequence=2, target=121)
        self.command(token, sequence=2, target=120.9)

    def test_velocity_at_exact_eight_degrees_per_second(self):
        token = self.arm()
        previous = self.command(token)
        self.clock.advance(0.01)
        self.feedback()
        self.clock.value = previous.issued_at + 0.125
        self.command(token, sequence=2, target=121)

    def test_every_step_is_capped_even_after_long_idle(self):
        token = self.arm()
        self.command(token)
        self.clock.advance(0.01)
        self.feedback()
        for _ in range(6):
            self.clock.advance(0.25)
            self.feedback()
            self.gate.renew(token)
        self.rejected("maximum_step", self.command, token, sequence=2, target=122)

    def test_previous_target_cap_survives_disarm_rearm(self):
        token = self.arm()
        self.command(token)
        self.clock.advance(0.01)
        self.feedback()
        self.gate.disarm()
        self.clock.advance(0.15)
        self.stable(j1=120.5)
        token = self.gate.arm("next")
        self.rejected("maximum_step", self.command, token, target=121.5)

    def test_reset_and_rearm_do_not_bypass_minimum_interval(self):
        token = self.arm()
        self.command(token)
        self.gate.estop()
        self.gate.reset()
        self.stable()
        token = self.gate.arm("next")
        self.rejected("minimum_interval", self.command, token)

    def test_outstanding_target_blocks_all_joints(self):
        token = self.arm()
        self.command(token, target=121)
        self.clock.advance(0.15)
        self.feedback()
        self.rejected("pending_ack", self.command, token, sequence=2)
        self.rejected("pending_ack", self.command, token, sequence=2, joint="J3", target=100)
        self.assertEqual(self.gate.status().pending.target, 121)

    def test_ack_requires_new_feedback_and_target_tolerance(self):
        token = self.arm()
        self.command(token, target=121)
        self.clock.advance(0.01)
        self.feedback(j1=120.89)
        self.assertIsNotNone(self.gate.status().pending)
        self.clock.advance(0.01)
        self.feedback(j1=120.95)
        self.assertIsNone(self.gate.status().pending)
        self.rejected("feedback_unstable", self.command, token, sequence=2, target=121)
        self.stable(j1=121)
        self.clock.advance(0.12)
        self.command(token, sequence=2, target=121)

    def test_feedback_received_before_or_at_issue_cannot_ack(self):
        for offset in (-0.05, 0.0):
            with self.subTest(offset=offset):
                self.setUp()
                token = self.arm()
                self.clock.advance(0.1)
                issued = self.command(token)
                self.clock.advance(0.01)
                self.feedback(stamp=issued.issued_at + offset)
                self.assertIsNotNone(self.gate.status().pending)

    def test_ack_timeout_latches_at_deadline_despite_fresh_wrong_feedback(self):
        token = self.arm()
        issued = self.command(token, target=121)
        self.clock.value = issued.issued_at + 0.49
        self.feedback()
        self.assertEqual(self.gate.status().state, "ARMED")
        self.clock.value = issued.issued_at + 1.0
        status = self.gate.tick()
        self.assertEqual((status.state, status.reason), ("ESTOP", "ack_timeout"))
        self.rejected("not_armed", self.command, token, sequence=2)
        self.gate.disarm()
        self.assertEqual(self.gate.status().state, "ESTOP")

    def test_late_ack_cannot_clear_timeout(self):
        token = self.arm()
        issued = self.command(token, target=121)
        self.clock.value = issued.issued_at + 1.0
        self.feedback(j1=121)
        self.assertEqual(self.gate.status().state, "ESTOP")
        self.assertIsNotNone(self.gate.status().pending)

    def test_disarm_retains_pending_until_ack_or_estop(self):
        token = self.arm()
        issued = self.command(token, target=121)
        self.gate.disarm()
        self.rejected("pending_ack", self.gate.arm, "next")
        self.clock.value = issued.issued_at + 1.0
        self.assertEqual(self.gate.tick().state, "ESTOP")

    def test_pending_can_be_acknowledged_while_disarmed(self):
        token = self.arm()
        self.command(token, target=121)
        self.gate.disarm()
        self.stable(j1=121)
        self.assertIsNone(self.gate.status().pending)
        self.gate.arm("next")

    def test_pending_survives_lease_loss_and_then_times_out(self):
        token = self.arm()
        lease_end = self.clock() + 1.0
        for _ in range(3):
            self.clock.advance(0.25)
            self.feedback()
        issued = self.command(token, target=121)
        self.clock.value = lease_end
        self.assertEqual(self.gate.status().state, "DISARMED")
        self.assertIsNotNone(self.gate.status().pending)
        self.clock.value = issued.issued_at + 1.0
        self.assertEqual(self.gate.status().state, "ESTOP")

    def test_invalid_feedback_cannot_erase_pending_timeout(self):
        token = self.arm()
        issued = self.command(token, target=121)
        self.clock.advance(0.1)
        self.rejected("invalid_feedback", self.gate.update_feedback, {})
        self.clock.value = issued.issued_at + 1.0
        self.assertEqual(self.gate.tick().reason, "ack_timeout")

    def test_publisher_exceptions_latch_estop_and_keep_original_cause(self):
        problem = OSError("fake transport failure")

        def publisher(command):
            self.sent.append(command)
            raise problem

        self.gate = ActuatorSafety(publisher, clock=self.clock)
        token = self.arm()
        with self.assertRaises(PublisherError) as caught:
            self.command(token)
        self.assertIs(caught.exception.__cause__, problem)
        self.assertEqual(self.gate.status().state, "ESTOP")
        self.assertEqual(self.gate.status().reason, "publisher_failure")
        self.rejected("not_armed", self.command, token, sequence=2)
        self.assertEqual(len(self.sent), 1)

    def test_publisher_false_or_other_return_latches_estop(self):
        for result in (False, True, 0, "queued"):
            with self.subTest(result=result):
                self.setUp()
                self.gate = ActuatorSafety(lambda command: result, clock=self.clock)
                token = self.arm()
                with self.assertRaises(PublisherError):
                    self.command(token)
                self.assertEqual(self.gate.status().state, "ESTOP")

    def test_publisher_base_exception_still_latches_stop(self):
        def publisher(command):
            raise KeyboardInterrupt()

        self.gate = ActuatorSafety(publisher, clock=self.clock)
        token = self.arm()
        with self.assertRaises(KeyboardInterrupt):
            self.command(token)
        self.assertEqual(self.gate.status().state, "ESTOP")

    def test_slow_publisher_checks_timeout_before_returning(self):
        def publisher(command):
            self.sent.append(command)
            self.clock.advance(1.0)

        self.gate = ActuatorSafety(publisher, clock=self.clock)
        token = self.arm()
        self.command(token)
        self.assertEqual(self.gate.status().state, "ESTOP")

    def test_reentrant_stop_is_preserved_and_never_restores_arm(self):
        def publisher(command):
            self.sent.append(command)
            self.assertEqual(self.gate.status().state, "ARMED")
            self.gate.estop()
            self.gate.disarm()
            self.rejected("publisher_reentry", self.gate.reset)
            self.rejected("publisher_reentry", self.command, token, sequence=2)

        self.gate = ActuatorSafety(publisher, clock=self.clock)
        token = self.arm()
        self.command(token)
        self.assertEqual(self.gate.status().state, "ESTOP")
        self.assertEqual(len(self.sent), 1)

    def test_other_mutating_publisher_reentry_is_rejected(self):
        def publisher(command):
            self.sent.append(command)
            self.rejected("publisher_reentry", self.gate.arm, "other")
            self.rejected("publisher_reentry", self.gate.renew, token)
            self.rejected("publisher_reentry", self.feedback)
            self.gate.disarm()

        self.gate = ActuatorSafety(publisher, clock=self.clock)
        token = self.arm()
        self.command(token)
        self.assertEqual(self.gate.status().state, "DISARMED")

    def test_clock_regression_latches_estop(self):
        token = self.arm()
        old = self.clock()
        self.clock.value = old - 0.01
        self.rejected("clock_regressed", self.command, token)
        self.clock.value = old
        self.assertEqual(self.gate.status().state, "ESTOP")
        self.assertEqual(self.gate.status().reason, "clock_regressed")

    def test_clock_invalid_values_and_exceptions_latch_estop(self):
        for value in (math.nan, math.inf, True, "10", 10 ** 1000, SneakyFloat(10)):
            with self.subTest(value=repr(value)):
                self.setUp()
                self.arm()
                self.clock.value = value
                self.rejected("clock_failure", self.gate.tick)
                self.gate.estop()  # The explicit stop does not depend on the clock.
                self.clock.value = 11
                self.assertEqual(self.gate.status().state, "ESTOP")

        def failed_clock():
            raise RuntimeError("clock unavailable")

        gate = ActuatorSafety(self.sent.append, clock=failed_clock)
        with self.assertRaises(SafetyError) as caught:
            gate.status()
        self.assertEqual(caught.exception.code, "clock_failure")

    def test_diagnostics_are_immutable_and_include_rejections_and_pending(self):
        token = self.arm()
        self.rejected("locked_or_unknown_joint", self.command, token, joint="J6")
        command = self.command(token)
        status = self.gate.status()
        self.assertEqual(status.pending, command)
        self.assertEqual(status.published_count, 1)
        self.assertEqual(status.rejected_count, 1)
        self.assertEqual(status.pending_remaining_s, 1.0)
        self.assertEqual(status.last_sequence, 1)
        with self.assertRaises(FrozenInstanceError):
            status.state = "DISARMED"

    def test_concurrent_estop_and_commands_are_linearizable(self):
        # No sleeps: barriers release both operations together on every trial.
        for _ in range(60):
            self.setUp()
            token = self.arm()
            barrier = threading.Barrier(3)
            errors = []

            def issue():
                barrier.wait(timeout=3)
                try:
                    self.command(token)
                except SafetyError as error:
                    errors.append(error.code)

            def stop():
                barrier.wait(timeout=3)
                self.gate.estop()

            threads = [threading.Thread(target=issue), threading.Thread(target=stop)]
            for thread in threads:
                thread.start()
            barrier.wait(timeout=3)
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
            self.assertEqual(self.gate.status().state, "ESTOP")
            self.assertIn(len(self.sent), (0, 1))
            self.assertEqual(errors, ["not_armed"] if not self.sent else [])
            self.rejected("not_armed", self.command, token, sequence=2)

    def test_estop_waits_for_inflight_publisher_and_fences_later_commands(self):
        entered = threading.Event()
        release = threading.Event()
        stop_started = threading.Event()
        stopped = threading.Event()
        errors = []

        def publisher(command):
            self.sent.append(command)
            entered.set()
            if not release.wait(timeout=3):
                raise RuntimeError("test did not release publisher")

        self.gate = ActuatorSafety(publisher, clock=self.clock)
        token = self.arm()

        def issue():
            try:
                self.command(token)
            except BaseException as error:
                errors.append(error)

        def stop():
            stop_started.set()
            self.gate.estop()
            stopped.set()

        command_thread = threading.Thread(target=issue)
        stop_thread = threading.Thread(target=stop)
        command_thread.start()
        try:
            self.assertTrue(entered.wait(timeout=3))
            stop_thread.start()
            self.assertTrue(stop_started.wait(timeout=3))
            self.assertFalse(stopped.is_set())
            acquired = self.gate._lock.acquire(blocking=False)
            if acquired:
                self.gate._lock.release()
            self.assertFalse(acquired, "publisher must run under the validation lock")
        finally:
            release.set()
            command_thread.join(timeout=3)
            if stop_thread.ident is not None:
                stop_thread.join(timeout=3)
        self.assertFalse(command_thread.is_alive())
        self.assertFalse(stop_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(stopped.is_set())
        self.rejected("not_armed", self.command, token, sequence=2)
        self.assertEqual(len(self.sent), 1)

    def test_estop_wins_before_queued_command_can_validate(self):
        token = self.arm()
        started = threading.Event()
        errors = []

        def issue():
            started.set()
            try:
                self.command(token)
            except SafetyError as error:
                errors.append(error.code)

        with self.gate._lock:
            thread = threading.Thread(target=issue)
            thread.start()
            self.assertTrue(started.wait(timeout=3))
            self.gate.estop()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["not_armed"])
        self.assertEqual(self.sent, [])

    def test_queue_wait_is_included_in_local_command_age(self):
        token = self.arm()
        receipt = self.clock()
        started = threading.Event()
        errors = []

        def issue():
            started.set()
            try:
                self.command(token, stamp=receipt)
            except SafetyError as error:
                errors.append(error.code)

        with self.gate._lock:
            thread = threading.Thread(target=issue)
            thread.start()
            self.assertTrue(started.wait(timeout=3))
            self.clock.advance(0.151)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["command_age"])
        self.assertEqual(self.sent, [])

    def test_concurrent_replay_can_publish_only_once(self):
        token = self.arm()
        barrier = threading.Barrier(9)
        errors = []

        def issue():
            barrier.wait(timeout=3)
            try:
                self.command(token)
            except SafetyError as error:
                errors.append(error.code)

        threads = [threading.Thread(target=issue) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(errors, ["invalid_sequence"] * 7)

    def test_concurrent_ownership_has_one_winner(self):
        self.stable()
        barrier = threading.Barrier(9)
        tokens = []
        errors = []

        def acquire():
            barrier.wait(timeout=3)
            try:
                tokens.append(self.gate.arm(threading.current_thread().name))
            except SafetyError as error:
                errors.append(error.code)

        threads = [threading.Thread(target=acquire) for _ in range(8)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(tokens), 1)
        self.assertEqual(errors, ["already_owned"] * 7)

    def test_seeded_adversarial_requests_never_publish_unsafe_targets(self):
        token = self.arm()
        rng = random.Random(717)
        for sequence in range(1, 1001):
            joint = rng.choice(["J1", "J3", "J2", "J4", "J5", "J6", 1, True])
            target = rng.choice([rng.uniform(-360, 360), math.nan, math.inf, True, "120"])
            try:
                self.command(token, sequence=sequence, joint=joint, target=target)
            except SafetyError:
                pass
        for command in self.sent:
            self.assertIn(command.joint, ("J1", "J3"))
            low, high = self.gate.limits[command.joint]
            self.assertTrue(low <= command.target <= high)
            self.assertTrue(math.isfinite(command.target))
            measured = 120 if command.joint == "J1" else 100
            self.assertLessEqual(abs(command.target - measured), 1)
            self.assertLessEqual(abs(command.target - measured) / command.duration_s, 8)


if __name__ == "__main__":
    unittest.main()
