"""Dead-man manual control. No queued trajectory and no automatic fault reset."""
import asyncio
import secrets
import time

from .safety import SafetyError


LEASE_SECONDS = .6
JOINTS = frozenset({'J1', 'J2', 'J3', 'J4', 'J5'})


class ManualJoystick:
    def __init__(self, motion, *, clock=time.monotonic):
        self.motion, self.clock = motion, clock
        self.task = None
        self.session = None
        self.until = 0
        self.sequence = -1
        self.vector = None
        self.status = {'state': 'idle', 'error': None}

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def start(self):
        if self.active or self.motion.lock.locked():
            raise SafetyError('operator_motion_busy')
        self.motion.check(self.motion.actuator.status())
        self.session = secrets.token_hex(24)
        self.sequence, self.vector = -1, None
        self.until = self.clock() + LEASE_SECONDS
        self.status = {'state': 'holding', 'error': None}
        self.task = asyncio.create_task(self._run(self.motion.generation))
        return self.session

    def update(self, session, sequence, joint, direction, speed):
        if not self.active or session != self.session or self.clock() >= self.until:
            raise SafetyError('joystick_session_expired')
        if type(sequence) is not int or sequence <= self.sequence:
            raise SafetyError('joystick_stale_update')
        if (type(joint) is not str or joint not in JOINTS or type(direction) is not int
                or direction not in (-1, 0, 1) or type(speed) is not int or not 4 <= speed <= 8):
            raise SafetyError('invalid_joystick_input')
        self.sequence = sequence
        self.vector = (joint, direction, speed)
        self.until = self.clock() + LEASE_SECONDS

    async def stop(self, session=None):
        if session is not None and session != self.session:
            return
        self.until = 0
        self.vector = None
        self.session = None
        if self.active:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.status.update(state='stopped')
        self.motion.actuator.disarm(force=True)

    async def _run(self, generation):
        try:
            async with self.motion.lock:
                while self.clock() < self.until and generation == self.motion.generation:
                    arm = self.motion.actuator.status()
                    self.motion.check(arm)
                    pending = arm.get('pending') or any(g.get('pending') for g in arm.get('manual', {}).values())
                    if self.vector and self.vector[1] and not pending:
                        joint, direction, speed = self.vector
                        gate = arm.get('manual', {}).get(joint, {})
                        current = arm.get('raw_pose', {}).get(joint)
                        if type(current) is not int or not gate.get('feedback_stable'):
                            self.status.update(state='waiting_feedback')
                        else:
                            low, high = gate.get('command_limits') or gate['limits']
                            target = min(high, current + 4) if direction > 0 else max(low, current - 4)
                            if target == current:
                                self.status.update(state='limit', joint=joint)
                            else:
                                try:
                                    self.motion.actuator.nudge(joint, direction, goal=target, speed=speed)
                                    self.status.update(state='moving', joint=joint, speed=speed)
                                except SafetyError as exc:
                                    if str(exc) not in {'feedback_unstable', 'minimum_interval', 'maximum_velocity',
                                                       'controller_not_ready', 'controller_not_acknowledged_or_busy'}:
                                        raise
                    await asyncio.sleep(.03)
            self.status.update(state='stopped', error=None)
        except asyncio.CancelledError:
            self.status.update(state='stopped')
            raise
        except Exception as exc:
            self.status.update(state='stopped', error=str(exc))
        finally:
            self.session = None
            self.motion.actuator.disarm(force=True)
