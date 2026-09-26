import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from milo_next.audio_controls import AudioControls, mixer_command
from milo_next.memory import MemoryStore
from milo_next.languages import LANGUAGES
from milo_next.preferences import Preferences, validate
from milo_next.settings import service_prefix
from milo_next.web_panel import create_app, access_code


def test_settings_atomic_private_and_bounded(tmp_path):
    path = tmp_path / 'settings.json'
    preferences = Preferences(path)
    preferences.update({'speech_rate': 1.2, 'muted': True})
    assert Preferences(path).values['muted'] is True
    assert path.stat().st_mode & 0o777 == 0o600
    for value in ({'speaker_volume': 101}, {'mic_gain': True}, {'speech_rate': float('nan')},
                  {'voice': 'download-me'}, {'mode': 'track'}, {'token': 'secret'}, {'muted': 1}):
        with pytest.raises(ValueError):
            preferences.update(value)
    assert preferences.values['speech_rate'] == 1.2
    assert service_prefix({'service_prefix': 'milo'}) == 'milo'
    with pytest.raises(ValueError):
        service_prefix({'service_prefix': 'x;shutdown'})


@pytest.mark.parametrize('language', LANGUAGES)
def test_language_is_explicit_persistent_and_selects_voice(tmp_path, language):
    path = tmp_path / 'settings.json'
    preferences = Preferences(path)
    preferences.update({'language': language})
    restored = Preferences(path)
    assert restored.values['language'] == language
    assert restored.values['voice'] == LANGUAGES[language][1]
    audio = SimpleNamespace(status=SimpleNamespace())
    controls = AudioControls(audio)
    controls.apply({'language': language})
    assert audio.language == language
    with pytest.raises(ValueError):
        preferences.update({'auto_language': True})
    assert preferences.values['language'] == language


def test_audio_controls_selected_devices_only(monkeypatch):
    calls = []
    monkeypatch.setattr('milo_next.audio_controls.subprocess.run', lambda args, **kw: calls.append(args))
    audio = SimpleNamespace(status=SimpleNamespace(input_device='UM02: USB Audio (hw:2,0)',
                                                  output_device='UACDemo (hw:3,0)'))
    controls = AudioControls(audio)
    controls.apply({'speaker_volume': 45, 'mic_gain': 70, 'muted': True, 'speech_rate': 1.25})
    assert calls == [['amixer', '-q', '-c', '3', 'sset', 'PCM', '0%'],
                     ['amixer', '-q', '-c', '2', 'sset', 'Mic', '70%']]
    assert audio.synthesis_settings['length_scale'] == .8
    controls.apply({'muted': False})
    assert calls[-1][-1] == '45%'
    with pytest.raises(ValueError):
        mixer_command('default', 'PCM', 40)


def test_operator_memory_no_embeddings_and_conflicting_updates(tmp_path):
    with MemoryStore(tmp_path / 'memory.db') as memory:
        person = memory.create_person('Alice')
        memory.add_face_embedding(person, [1, 0], 'test')
        memory.put_fact(person, 'drink', 'tea')
        assert memory.people()[0]['observations'] == 1
        assert 'vector' not in memory.people()[0]
        fact = memory.facts(person)[0]
        memory.edit_fact(person, 'drink', 'coffee', fact['updated_at'])
        session = memory.new_session(person)
        assert 'coffee' in memory.context(session, 'coffee')
        with pytest.raises(ValueError):
            memory.delete_fact_version(person, 'drink', fact['updated_at'])
        current = memory.facts(person)[0]
        memory.delete_fact_version(person, 'drink', current['updated_at'])
        assert memory.facts(person) == []


