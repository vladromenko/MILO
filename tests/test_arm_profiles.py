import asyncio
from copy import deepcopy

from aiohttp.test_utils import TestClient, TestServer
import pytest

from milo_next.arm_profiles import (ArmProfiles, defaults, validate_profile,
                                    activation_preconditions, ACTIVATION_BLOCK)
from milo_next.web_panel import create_app


def test_profile_drafts_never_activate_or_unlock_milo(tmp_path):
    path = tmp_path / 'drafts.json'
    profiles = ArmProfiles(path)
    assert not path.exists()
    draft = defaults()['standalone']
    draft['J6'].update(enabled=True, min=30, max=180)
    result = profiles.save({'profile': 'standalone', 'joints': draft, 'revision': 0})
    assert result['draft_only'] and not result['activation_available']
    assert not result['profiles']['milo']['J6']['enabled']
    assert ArmProfiles(path).snapshot()['revision'] == 1
    assert path.stat().st_mode & 0o777 == 0o600
    result['profiles']['milo']['J6']['enabled'] = True
    assert not profiles.snapshot()['profiles']['milo']['J6']['enabled']
    with pytest.raises(ValueError, match='reload'):
        profiles.save({'profile':'standalone','joints':draft,'revision':0})


@pytest.mark.parametrize('joint,key,value', [
    ('J4','min',-44), ('J5','max',271), ('J6','min',29), ('J1','speed',float('nan')),
    ('J1','speed',float('inf')), ('J1','speed',10**500), ('J1','speed',True),
    ('J1','min',1.5), ('J2','enabled',1), ('J2','min',170),
])
def test_profile_rejects_bad_limits(joint, key, value):
    draft = defaults()['standalone']
    draft[joint][key] = value
    with pytest.raises(ValueError):
        validate_profile('standalone', draft)


def test_milo_cannot_expand_assembly_limits_or_enable_six():
    for joint, key, value in [('J6','enabled',True), ('J2','max',180), ('J4','min',-43)]:
        draft = defaults()['milo']
        draft[joint][key] = value
        with pytest.raises(ValueError):
            validate_profile('milo', draft)


def test_activation_requires_removal_not_power_off_and_is_staged():
    arm = {'state':'DISARMED', 'pending':None, 'manual':{}}
    for body in ({}, {'screen_off':True}, {'screen_removed_and_cables_disconnected':'yes'}):
        with pytest.raises(ValueError, match='removed'):
            activation_preconditions('standalone', body, arm)
    with pytest.raises(ValueError, match=ACTIVATION_BLOCK):
        activation_preconditions('standalone', {'screen_removed_and_cables_disconnected':True}, arm)
    for state in ({'state':'ARMED'}, {'state':'DISARMED','manual':{'J4':{'pending':{'target':0}}}}):
        with pytest.raises(ValueError, match='stop movement'):
            activation_preconditions('milo', {}, state)


def test_corrupt_profile_not_silently_replaced(tmp_path):
    path = tmp_path / 'drafts.json'
    path.write_text('{broken')
    with pytest.raises(ValueError):
        ArmProfiles(path)
    assert path.read_text() == '{broken'


def test_profile_api_auth_csrf_revision_and_no_motion(tmp_path, monkeypatch):
    async def forbid(*args, **kwargs):
        raise AssertionError('profile editing must never call runtime or publish motion')
    monkeypatch.setattr('milo_next.web_panel.Panel.upstream', forbid)
    async def run():
        path = tmp_path / 'drafts.json'
        async with TestClient(TestServer(create_app({'token':'unused'}, 'preview', path))) as client:
            assert (await client.get('/api/arm-profiles')).status == 401
            response = await client.post('/login', json={'code':'preview'})
            headers = {'X-Milo-CSRF':(await response.json())['csrf']}
            data = await (await client.get('/api/arm-profiles')).json()
            body = {'profile':'standalone','joints':data['profiles']['standalone'],'revision':data['revision']}
            assert (await client.post('/api/arm-profiles',json=body)).status == 403
            responses = await asyncio.gather(*(client.post('/api/arm-profiles',json=body,headers=headers) for _ in range(2)))
            assert sorted(r.status for r in responses) == [200,409]
            assert (await client.post('/api/control',json={'action':'activate_profile','profile':'standalone'},headers=headers)).status == 403
            assert (await client.post('/api/control',json={'action':'nudge','joint':'J6','delta':1,'posture':True},headers=headers)).status == 403
            assert ArmProfiles(path).snapshot()['revision'] == 1
    asyncio.run(run())
