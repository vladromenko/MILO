import pytest

from milo_next.safety import ActuatorSafety, SafetyError


@pytest.mark.parametrize('joint', ['J3', 'J4'])
@pytest.mark.parametrize('direction', [-1, 1])
def test_loaded_joint_accepts_settled_three_degree_error_before_deadline(joint, direction):
    now, sent = [10.0], []
    gate = ActuatorSafety(sent.append, clock=lambda: now[0], integer_feedback=True, manual_joint=joint)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({joint: 40})
    token = gate.arm('operator-test')
    command = gate.command(token, 1, joint, 40 + 4 * direction, received_at=now[0])
    gate.disarm()
    for offset in (2.1, 2.48):
        now[0] = command.issued_at + offset
        gate.update_feedback({joint: 40 + direction})
        assert gate.status().pending is not None
    now[0] = command.issued_at + 2.86
    gate.update_feedback({joint: 40 + direction})
    assert gate.status().pending is None
    assert gate.status().state == 'DISARMED'
    assert len(sent) == 1


@pytest.mark.parametrize('joint', ['J3', 'J4'])
@pytest.mark.parametrize('target,readings', [
    (42, (40, 40, 40)),  # Within tolerance, but no actual motion.
    (42, (39, 39, 39)),  # Wrong direction, despite a three-degree error.
    (44, (48, 48, 48)),  # Outside the settled tolerance.
    (44, (37, 39, 41)),  # Not yet settled; last sample alone is insufficient.
])
def test_loaded_joint_still_faults_on_unconfirmed_motion(joint, target, readings):
    now, sent = [10.0], []
    gate = ActuatorSafety(sent.append, clock=lambda: now[0], integer_feedback=True, manual_joint=joint)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({joint: 40})
    token = gate.arm('operator-test')
    command = gate.command(token, 1, joint, target, received_at=now[0])
    gate.disarm()
    for offset, reading in zip((1.0, 1.38, 1.76), readings):
        now[0] = command.issued_at + offset
        gate.update_feedback({joint: reading})
    assert gate.status().pending is not None
    now[0] = command.issued_at + 3
    assert gate.status().reason == 'ack_timeout'
    assert gate.status().state == 'ESTOP'
    now[0] += .01
    gate.update_feedback({joint: target})
    assert gate.status().state == 'ESTOP'
    assert len(sent) == 1


def test_j4_settled_ack_requires_three_post_travel_samples():
    now = [10.0]
    gate = ActuatorSafety(lambda _: None, clock=lambda: now[0], integer_feedback=True, manual_joint='J4')
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J4': 40})
    token = gate.arm('operator-test')
    command = gate.command(token, 1, 'J4', 44, received_at=now[0])
    gate.disarm()
    for offset in (.4, .8, 1.2, 1.6):
        now[0] = command.issued_at + offset
        gate.update_feedback({'J4': 41})
        assert gate.status().pending is not None
    now[0] = command.issued_at + 2.0
    gate.update_feedback({'J4': 41})
    assert gate.status().pending is None


@pytest.mark.parametrize('reading,acknowledged', [(1, False), (2, False), (3, True), (5, True), (7, True), (8, False)])
def test_j4_manual_ack_requires_real_progress(reading, acknowledged):
    now = [10.0]
    gate = ActuatorSafety(lambda _: None, clock=lambda: now[0], integer_feedback=True, manual_joint='J4')
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J4': 1})
    token = gate.arm('operator-test')
    gate.command(token, 1, 'J4', 5, received_at=now[0])
    now[0] += .95
    gate.update_feedback({'J4': reading})
    assert (gate.status().pending is None) == acknowledged


@pytest.mark.parametrize('reading,acknowledged', [(120, False), (119, False), (121, True), (122, True), (123, True), (124, True), (125, True), (126, False)])
def test_integer_ack_requires_progress_and_target_proximity(reading, acknowledged):
    now = [10.0]
    sent = []
    gate = ActuatorSafety(sent.append, clock=lambda: now[0], integer_feedback=True)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J1': 120, 'J3': 100})
    token = gate.arm('test')
    command = gate.command(token, 1, 'J1', 123, received_at=now[0])
    assert command.duration_s == .25
    now[0] += .4
    gate.update_feedback({'J1': reading, 'J3': 100})
    assert (gate.status().pending is None) == acknowledged
    if not acknowledged:
        now[0] = command.issued_at + 1.5
        assert gate.status().state == 'ESTOP'


