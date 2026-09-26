import asyncio
import pytest

from milo_next.manual_joystick import ManualJoystick
from milo_next.operator_motion import OperatorMotion
from milo_next.safety import SafetyError


class Arm:
    def __init__(self):
        self.sent = []
        self.pending = False
        self.stable = True
        self.state = 'DISARMED'
        self.pose = {'J1':90,'J2':110,'J3':90,'J4':30,'J5':90,'J6':26}

    def status(self):
        return {'state':self.state,'raw_pose':dict(self.pose),'pending':self.pending,
                'manual':{j:{'feedback_stable':self.stable,'limits':[0,180]} for j in self.pose if j!='J6'}}

    def nudge(self, joint, direction, *, goal, speed):
        self.sent.append((joint,direction,goal,speed))
        self.pending = True

    def disarm(self, **kwargs):
        pass


def test_lease_expires_without_motion_and_stale_updates_cannot_revive():
    async def run():
        arm = Arm(); now = [10.]
        stick = ManualJoystick(OperatorMotion(arm), clock=lambda: now[0])
        session = stick.start()
        now[0] += .7
        with pytest.raises(SafetyError, match='expired'):
            stick.update(session,1,'J1',1,8)
        await stick.task
        assert not arm.sent and not stick.active
    asyncio.run(run())


def test_pending_not_overwritten_stop_drops_queue_and_new_session_rejects_old_updates():
    async def run():
        arm = Arm(); stick = ManualJoystick(OperatorMotion(arm))
        session = stick.start()
        stick.update(session,1,'J1',1,8)
        await asyncio.sleep(.04)
        assert len(arm.sent)==1 and arm.sent[0]==('J1',1,94,8)
        stick.update(session,2,'J3',1,4)
        await asyncio.sleep(.04)
        assert len(arm.sent)==1
        with pytest.raises(SafetyError, match='stale'):
            stick.update(session,2,'J1',1,8)
        await stick.stop(session)
        arm.pending=False
        await asyncio.sleep(.04)
        assert len(arm.sent)==1 and arm.pose['J6']==26
        new = stick.start()
        await stick.stop(session)
        assert stick.active
        with pytest.raises(SafetyError, match='expired'):
            stick.update(session,3,'J1',1,8)
        await stick.stop(new)
    asyncio.run(run())


@pytest.mark.parametrize('joint,direction,speed', [('J6',1,8),([],1,8),('J1',True,8),('J1',1,9),('J1',1,3),('J1',1,True)])
def test_invalid_inputs_do_not_send(joint,direction,speed):
    async def run():
        arm=Arm(); stick=ManualJoystick(OperatorMotion(arm)); session=stick.start()
        with pytest.raises(SafetyError,match='invalid'):
            stick.update(session,1,joint,direction,speed)
        await stick.stop()
        assert not arm.sent
    asyncio.run(run())


def test_bounds_feedback_and_operator_cancellation():
    async def run():
        arm=Arm(); motion=OperatorMotion(arm); stick=ManualJoystick(motion)
        arm.pose['J1']=180
        session=stick.start(); stick.update(session,1,'J1',1,8)
        await asyncio.sleep(.04)
        assert stick.status['state']=='limit' and not arm.sent
        arm.stable=False
        stick.update(session,2,'J1',-1,8)
        await asyncio.sleep(.04)
        assert not arm.sent and stick.status['state']=='waiting_feedback'
        motion.cancel(); await stick.task
        assert not arm.sent
        arm.state='ESTOP'
        with pytest.raises(SafetyError,match='estop'):
            stick.start()
    asyncio.run(run())
