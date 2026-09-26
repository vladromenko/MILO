"""Operator controls for the already selected USB devices, never ALSA defaults."""
import re
import subprocess
from pathlib import Path
from .languages import LANGUAGES
from .preferences import DEFAULTS, validate

AUDIO_KEYS = set(DEFAULTS) - {'mode', 'proactivity'}


def mixer_command(device, control, value):
    match = re.search(r'\(hw:(\d+),\d+\)', device or '')
    if not match:
        raise ValueError('selected USB sound card is unavailable')
    return ['amixer', '-q', '-c', match[1], 'sset', control, f'{round(value)}%']


class AudioControls:
    def __init__(self, audio):
        self.audio = audio
        self.values = {k: v for k, v in DEFAULTS.items() if k in AUDIO_KEYS}

    def apply(self, values):
        values = validate(values)
        if set(values) - AUDIO_KEYS:
            raise ValueError('not an audio setting')
        merged = {**self.values, **values}
        status = self.audio.status
        if 'language' in values and hasattr(self.audio, 'piper_model'):
            path = Path(self.audio.piper_model).parent / (LANGUAGES[merged['language']][1] + '.onnx')
            if not path.is_file() or not Path(str(path) + '.json').is_file():
                raise ValueError('voice model is not installed')
        if {'speaker_volume', 'muted'} & values.keys():
            subprocess.run(mixer_command(status.output_device, 'PCM',
                0 if merged['muted'] else merged['speaker_volume']), check=True,
                timeout=3, capture_output=True)
        if 'mic_gain' in values:
            subprocess.run(mixer_command(status.input_device, 'Mic', merged['mic_gain']),
                           check=True, timeout=3, capture_output=True)
        self.audio.synthesis_settings = {
            'length_scale': 1 / merged['speech_rate'], 'volume': merged['tts_volume']}
        self.audio.language = merged['language']
        self.values = merged
        return dict(merged)
