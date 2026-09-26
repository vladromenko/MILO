import json
import subprocess

from scripts.setup_hailo_recovery import recover


def test_working_hailo_never_unloaded(tmp_path):
    def forbidden(*args):
        raise AssertionError(args)
    assert recover(tmp_path, available=lambda: True, devices=forbidden, execute=forbidden) == {'state': 'available'}


def test_missing_hardware_never_reloads_unrelated_devices(tmp_path):
    calls = []
    result = recover(tmp_path, available=lambda: False, devices=lambda: [], execute=lambda *args: calls.append(args))
    assert result['state'] == 'missing_or_ambiguous_pcie_device' and not calls


def test_recovery_resets_only_selected_device_and_reloads_driver(tmp_path):
    device = tmp_path / 'pci-device'
    device.mkdir()
    reset = device / 'reset'
    reset.touch()
    availability = iter([False, False, True])
    calls = []
    result = recover(tmp_path, available=lambda: next(availability), devices=lambda: [device],
        execute=lambda *args: calls.append(args), sleep=lambda _: None)
    assert result == {'state': 'recovered', 'attempts': 1}
    assert reset.read_text() == '1\n'
    assert calls == [('/usr/sbin/modprobe', '-r', 'hailo1x_pci'), ('/usr/sbin/modprobe', 'hailo1x_pci')]


def test_failed_recovery_is_limited_to_three_attempts_even_after_restart(tmp_path):
    calls = []
    for attempt in range(1, 4):
        result = recover(tmp_path, available=lambda: False, devices=lambda: [tmp_path],
            execute=lambda *args: calls.append(args), sleep=lambda _: None)
        assert result == {'state': 'device_still_missing', 'attempts': attempt}
    result = recover(tmp_path, available=lambda: False, devices=lambda: [tmp_path],
        execute=lambda *args: calls.append(args), sleep=lambda _: None)
    assert result['state'] == 'recovery_exhausted' and len(calls) == 6


def test_busy_module_is_not_forced_or_reset(tmp_path):
    reset = tmp_path / 'reset'
    reset.touch()
    calls = []
    def busy(*args):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    result = recover(tmp_path, available=lambda: False, devices=lambda: [tmp_path], execute=busy)
    assert result['state'] == 'recovery_failed' and len(calls) == 1
    assert reset.read_text() == ''
    assert json.loads((tmp_path / 'attempts.json').read_text()) == {'attempts': 1}


def test_invalid_counter_fails_without_reset(tmp_path):
    (tmp_path / 'attempts.json').write_text('{broken')
    calls = []
    assert recover(tmp_path, available=lambda: False, devices=lambda: [tmp_path],
        execute=lambda *args: calls.append(args))['state'] == 'invalid_attempt_counter'
    assert not calls
