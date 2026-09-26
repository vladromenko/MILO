import pytest

from milo_next.firmware_link import FirmwareLink
from milo_next.safety import SafetyError


@pytest.fixture
def link():
    now = [0.0]
    sent = []
    obj = FirmwareLink(sent.append, clock=lambda: now[0])
    return obj, now, sent


def ready(obj):
    obj.receive_version(6003)
    obj.receive_state(0)


def test_inert_start_and_explicit_ack(link):
    obj, now, sent = link
    obj.tick()
    assert sent == []
    with pytest.raises(SafetyError):
        obj.enable()
    ready(obj)
    assert sent == []
    obj.enable()
    assert sent == [1]
    with pytest.raises(SafetyError):
        obj.require_idle()
    obj.receive_state(1)
    obj.require_idle()
    obj.command_sent()
    with pytest.raises(SafetyError):
        obj.require_idle()
    obj.receive_state(1)
    with pytest.raises(SafetyError):
        obj.require_idle()
    obj.receive_state(1 | (4 << 8) | (1 << 16))
    with pytest.raises(SafetyError):
        obj.require_idle()
    obj.receive_state(1 | (1 << 16))
    obj.require_idle()


def test_lost_state_inhibits_and_does_not_auto_resume(link):
    obj, now, sent = link
    ready(obj)
    obj.enable()
    obj.receive_state(1)
    now[0] = .25
    obj.tick()
    assert sent == [1, 1]
    now[0] = .51
    obj.tick()
    assert sent == [1, 1, 0]
    ready(obj)
    obj.tick()
    assert not obj.requested


@pytest.mark.parametrize('bad', [-1, 4, 7 << 8, 1 << 31, True, '1', None])
def test_malformed_state_fails_closed(link, bad):
    obj, now, sent = link
    ready(obj)
    obj.enable()
    obj.receive_state(bad)
    assert not obj.requested and sent[-1] == 0


def test_fault_pending_cannot_reset_or_rearm(link):
    obj, now, sent = link
    ready(obj)
    obj.enable()
    obj.receive_state(2 | (4 << 8))
    for action in (obj.enable, obj.reset_fault, obj.require_idle):
        with pytest.raises(SafetyError):
            action()
    obj.receive_state(2)
    obj.reset_fault()
    assert sent[-1] == 2
    with pytest.raises(SafetyError):
        obj.enable()
    obj.receive_state(0)
    obj.enable()


def test_unknown_firmware_and_stale_version(link):
    obj, now, sent = link
    obj.receive_version(6001)
    obj.receive_state(0)
    with pytest.raises(SafetyError):
        obj.enable()
    assert sent == []
    ready(obj)
    now[0] = 3
    obj.receive_state(0)
    with pytest.raises(SafetyError):
        obj.enable()


def test_send_failure_revokes_permission(link):
    obj, now, sent = link
    ready(obj)
    def fail(value):
        raise OSError('transport closed')
    obj.publish_control = fail
    with pytest.raises(OSError):
        obj.enable()
    assert not obj.requested


def test_controller_restart_never_auto_enables(link):
    obj, now, sent = link
    ready(obj)
    obj.enable()
    obj.receive_state(1)
    obj.receive_state(0)
    assert sent == [1, 0]
    now[0] += .25
    obj.tick()
    assert sent == [1, 0] and not obj.requested


def test_unexpected_command_counter_revokes_permission(link):
    obj, now, sent = link
    ready(obj)
    obj.enable()
    obj.receive_state(1 | (12 << 16))
    assert not obj.requested and sent[-1] == 0


def test_command_counter_wrap_and_reset_block_until_ack(link):
    obj, now, sent = link
    obj.receive_version(6003)
    obj.receive_state(32767 << 16)
    obj.enable()
    obj.receive_state(1 | (32767 << 16))
    obj.require_idle()
    obj.command_sent()
    obj.receive_state(1 | (32767 << 16))
    with pytest.raises(SafetyError):
        obj.require_idle()
    obj.inhibit()
    with pytest.raises(SafetyError):
        obj.reset_fault()
    obj.receive_state(0)
    assert obj.await_sequence is None
    obj.reset_fault()
    assert sent[-1] == 2
