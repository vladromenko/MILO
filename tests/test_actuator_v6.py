from types import SimpleNamespace, ModuleType
from unittest.mock import patch

import pytest

from test_actuator import FakeROS, Clock
from milo_next.actuator import Actuator
from milo_next.safety import SafetyError


class V6ROS(FakeROS):
    def __init__(self):
        super().__init__()
        self.controls = []

    @property
    def modules(self):
        msgs = ModuleType('std_msgs.msg')
        msgs.Int32 = SimpleNamespace
        return {**super().modules, 'std_msgs': ModuleType('std_msgs'), 'std_msgs.msg': msgs}

    def count_publishers(self, topic):
        assert topic in {'/arm_joint', '/arm_control'}
        return self.publishers

    def create_publisher(self, message_type, topic, depth):
        if topic == '/arm_control':
            return SimpleNamespace(get_subscription_count=lambda: self.subscribers,
                                   publish=lambda msg: self.controls.append(msg.data))
        return super().create_publisher(message_type, topic, depth)


def feed(act, clock, j4=-43):
    for _ in range(4):
        clock.advance(.1)
        act._feedback(SimpleNamespace(joint1=120, joint2=115, joint3=100,
                                     joint4=j4, joint5=90, joint6=40))


def test_v6_recovery_requires_handshake_and_never_commands_six(tmp_path):
    ros, clock = V6ROS(), Clock()
    with patch.dict('sys.modules', ros.modules):
        act = Actuator(clock=clock, latch_path=tmp_path/'ESTOP',
                       integer_feedback=True, firmware_v6=True)
        act.start()
        try:
            assert ros.sent == [] and ros.controls == []
            feed(act, clock)
            act._firmware_version(SimpleNamespace(data=6003))
            act._firmware_state(SimpleNamespace(data=0))
            with pytest.raises(SafetyError, match='acknowledged'):
                act.recover_j4_step()
            assert ros.controls == [1] and ros.sent == []
            act._firmware_state(SimpleNamespace(data=1))
            act.recover_j4_step()
            assert ros.sent == [(4, -41, 900)]
            with pytest.raises(SafetyError):
                act.nudge('J6', 1)
            with pytest.raises(SafetyError):
                act.recover_j4_step()
            assert len(ros.sent) == 1
            act.estop()
            assert ros.controls[-1] == 0
        finally:
            act.close()


def test_legacy_version_never_enables_v6_motion(tmp_path):
    ros, clock = V6ROS(), Clock()
    with patch.dict('sys.modules', ros.modules):
        act = Actuator(clock=clock, latch_path=tmp_path/'ESTOP',
                       integer_feedback=True, firmware_v6=True)
        act.start()
        try:
            feed(act, clock)
            act._firmware_version(SimpleNamespace(data=6001))
            act._firmware_state(SimpleNamespace(data=0))
            with pytest.raises(SafetyError):
                act.recover_j4_step()
            assert ros.sent == [] and ros.controls == []
        finally:
            act.close()
