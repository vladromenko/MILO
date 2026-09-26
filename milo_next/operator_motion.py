"""Serialized operator requests; never enabled by service startup or an LLM."""
import asyncio
import time

from .safety import SafetyError


HOME = (('J4', 0), ('J3', 75), ('J2', 115))


class OperatorMotion:
    def __init__(self, actuator):
        self.actuator = actuator
        self.lock = asyncio.Lock()
        self.generation = 0
        self.home_status = {'state': 'idle', 'joint': None, 'error': None}
        self.move_status = {'state': 'idle', 'joint': None, 'error': None}

    def cancel(self):
        self.generation += 1

    @staticmethod
    def check(status):
        if status['state'] == 'ESTOP':
            raise SafetyError('arm_estop: ' + str(status.get('error') or status.get('reason') or 'inspection_required'))
        if status['state'] != 'DISARMED':
            raise SafetyError('manual_requires_disarmed')
        fw = status.get('firmware')
        if fw and fw.get('fault'):
            raise SafetyError('controller_fault_requires_inspection')

    async def _step(self, joint, direction, generation, *, scan=False, goal=None, allowed=None):
        deadline = time.monotonic() + 4
        sent = False
        while time.monotonic() < deadline:
            if generation != self.generation:
                raise SafetyError('operator_cancelled')
            status = self.actuator.status()
            self.check(status)
            fw = status.get('firmware')
            if sent:
                pending = status.get('pending') or any(
                    item.get('pending') for item in status.get('manual', {}).values())
                if not pending and (not fw or (fw['fresh'] and not fw['pending_joint']
                                               and fw['await_sequence'] is None)):
                    return status
            else:
                if allowed is not None and not allowed():
                    await asyncio.sleep(.05)
                    continue
                if status.get('pending') or any(item.get('pending') for item in status.get('manual', {}).values()):
                    await asyncio.sleep(.05)
                    continue
                try:
                    if goal is not None:
                        self.actuator.nudge(joint, direction, _scan=scan, goal=goal)
                    elif scan:
                        self.actuator.nudge(joint, direction, _scan=True)
                    else:
                        self.actuator.nudge(joint, direction)
                    sent = True
                except SafetyError as exc:
                    # These rejects publish no movement. Never retry a sent step.
                    if str(exc) not in {'controller_not_acknowledged_or_busy',
                                        'controller_not_ready', 'feedback_unstable'}:
                        raise
            await asyncio.sleep(.05)
        raise SafetyError('manual_step_not_confirmed')

    async def step(self, joint, direction):
        if self.lock.locked():
            raise SafetyError('operator_motion_busy')
        async with self.lock:
            try:
                return await self._step(joint, direction, self.generation)
            finally:
                self.actuator.disarm(force=True)

    async def home(self):
        if self.lock.locked():
            raise SafetyError('operator_motion_busy')
        async with self.lock:
            generation = self.generation
            self.home_status = {'state': 'running', 'joint': None, 'error': None}
            try:
                async with asyncio.timeout(120):
                    for joint, goal in HOME:
                        self.home_status['joint'] = joint
                        while True:
                            if generation != self.generation:
                                raise SafetyError('operator_cancelled')
                            status = self.actuator.status()
                            self.check(status)
                            fw = status.get('firmware')
                            if fw and (not fw['fresh'] or fw['pending_joint'] or fw['await_sequence'] is not None):
                                raise SafetyError('controller_not_ready')
                            current = status.get('raw_pose', {}).get(joint)
                            if type(current) is not int:
                                raise SafetyError('feedback_unavailable')
                            if abs(current - goal) <= 2:
                                break
                            await self._step(joint, 1 if goal > current else -1, generation, goal=goal)
                self.home_status.update(state='complete', joint=None)
            except BaseException as exc:
                self.home_status.update(state='stopped', error=str(exc) or type(exc).__name__)
                raise
            finally:
                self.actuator.disarm(force=True)
        return self.actuator.status()

    async def move_to(self, joint, target):
        self.actuator.validate_target(joint, target)
        if self.lock.locked():
            raise SafetyError('operator_motion_busy')
        async with self.lock:
            generation = self.generation
            self.move_status = {'state': 'running', 'joint': joint, 'target': target, 'error': None}
            try:
                async with asyncio.timeout(120):
                    while True:
                        if generation != self.generation:
                            raise SafetyError('operator_cancelled')
                        status = self.actuator.status()
                        self.check(status)
                        current = status.get('raw_pose', {}).get(joint)
                        if type(current) is not int:
                            raise SafetyError('feedback_unavailable')
                        if abs(current - target) <= 2:
                            break
                        await self._step(joint, 1 if target > current else -1, generation, goal=target)
                self.move_status.update(state='complete')
                return self.actuator.status()
            except BaseException as exc:
                self.move_status.update(state='stopped', error=str(exc) or type(exc).__name__)
                raise
            finally:
                self.actuator.disarm(force=True)

    async def recover(self):
        if self.lock.locked():
            raise SafetyError('operator_motion_busy')
        async with self.lock:
            generation = self.generation
            self.actuator.disarm(force=True)
            reset = False
            for _ in range(100):
                if generation != self.generation:
                    raise SafetyError('operator_cancelled')
                if self.actuator.status()['state'] == 'ESTOP':
                    if reset:
                        raise SafetyError('fault_recurred_during_recovery')
                    self.actuator.reset()
                    reset = True
                try:
                    ready = self.actuator.rebase()
                    self.home_status = {'state': 'idle', 'joint': None, 'error': None}
                    self.move_status = {'state': 'idle', 'joint': None, 'error': None}
                    return ready
                except SafetyError as exc:
                    if str(exc) not in {'feedback_unstable', 'recovery_wait_for_settle', 'pending_manual_motion', 'recovery_requires_disarmed'}:
                        raise
                await asyncio.sleep(.05)
            raise SafetyError('fresh_stable_feedback_required')
