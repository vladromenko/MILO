"""Temporal social context; explicit reports take priority over uncertain cues."""
import time


class Behavior:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.last_seen = 0
        self.topic = ''
        self.social = {'engagement': None, 'visible_expression': 'unknown', 'valence': 'unknown',
                       'confidence': 0.0, 'trend': 'unknown', 'source': 'none'}

    def observe(self, faces):
        if len(faces) != 1:
            self.social = {**self.social, 'engagement': None, 'visible_expression': 'unknown',
                           'valence': 'unknown', 'confidence': 0.0, 'trend': 'unknown', 'source': 'none'}
            return
        self.last_seen = self.clock()
        face = faces[0]
        cue = face.get('expression', {})
        label = cue.get('label', 'unknown')
        old = self.social['visible_expression']
        valence = ('positive' if label == 'happy' else 'possibly_negative'
                   if label in {'sad', 'angry', 'fearful', 'disgust'} else 'unknown')
        self.social = {'engagement': face.get('engaged'), 'visible_expression': label,
                       'valence': valence, 'confidence': cue.get('model_score', 0),
                       'trend': 'stable' if old == label else 'changed',
                       'source': 'uncertain_visual_cue'}

    def snapshot(self, mode, person, audio, busy):
        state = ('sleeping' if mode == 'sleep' else 'speaking' if audio.get('speaking') else
                 'thinking' if busy else 'listening' if audio.get('state') == 'listening' else 'idle')
        return {'mode': mode, 'state': state, 'active_person': person,
                'topic': self.topic, 'social': dict(self.social),
                'speaking': bool(audio.get('speaking')), 'speaker_identity': 'uncertain' if not person else 'face-associated'}
