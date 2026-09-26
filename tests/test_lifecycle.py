import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
import pytest

from milo_next.lifecycle import Lifecycle
from milo_next.web_panel import create_app


def unit_status(target='inactive', launch='inactive', stop='inactive'):
    return '\n\n'.join(
        f'Id=milo{suffix}\nLoadState=loaded\nActiveState={state}\nResult=success'
        for suffix, state in [('.target', target), ('-launch.service', launch), ('-stop.service', stop)])


@pytest.mark.parametrize('target,launch,stop,expected', [
    ('inactive','inactive','inactive','stopped'), ('active','inactive','inactive','running'),
    ('active','activating','inactive','starting'), ('active','deactivating','activating','stopping'),
    ('active','failed','inactive','start_failed'), ('inactive','inactive','failed','stop_failed'),
])
def test_lifecycle_states(target, launch, stop, expected):
    manager = Lifecycle('milo')
    async def command(*args):
        return unit_status(target, launch, stop)
    manager.systemctl = command
    assert asyncio.run(manager.snapshot())['state'] == expected


def test_start_is_idempotent_but_stop_can_cancel_start():
    manager = Lifecycle('milo')
    calls = []
    async def command(*args):
        calls.append(args)
        return unit_status('active', 'activating')
    manager.systemctl = command
    async def run():
        assert (await manager.control('start'))['state'] == 'starting'
        assert len(calls) == 1
        assert (await manager.control('stop'))['state'] == 'stopping'
        assert calls[-1] == ('--no-block', 'start', 'milo-stop.service')
        with pytest.raises(ValueError):
            await manager.control('reboot')
    asyncio.run(run())


def test_missing_units_are_not_reported_as_standby():
    manager = Lifecycle('milo')
    async def command(*args):
        return unit_status().replace('LoadState=loaded', 'LoadState=not-found')
    manager.systemctl = command
    with pytest.raises(RuntimeError, match='Install'):
        asyncio.run(manager.snapshot())


def test_phone_lifecycle_auth_csrf_validation_and_offline_access(monkeypatch, tmp_path):
    calls = []
    async def command(self, *args):
        assert args[0] != 'reset-failed', 'Inactive oneshots may have been garbage-collected by systemd'
        calls.append(args)
        return unit_status()
    monkeypatch.setattr(Lifecycle, 'systemctl', command)
    async def run():
        cfg = {'token':'private-test-token', 'service_prefix':'milo'}
        async with TestClient(TestServer(create_app(cfg, 'preview', tmp_path / 'drafts'))) as client:
            assert (await client.get('/api/runtime')).status == 401
            assert not calls
            login = await client.post('/login', json={'code':'preview'})
            headers = {'X-Milo-CSRF':(await login.json())['csrf']}
            assert (await (await client.get('/api/runtime')).json())['state'] == 'stopped'
            assert (await client.post('/api/runtime',json={'action':'start'})).status == 403
            for body in [{'action':'reboot'}, {'action':'start','command':'anything'}, [], {'action':None}]:
                assert (await client.post('/api/runtime',json=body,headers=headers)).status == 400
            hostile = {**headers, 'Origin':'http://untrusted.example'}
            assert (await client.post('/api/runtime',json={'action':'start'},headers=hostile)).status == 403
            response = await client.post('/api/runtime',json={'action':'start'},headers=headers)
            assert response.status == 202
            assert (await response.json())['state'] == 'starting'
            assert calls[-1] == ('--no-block', 'start', 'milo-launch.service')
    asyncio.run(run())


@pytest.mark.parametrize('role', ['brain', 'edge'])
def test_installer_separates_standby_panel_from_robot_boot(tmp_path, monkeypatch, role):
    source = Path(__file__).resolve().parents[1] / 'scripts/install_services.py'
    spec = importlib.util.spec_from_file_location('test_install_services', source)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer, 'ROOT', tmp_path / 'app')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(installer, 'initialize', lambda *_: None)
    monkeypatch.setattr(installer, 'service_prefix', lambda: 'milo')
    monkeypatch.setattr(installer.sys, 'argv', ['install_services.py', role])
    calls = []
    monkeypatch.setattr(installer.subprocess, 'run', lambda args, **_: calls.append(args) or SimpleNamespace(returncode=0))
    units = tmp_path / '.config/systemd/user'
    old_link = units / 'milo.target.wants/milo-web.service'
    old_link.parent.mkdir(parents=True)
    old_link.symlink_to('../milo-web.service')
    installer.main()
    assert 'web.service' not in (units / 'milo.target').read_text()
    assert ['systemctl','--user','disable','milo.target'] in calls
    assert ['systemctl','--user','enable','milo.target'] not in calls
    if role == 'brain':
        assert not old_link.is_symlink()
        panel = (units / 'milo-web.service').read_text()
        assert 'PartOf=' not in panel and 'WantedBy=default.target' in panel
        assert ['systemctl','--user','enable','milo-web.service'] in calls
        launch = (units / 'milo-launch.service').read_text()
        assert f'ExecStart={tmp_path}/app/milo start\n' in launch
        assert 'WantedBy' not in launch
        stop = (units / 'milo-stop.service').read_text()
        assert 'Conflicts=milo-launch.service' in stop
        assert 'After=milo-launch.service' in stop
