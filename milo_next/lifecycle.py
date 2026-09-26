"""Phone start/stop jobs survive browser disconnects; never expose shell input."""
import asyncio


class Lifecycle:
    def __init__(self, prefix):
        self.prefix = prefix
        self.lock = asyncio.Lock()

    async def systemctl(self, *args):
        process = await asyncio.create_subprocess_exec(
            'systemctl', '--user', *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 5)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        if process.returncode:
            raise RuntimeError('Service manager unavailable; check installed MILO services')
        return output.decode()

    async def snapshot(self):
        names = [self.prefix + '.target', self.prefix + '-launch.service',
                 self.prefix + '-stop.service']
        raw = await self.systemctl('show', *names, '--property=Id,LoadState,ActiveState,Result')
        units = {}
        for block in raw.strip().split('\n\n'):
            values = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            units[values.get('Id')] = values
        if any(units.get(name, {}).get('LoadState') != 'loaded' for name in names):
            raise RuntimeError('Install the MILO phone startup services first')
        target, launch, stop = (units[name] for name in names)
        busy = {'active', 'activating', 'deactivating', 'reloading'}
        if stop['ActiveState'] in busy:
            state = 'stopping'
        elif launch['ActiveState'] in busy:
            state = 'starting'
        elif stop['ActiveState'] == 'failed':
            state = 'stop_failed'
        elif launch['ActiveState'] == 'failed':
            state = 'start_failed'
        else:
            state = 'running' if target['ActiveState'] == 'active' else 'stopped'
        return {'state': state, 'target_active': target['ActiveState'] == 'active',
                'start_result': launch.get('Result'), 'stop_result': stop.get('Result'),
                'failed_units': [name for name in names[1:] if units[name]['ActiveState'] == 'failed']}

    async def control(self, action):
        if action not in {'start', 'stop'}:
            raise ValueError('Unsupported lifecycle action')
        async with self.lock:
            current = await self.snapshot()
            if action == 'start' and current['state'] in {'starting', 'running', 'stopping'}:
                return current
            if action == 'stop' and current['state'] == 'stopping':
                return current
            if current['failed_units']:
                await self.systemctl('reset-failed', *current['failed_units'])
            unit = self.prefix + ('-launch.service' if action == 'start' else '-stop.service')
            await self.systemctl('--no-block', 'start', unit)
            return {'state': 'starting' if action == 'start' else 'stopping'}
