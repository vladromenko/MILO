import asyncio
import pytest

from milo_next.object_search import ObjectSearch, search_intent, waypoints
from milo_next.scene_memory import SceneMemory
from milo_next.operator_motion import OperatorMotion
from milo_next.safety import SafetyError, ActuatorSafety


@pytest.mark.parametrize('text,target', [('найди кружку', 'cup'), ('Milo, find my phone', 'cell phone'),
    ('посмотри по сторонам и поищи книгу', 'book'), ('осмотрись', None)])
def test_explicit_intents(text, target):
    assert search_intent(text) == {'target': target}


@pytest.mark.parametrize('text', ['не ищи кружку', 'I lost my cup', 'Can you explain how to find a cup?',
    'do not find my phone', 'я сказал найди кружку'])
def test_no_implicit_motion(text):
    assert search_intent(text) is None


def test_stop_unsupported_and_all_waypoints_bounded():
    assert search_intent('стоп') == {'stop': True}
    assert search_intent('найди ключи') == {'unsupported': True}
    assert search_intent('найди чашку и книгу') == {'unsupported': True}
    for x in range(0, 181):
        for y in (0, 34, 73, 75, 76, 130, 135):
            for joint, angle in waypoints({'J1': x, 'J3': y}):
                assert joint == 'J1' and 0 <= angle <= 180
        assert waypoints({'J1': x}) == [('J1', max(0, x - 36)), ('J1', min(180, x + 36)), ('J1', x)]


@pytest.mark.parametrize('pose', [{}, {'J1': True}, {'J1': 12.5}, {'J1': float('nan')}, {'J1': -1}, {'J1': 181}])
def test_scan_rejects_invalid_base_without_requiring_vertical_pose(pose):
    with pytest.raises(SafetyError, match='valid_base_position_required'):
        waypoints(pose)


class Arm:
    def __init__(self):
        self.pose = {'J1': 120, 'J3': 90, 'J6': 42}
        self.sent = []

    def status(self):
        return {'state': 'DISARMED', 'raw_pose': dict(self.pose), 'pending': None, 'manual': {}}

    def disarm(self, **kwargs):
        pass

    def nudge(self, joint, direction):
        self.sent.append(joint)
        self.pose[joint] += direction * (4 if joint == 'J3' else 2)


def test_full_scan_uses_frozen_base_origin_and_never_commands_other_joints():
    class Motion(OperatorMotion):
        async def _step(self, joint, direction, generation, **kwargs):
            assert joint == 'J1' and kwargs['scan'] is True
            self.actuator.sent.append((joint, kwargs['goal']))
            self.actuator.pose[joint] = kwargs['goal']
    async def run():
        arm, results, views = Arm(), [], []
        arm.pose.update(J2=138, J3=34, J4=3, J5=87)
        original = dict(arm.pose)
        async def complete(result):
            results.append(result)
        async def capture():
            views.append(arm.pose['J1'])
        search = ObjectSearch(Motion(arm), lambda: {}, lambda: True, complete, capture)
        search.start(None)
        arm.pose['J1'] = 122  # A later reading must not redefine the requested origin.
        await search.task
        assert results[-1]['state'] == 'complete'
        assert arm.sent == [('J1', 84), ('J1', 156), ('J1', 120)]
        assert views == [122, 84, 156, 120]
        assert arm.pose == original
    asyncio.run(run())


def test_found_requires_distinct_frames_and_stale_fails_without_moving():
    async def run():
        arm, results = Arm(), []
        seq = 0
        def observe():
            nonlocal seq
            seq += 1
            return {'captured_monotonic': 10, 'timings': {'objects_frame_seq': seq,
                'objects_captured_monotonic': 10}, 'status': {'backends': {'objects': 'hailort_h10'}},
                'objects': [{'label': 'cup', 'score': .9, 'bbox': [.1, .2, .2, .3]}]}
        async def complete(result):
            results.append(result)
        search = ObjectSearch(OperatorMotion(arm), observe, lambda: True, complete)
        search.start('cup')
        await search.task
        assert seq >= 3 and results[-1]['state'] == 'found' and not arm.sent
        search.observe = lambda: {'captured_monotonic': 10}
        search.start('cup')
        await search.task
        assert results[-1]['state'] == 'stopped' and not arm.sent
    asyncio.run(run())


def test_cancel_and_duplicate_requests():
    async def run():
        arm = Arm()
        async def complete(result):
            pass
        search = ObjectSearch(OperatorMotion(arm), lambda: {}, lambda: True, complete)
        search.start(None)
        with pytest.raises(SafetyError, match='busy'):
            search.start(None)
        search.cancel()
        await asyncio.gather(search.task, return_exceptions=True)
        assert not arm.sent and arm.pose['J6'] == 42
    asyncio.run(run())


def test_general_scan_can_observe_without_hailo_and_holds_for_camera_gap(monkeypatch):
    monkeypatch.setattr('milo_next.object_search.waypoints', lambda pose: [])
    async def run():
        arm, results = Arm(), []
        available = True
        async def complete(result):
            results.append(result)
        search = ObjectSearch(OperatorMotion(arm), lambda: {}, lambda: available, complete)
        search.start(None)
        available = False
        await asyncio.sleep(.15)
        assert search.active and not arm.sent
        available = True
        await search.task
        assert results[-1]['state'] == 'complete'
        assert results[-1]['detector_warning'] == 'object_detector_unavailable'
        assert not arm.sent
    asyncio.run(run())


def test_scene_memory_bounded_detached_expiring_no_personal_images():
    memory = SceneMemory()
    objects = [{'label': 'cup', 'score': .9, 'bbox': [0, 0, 1, 1], 'embedding': [1]}]
    for i in range(2000):
        memory.observe(objects, i)
    objects[0]['bbox'][0] = 9
    assert len(memory.frames) == 1800
    summary = memory.summary(2000)
    assert summary[0]['last_seen_age_s'] == 1
    assert summary[0]['bbox'][0] == 0 and 'embedding' not in summary[0]
    assert not memory.summary(2400)


@pytest.mark.parametrize('measured,confirmed', [(77, True), (76, False), (75, False)])
def test_j3_small_undertravel_requires_actual_progress_and_settled_samples(measured, confirmed):
    now = [10.0]
    gate = ActuatorSafety(lambda command: None, clock=lambda: now[0],
                          integer_feedback=True, manual_joint='J3')
    for stamp in (10, 10.1, 10.2):
        now[0] = stamp
        gate.update_feedback({'J3': 76})
    token = gate.arm('operator-bounded-step')
    gate.command(token, 1, 'J3', 80, received_at=now[0])
    gate.disarm()
    for stamp in (11.2, 11.5):
        now[0] = stamp
        gate.update_feedback({'J3': measured})
        assert gate.status().pending is not None
    now[0] = 11.8
    gate.update_feedback({'J3': measured})
    assert (gate.status().pending is None) == confirmed
    if not confirmed:
        now[0] = 13.3
        assert gate.tick().state == 'ESTOP'