def test_panel_auth_csrf_no_motion_and_no_public_memory(tmp_path):
    async def run():
        code = access_code(tmp_path / 'access.json')
        assert access_code(tmp_path / 'access.json') == code
        app = create_app({'token': 'internal-secret', 'service_prefix': 'milo'}, code)
        async with TestClient(TestServer(app)) as client:
            for path in ['/api/memory', '/api/status', '/api/logs', '/api/preview']:
                assert (await client.get(path)).status == 401
            assert (await client.post('/login', json={'code': 'wrong'})).status == 401
            assert (await client.post('/login', json={'code': code}, headers={'Origin': 'http://evil.test'})).status == 403
            assert (await client.get('/', headers={'Host': 'evil.test'})).status == 403
            response = await client.post('/login', json={'code': code})
            assert response.status == 200
            csrf = (await response.json())['csrf']
            cookie = response.cookies['milo_operator']
            assert cookie['httponly'] and cookie['samesite'] == 'Strict'
            assert (await client.post('/api/control', json={'action': 'estop'})).status == 403
            headers = {'X-Milo-CSRF': csrf}
            for joint, target, posture in [('J6',90,True), ('J1',True,True), ('J1',90.0,True), ('J1',90,False), ([],90,True)]:
                assert (await client.post('/api/control', json={'action':'move','joint':joint,'target':target,'posture':posture}, headers=headers)).status == 403
            for action in ['recover', 'scan', 'move']:
                assert (await client.post('/api/control', json={'action':action})).status == 403
            assert (await client.post('/api/control', json={'action':'joystick_start','posture':False}, headers=headers)).status == 403
            for joint, direction, speed in [('J6',1,8),('J1',True,8),('J1',1,9),([],1,8)]:
                body = {'action':'joystick_update','session':'a'*48,'sequence':1,'joint':joint,'direction':direction,'speed':speed}
                assert (await client.post('/api/control', json=body, headers=headers)).status == 403
            assert (await client.post('/api/control', json={'action': 'reset'})).status == 403
            for action in ['jog', 'chat']:
                assert (await client.post('/api/control', json={'action': action}, headers=headers)).status == 403
            for joint in ['J2', 'J4', 'J5', 'J6']:
                assert (await client.post('/api/control', json={'action': 'nudge', 'joint': joint, 'delta': 1}, headers=headers)).status == 403
            for delta in [0, 2, True, 1.0]:
                assert (await client.post('/api/control', json={'action': 'nudge', 'joint': 'J1', 'delta': delta}, headers=headers)).status == 403
            assert (await client.get('/api/logs?component=../../etc/passwd')).status == 400
            assert (await client.post('/api/logout', json={}, headers=headers)).status == 200
            assert (await client.get('/api/session')).status == 401
    asyncio.run(run())


def test_login_rate_limited():
    async def run():
        async with TestClient(TestServer(create_app({'token': 'private'}, 'code'))) as client:
            for _ in range(30):
                assert (await client.post('/login', json={'code': 'wrong'})).status == 401
            assert (await client.post('/login', json={'code': 'code'})).status == 429
    asyncio.run(run())


def test_manual_move_and_recover_forward_only_valid_authenticated_actions(monkeypatch):
    from aiohttp import web
    from milo_next.web_panel import Panel
    sent = []
    async def upstream(self, method, path, body=None, **kwargs):
        sent.append(body)
        return web.json_response({'ok': True})
    monkeypatch.setattr(Panel, 'upstream', upstream)
    async def run():
        async with TestClient(TestServer(create_app({'token':'private'}, 'code'))) as client:
            login = await client.post('/login', json={'code':'code'})
            headers = {'X-Milo-CSRF': (await login.json())['csrf']}
            for body in [{'action':'recover'}, {'action':'scan'},
                         {'action':'joystick_start','posture':True},
                         {'action':'joystick_update','session':'a'*48,'sequence':1,'joint':'J1','direction':1,'speed':8},
                         {'action':'joystick_stop','session':'a'*48},
                         {'action':'move','joint':'J3','target':90,'posture':True}]:
                assert (await client.post('/api/control', json=body, headers=headers)).status == 200
                assert sent[-1] == body
    asyncio.run(run())
    assert len(sent) == 6