def test_integer_profile_preserves_limits_locked_joints_and_step_cap():
    now = [10.0]
    sent = []
    gate = ActuatorSafety(sent.append, clock=lambda: now[0], integer_feedback=True)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J1': 120, 'J3': 100})
    token = gate.arm('test')
    for joint, target, error in [('J6', 40, 'locked_or_unknown_joint'), ('J1', 181, 'joint_limit'), ('J1', 130, 'maximum_step')]:
        with pytest.raises(SafetyError, match=error):
            gate.command(token, 1, joint, target, received_at=now[0])
    assert sent == []


def test_rc13_profile_accepts_verified_step_speed_and_feedback_age():
    now = [10.0]
    sent = []
    gate = ActuatorSafety(sent.append, clock=lambda: now[0], integer_feedback=True)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J1': 120, 'J3': 100})
    token = gate.arm('test')
    command = gate.command(token, 1, 'J1', 129, received_at=now[0])
    assert command.duration_s == .25
    now[0] += .4
    gate.update_feedback({'J1': 129, 'J3': 100})
    gate.renew(token)
    now[0] += .7
    assert gate.status().state == 'ARMED'
    gate.renew(token)
    now[0] += .31
    assert gate.status().reason == 'feedback_expired'
    assert gate.status().state == 'DISARMED'


def test_retry_is_same_target_once_and_missing_motion_still_stops():
    now = [10.0]
    sent = []
    gate = ActuatorSafety(sent.append, clock=lambda: now[0], integer_feedback=True)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J1': 120, 'J3': 100})
    token = gate.arm('test')
    first = gate.command(token, 1, 'J1', 126, received_at=now[0])
    for _ in range(3):
        now[0] += .31
        gate.update_feedback({'J1': 120, 'J3': 100})
    gate.renew(token)
    retry = gate.retry_pending(token, received_at=now[0])
    assert (retry.joint, retry.target, retry.duration_s) == (first.joint, first.target, first.duration_s)
    assert retry.sequence == 2 and len(sent) == 2
    with pytest.raises(SafetyError, match='retry_not_ready'):
        gate.retry_pending(token, received_at=now[0])
    now[0] += 1.5
    assert gate.status().state == 'ESTOP'
    with pytest.raises(SafetyError):
        gate.retry_pending(token, received_at=now[0])
    assert len(sent) == 2


def test_retry_rejects_stale_vision_and_observed_movement():
    now = [10.0]
    sent = []
    gate = ActuatorSafety(sent.append, clock=lambda: now[0], integer_feedback=True)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J1': 120, 'J3': 100})
    token = gate.arm('test')
    gate.command(token, 1, 'J1', 126, received_at=now[0])
    for _ in range(3):
        now[0] += .31
        gate.update_feedback({'J1': 122, 'J3': 100})
    with pytest.raises(SafetyError, match='command_age'):
        gate.retry_pending(token, received_at=now[0] - .2)
    with pytest.raises(SafetyError, match='retry_motion_observed'):
        gate.retry_pending(token, received_at=now[0])
    assert len(sent) == 1


@pytest.mark.parametrize('reading,acknowledged', [(77, False), (78, False), (79, True), (80, True), (81, True), (83, True), (84, False)])
def test_j3_measured_tolerance_still_requires_progress(reading, acknowledged):
    now = [10.0]
    gate = ActuatorSafety(lambda _: None, clock=lambda: now[0], integer_feedback=True)
    for _ in range(3):
        now[0] += .01
        gate.update_feedback({'J1': 120, 'J3': 77})
    token = gate.arm('test')
    gate.command(token, 1, 'J3', 81, received_at=now[0])
    now[0] += .4
    gate.update_feedback({'J1': 120, 'J3': reading})
    assert (gate.status().pending is None) == acknowledged
