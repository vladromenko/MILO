"""Offline arm profile drafts. Saving never changes the live actuator policy.

Activation intentionally stays unavailable until the V6 host adapter and the
physical assembly have been commissioned. The firmware's electrical envelope
is not evidence of collision clearance for a mounted screen or camera.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading

from .settings import write_private_json

ENVELOPE = {'J1': (0, 180), 'J2': (0, 180), 'J3': (0, 180),
            'J4': (-43, 180), 'J5': (0, 270), 'J6': (30, 180)}
MILO_LIMITS = {'J1': (0, 180), 'J2': (90, 165), 'J3': (76, 135),
               'J4': (-30, 115), 'J5': (70, 110), 'J6': (40, 40)}
REMOVAL_CONFIRMATION = 'screen_removed_and_cables_disconnected'
ACTIVATION_BLOCK = 'firmware_and_host_adapter_commissioning_required'


def defaults():
    # Standalone is deliberately disabled joint-by-joint, not a wide-open preset.
    return {name: {joint: {'min': low, 'max': high, 'speed': 8,
                          'enabled': name == 'milo' and joint != 'J6'}
                   for joint, (low, high) in MILO_LIMITS.items()}
            for name in ('milo', 'standalone')}


def validate_profile(name, values):
    if name not in {'milo', 'standalone'} or type(values) is not dict or set(values) != set(ENVELOPE):
        raise ValueError('all six named joints are required')
    result = {}
    for joint, value in values.items():
        if type(value) is not dict or set(value) != {'min', 'max', 'speed', 'enabled'}:
            raise ValueError('invalid joint settings')
        if (type(value['min']) is not int or type(value['max']) is not int or
                type(value['speed']) not in (int, float) or not 1 <= value['speed'] <= 36 or
                type(value['enabled']) is not bool):
            raise ValueError('invalid setting type')
        low, high = MILO_LIMITS[joint] if name == 'milo' else ENVELOPE[joint]
        if not low <= value['min'] <= value['max'] <= high or not 1 <= value['speed'] <= 36:
            raise ValueError('outside profile envelope')
        if name == 'milo' and joint == 'J6' and value['enabled']:
            raise ValueError('J6 remains locked in MILO')
        result[joint] = dict(value)
    return result


def activation_preconditions(name, body, arm):
    """Shared contract for the future commissioned adapter; never publishes."""
    if name not in {'milo', 'standalone'}:
        raise ValueError('unknown profile')
    if arm.get('state') != 'DISARMED' or arm.get('pending') or any(
            state.get('pending') for state in arm.get('manual', {}).values()):
        raise ValueError('stop movement before changing profile')
    if name == 'standalone' and body.get(REMOVAL_CONFIRMATION) is not True:
        raise ValueError('screen must be removed and cables disconnected, not merely switched off')
    # A browser assertion alone cannot certify firmware or the host command path.
    raise ValueError(ACTIVATION_BLOCK)


class ArmProfiles:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.revision = 0
        self.profiles = defaults()
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if (type(data) is not dict or set(data) != {'revision', 'profiles'} or
                    type(data['revision']) is not int or data['revision'] < 0 or
                    type(data['profiles']) is not dict or set(data['profiles']) != {'milo', 'standalone'}):
                raise ValueError('invalid profile file')
            self.profiles = {name: validate_profile(name, value) for name, value in data['profiles'].items()}
            self.revision = data['revision']

    def snapshot(self):
        with self.lock:
            return {'revision': self.revision, 'profiles': deepcopy(self.profiles),
                    'envelope': {name: list(bounds) for name, bounds in ENVELOPE.items()},
                    'milo_envelope': {name: list(bounds) for name, bounds in MILO_LIMITS.items()},
                    'activation_available': False, 'activation_block': ACTIVATION_BLOCK,
                    'required_confirmation': REMOVAL_CONFIRMATION, 'draft_only': True}

    def save(self, body):
        if type(body) is not dict or set(body) != {'profile', 'joints', 'revision'}:
            raise ValueError('invalid draft request')
        validated = validate_profile(body['profile'], body['joints'])
        with self.lock:
            if type(body['revision']) is not int or body['revision'] != self.revision:
                raise ValueError('profile changed; reload before saving')
            profiles = deepcopy(self.profiles)
            profiles[body['profile']] = validated
            write_private_json(self.path, {'revision': self.revision + 1, 'profiles': profiles})
            self.profiles = profiles
            self.revision += 1
            return self.snapshot()
