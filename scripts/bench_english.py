"""Repeatable English LLM benchmark; synthetic prompts, no sound or motion."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import statistics
import sys
import time

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from milo_next.brain import SYSTEM
from milo_next.settings import settings

CASES = [
    ('support', 'I spent weeks preparing for an interview and did not get the job. I feel useless.'),
    ('celebrate', 'I finally finished my first five kilometre run without stopping!'),
    ('curiosity', 'Why does music sometimes give people goosebumps?'),
    ('play', 'I have five minutes before a meeting. Give me a small, unusual imagination game.'),
    ('practical', 'I keep procrastinating on an email that only takes ten minutes. Help me get started.'),
    ('nuance', 'My friend got the promotion I wanted. I am happy for her but also jealous.'),
]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--label', required=True)
    args = parser.parse_args()
    headers = {'Authorization': 'Bearer ' + settings()['token'], 'Connection': 'close'}
    results = []
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as http:
        for _ in range(60):
            try:
                async with http.get('http://127.0.0.1:8781/health') as response:
                    if response.status == 200:
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(1)
        for name, question in [('warmup', 'Hello, Milo.'), *CASES]:
            payload = {'messages': [{'role': 'system', 'content': SYSTEM + ' Respond only in English.'},
                {'role': 'user', 'content': '<memory>\n\n</memory>\n<observations>'
                 '{"people":1,"objects":[],"social":{"facial_emotion":"unknown"}}'
                 '</observations>\nCurrent request: ' + question}],
                'stream': True, 'max_tokens': 180, 'temperature': .6, 'seed': 42,
                'chat_template_kwargs': {'enable_thinking': False}}
            start = time.monotonic()
            first = sentence = None
            text = ''
            async with http.post('http://127.0.0.1:8781/v1/chat/completions', json=payload) as response:
                response.raise_for_status()
                async for line in response.content:
                    if not line.startswith(b'data: ') or line.strip() == b'data: [DONE]':
                        continue
                    data = json.loads(line[6:])
                    choices = data.get('choices', [])
                    part = choices[0].get('delta', {}).get('content', '') if choices else ''
                    if not part:
                        continue
                    text += part
                    first = first if first is not None else time.monotonic()-start
                    if sentence is None and re.search(r'[.!?](?:\s|$)', text):
                        sentence = time.monotonic()-start
            row = {'case': name, 'ttft_s': round(first or 0, 3),
                   'first_sentence_s': round(sentence or 0, 3),
                   'complete_s': round(time.monotonic()-start, 3), 'answer': text}
            print(json.dumps(row, ensure_ascii=False), flush=True)
            if name != 'warmup':
                results.append(row)
    report = {'label': args.label, 'cases': results,
              'median': {key: round(statistics.median(row[key] for row in results), 3)
                         for key in ('ttft_s', 'first_sentence_s', 'complete_s')}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report['median']), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
