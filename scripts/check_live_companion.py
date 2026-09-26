"""Read-only live tracking/expression health sample; no images or speech retained."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from milo_next.settings import settings

parser = argparse.ArgumentParser()
parser.add_argument('--seconds', type=int, default=180)
args = parser.parse_args()
if not 5 <= args.seconds <= 600:
    parser.error('seconds must be 5..600')
request = urllib.request.Request('http://127.0.0.1:8780/status',
    headers={'Authorization':'Bearer ' + settings()['token']})
states, expressions, objects, errors = (Counter() for _ in range(4))
start = time.monotonic()
first = last = None
maximum_score = 0
maxima = {'camera_ms':0, 'edge_ms':0, 'feedback_ms':0}
while time.monotonic() - start < args.seconds:
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            status = json.load(response)
        arm, perception = status['arm'], status.get('perception', {})
        faces = perception.get('faces', [])
        j6 = arm['raw_pose'].get('J6')
        if first is None and type(j6) in (int, float):
            first = {'commands':arm['published_count'], 'j6':j6}
        states[arm['state']] += 1
        objects[perception.get('status', {}).get('objects', {}).get('state', 'missing')] += 1
        if status.get('edge_error'): errors['edge'] += 1
        if status.get('audio', {}).get('errors'): errors['audio'] += 1
        if arm['state'] == 'ESTOP': errors['estop'] += 1
        if type(j6) not in (int, float):
            errors['j6_feedback_missing'] += 1
        elif first and j6 != first['j6']:
            errors['j6_changed'] += 1
        cue = faces[0].get('expression', {}) if len(faces) == 1 else {}
        expressions[cue.get('label','no_single_face')] += 1
        maximum_score = max(maximum_score, cue.get('model_score',0))
        maxima['camera_ms'] = max(maxima['camera_ms'], perception.get('capture_age_ms') or 0)
        maxima['edge_ms'] = max(maxima['edge_ms'], status.get('edge_age_ms') or 0)
        maxima['feedback_ms'] = max(maxima['feedback_ms'], (arm.get('feedback_age_s') or 0) * 1000)
        last = {'commands':arm['published_count'], 'j6':arm['raw_pose'].get('J6'),
            'tracking_requested':status.get('tracking_requested'),
            'expression_reaction':status.get('metrics',{}).get('last_expression_reaction'),
            'reply_source':status.get('metrics',{}).get('reply_source')}
    except Exception as exc:
        errors[type(exc).__name__] += 1
    time.sleep(1)
print(json.dumps({'seconds':round(time.monotonic()-start,1), 'states':states,
    'objects':objects,'expressions':expressions,'max_expression_score':maximum_score,
    'errors':errors,'maxima':maxima,'first':first,'last':last}, indent=2))
