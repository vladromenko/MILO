import asyncio
import pytest

from milo_next.operator_motion import OperatorMotion
from milo_next.safety import SafetyError


class Arm:
    def __init__(self):
        self.pose = {'J1':161, 'J2':138, 'J3':35, 'J4':18, 'J5':88, 'J6':42}
        self.sent, self.off = [], 0
        self.fault = False

    def status(self):
        return {'state':'DISARMED', 'pending':None, 'manual':{}, 'raw_pose':dict(self.pose),
            'firmware':{'fresh':True, 'fault':self.fault, 'pending_joint':0, 'await_sequence':None}}

    def nudge(self, joint, direction, *, goal=None, _scan=False):
        self.sent.append(joint)
        target = self.pose[joint] + direction * (4 if joint == 'J3' else 2)
        self.pose[joint] = target if goal is None else min(target, goal) if direction > 0 else max(target, goal)

    def validate_target(self, joint, target):
        if joint not in {'J1','J2','J3','J4','J5'} or type(target) is not int:
            raise SafetyError('invalid_manual_target')

    def rebase(self):
        return ['J1','J2','J3','J4','J5']

    def disarm(self, **kwargs):
        self.off += 1


def test_latched_motion_fault_is_not_hidden_by_manual_mode_error():
    with pytest.raises(SafetyError, match='arm_estop: ack_timeout'):
        OperatorMotion.check({'state': 'ESTOP', 'reason': 'ack_timeout'})


def test_home_original_order_tolerance_and_no_six():
    arm = Arm()
    motion = OperatorMotion(arm)
    asyncio.run(motion.home())
    assert list(dict.fromkeys(arm.sent)) == ['J4', 'J3', 'J2']
    assert all(abs(arm.pose[j] - v) <= 2 for j,v in [('J4',0),('J3',75),('J2',115)])
    assert arm.pose['J1'] == 161 and arm.pose['J5'] == 88 and arm.pose['J6'] == 42
    assert motion.home_status['state'] == 'complete'
    assert arm.off == 1


def test_controller_fault_never_resets_or_sends():
    arm = Arm()
    arm.fault = True
    motion = OperatorMotion(arm)
    with pytest.raises(SafetyError, match='controller_fault'):
        asyncio.run(motion.home())
    assert not arm.sent
    assert motion.home_status['state'] == 'stopped'


def test_no_duplicate_step_after_acceptance_and_cancel_blocks_queue():
    async def run():
        arm = Arm()
        motion = OperatorMotion(arm)
        await motion.step('J4', -1)
        assert arm.sent == ['J4']
        generation = motion.generation
        motion.cancel()
        with pytest.raises(SafetyError, match='cancelled'):
            await motion._step('J4', -1, generation)
        assert arm.sent == ['J4']
    asyncio.run(run())


def test_confirmation_handshake_does_not_repeat_motion():
    class HandshakeArm(Arm):
        attempts = 0
        def nudge(self, joint, direction):
            self.attempts += 1
            if self.attempts == 1:
                raise SafetyError('controller_not_acknowledged_or_busy')
            super().nudge(joint, direction)
    arm = HandshakeArm()
    asyncio.run(OperatorMotion(arm).step('J4', -1))
    assert arm.attempts == 2 and arm.sent == ['J4']


def test_target_motion_and_recovery_never_command_six():
    arm = Arm()
    motion = OperatorMotion(arm)
    asyncio.run(motion.move_to('J3', 42))
    assert 40 <= arm.pose['J3'] <= 42
    assert set(arm.sent) == {'J3'} and arm.pose['J6'] == 42
    sent = list(arm.sent)
    assert asyncio.run(motion.recover()) == ['J1','J2','J3','J4','J5']
    assert arm.sent == sent and motion.move_status['state'] == 'idle'
    with pytest.raises(SafetyError):
        asyncio.run(motion.move_to('J6', 90))
    assert arm.sent == sent


def test_step_waits_for_camera_and_cancel_interrupts_without_publication():
    async def run():
        arm = Arm()
        motion = OperatorMotion(arm)
        task = asyncio.create_task(motion._step('J1', 1, motion.generation,
                                  scan=True, goal=165, allowed=lambda: False))
        await asyncio.sleep(.1)
        assert arm.sent == []
        motion.cancel()
        with pytest.raises(SafetyError, match='cancelled'):
            await task
        assert arm.sent == []
    asyncio.run(run())


def test_recover_waits_for_pending_and_resets_a_latched_fault_once():
    class RecoverArm(Arm):
        pending = True
        stopped = False
        resets = 0
        def status(self):
            status = super().status()
            status['state'] = 'ESTOP' if self.stopped else 'DISARMED'
            return status
        def reset(self):
            self.resets += 1
            self.stopped = False
            self.pending = False
        def rebase(self):
            if self.pending:
                raise SafetyError('pending_manual_motion')
            return super().rebase()
    async def run():
        arm = RecoverArm(); motion = OperatorMotion(arm)
        task = asyncio.create_task(motion.recover())
        await asyncio.sleep(.08)
        assert not task.done() and not arm.sent
        arm.stopped = True
        assert 'J1' in await task
        assert arm.resets == 1 and not arm.sent
    asyncio.run(run())
