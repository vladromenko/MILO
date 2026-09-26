import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from milo_next.settings import service_prefix


def load_script(name):
    path = Path(__file__).resolve().parents[1] / 'scripts' / name
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_public_service_prefix_is_accepted():
    assert service_prefix({'service_prefix': 'milo'}) == 'milo'
    for value in ('milo-final', 'milo-next', 'x;shutdown'):
        with pytest.raises(ValueError):
            service_prefix({'service_prefix': value})


def test_runtime_configuration_preserves_shared_secrets(tmp_path, monkeypatch):
    module = load_script('configure_runtime.py')
    root = tmp_path / 'MILO'
    (root / 'config').mkdir(parents=True)
    runtime = {'token': 't' * 64, 'role': 'brain', 'service_prefix': 'milo'}
    network = {'password': 'existing-network-password', 'wifi': 'old'}
    (root / 'config/runtime.json').write_text(json.dumps(runtime))
    (root / 'config/network.json').write_text(json.dumps(network))
    monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(module, 'initialize', lambda role: None)
    monkeypatch.setattr(module, 'settings', lambda: json.loads((root / 'config/runtime.json').read_text()))
    monkeypatch.setattr(module.sys, 'argv', ['configure_runtime.py', 'edge', '--wifi', 'wlan0'])
    module.main()
    updated_runtime = json.loads((root / 'config/runtime.json').read_text())
    updated_network = json.loads((root / 'config/network.json').read_text())
    assert updated_runtime['token'] == 't' * 64
    assert updated_runtime['role'] == 'edge'
    assert updated_network == {'password': 'existing-network-password', 'wifi': 'wlan0'}
    assert (root / 'config/runtime.json').stat().st_mode & 0o777 == 0o600
