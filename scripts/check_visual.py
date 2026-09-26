"""Live visual question benchmark. No movement, playback or persistent images."""
import asyncio
import json
from pathlib import Path
import sys
import time

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from milo_next.settings import settings


async def main():
    headers = {'Authorization': 'Bearer ' + settings()['token']}
    async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=160)) as http:
        for action in ('clear', 'capture'):
            async with http.post('http://127.0.0.1:8872/visual', json={'action': action}) as response:
                response.raise_for_status()
        async def vision():
            async with http.post('http://127.0.0.1:8872/visual', json={'action': 'ask',
                    'question': 'Describe the visible person and their clothing. Is anyone holding a phone?'}) as response:
                response.raise_for_status()
                print('VISION', json.dumps(await response.json(), ensure_ascii=False), flush=True)
        task = asyncio.create_task(vision())
        await asyncio.sleep(2)
        started = time.monotonic()
        async with http.post('http://127.0.0.1:8781/v1/chat/completions', json={
                'messages': [{'role': 'user', 'content': 'Say one supportive sentence to someone having a hard day.'}],
                'max_tokens': 50, 'stream': False,
                'chat_template_kwargs': {'enable_thinking': False}}) as response:
            response.raise_for_status()
            answer = (await response.json())['choices'][0]['message']['content']
            print('DIALOGUE_DURING_VISION', json.dumps({'seconds': round(time.monotonic()-started, 2),
                'answer': answer}, ensure_ascii=False), flush=True)
        await task


if __name__ == '__main__':
    asyncio.run(main())
