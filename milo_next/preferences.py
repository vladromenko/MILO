"""Small operator settings shared by the phone panel and runtime."""
import json
import math
from .settings import write_private_json
from .languages import LANGUAGES

DEFAULTS = {'mode': 'active', 'proactivity': 'normal', 'speaker_volume': 40,
            'muted': False, 'tts_volume': 1.0, 'mic_gain': 100, 'speech_rate': 1.0,
            'voice': 'en_US-ryan-low', 'language': 'en'}


def validate(values):
    if type(values) is not dict or set(values) - set(DEFAULTS):
        raise ValueError('unknown settings')
    result = dict(values)
    choices = {'mode': {'active', 'quiet', 'sleep'}, 'proactivity': {'quiet', 'normal', 'social'},
               'voice': {voice for _, voice in LANGUAGES.values()}, 'language': set(LANGUAGES)}
    for name, options in choices.items():
        if name in result and (type(result[name]) is not str or result[name] not in options):
            raise ValueError(f'{name}: unsupported option')
    for name, low, high in (('speaker_volume', 0, 100), ('mic_gain', 0, 100),
                            ('tts_volume', 0, 1), ('speech_rate', .7, 1.4)):
        if name in result:
            value = result[name]
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{name}: outside {low}..{high}')
    if 'muted' in result and type(result['muted']) is not bool:
        raise ValueError('muted must be boolean')
    if 'language' in result:
        result['voice'] = LANGUAGES[result['language']][1]
    return result


class Preferences:
    def __init__(self, path):
        self.path = path
        self.values = dict(DEFAULTS)
        if path.exists():
            self.values.update(validate(json.loads(path.read_text())))

    def update(self, values):
        merged = {**self.values, **validate(values)}
        write_private_json(self.path, merged)
        self.values = merged
        return dict(merged)
