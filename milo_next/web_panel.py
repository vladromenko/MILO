"""Offline operator UI. Browser credentials never grant direct actuator/SSH access."""
import asyncio
from collections import deque
import json
import secrets
import time
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from .diagnostics import Diagnostics
from .arm_profiles import ArmProfiles
from .lifecycle import Lifecycle
from .settings import ROOT, settings, write_private_json, service_prefix

COOKIE = 'milo_operator'
SESSION = web.RequestKey('milo_session', dict)


async def read_bounded(response, limit):
    result = bytearray()
    async for chunk in response.content.iter_chunked(16384):
        result.extend(chunk)
        if len(result) > limit:
            raise ValueError('runtime response too large')
    return bytes(result)


def access_code(path=None):
    path = path or ROOT / 'config/web_access.json'
    if not path.exists():
        write_private_json(path, {'code': secrets.token_urlsafe(24)})
    return json.loads(path.read_text())['code']


class Panel:
    def __init__(self, cfg, code, profile_path=None):
        self.cfg, self.code = cfg, code
        self.sessions = {}
        self.attempts = deque(maxlen=30)
        self.diagnostics = Diagnostics()
        self.preview_lock = asyncio.Lock()
        self.preview_at = 0
        self.preview_frame = None
        self.profile_path = profile_path or ROOT / 'config/arm_profile_drafts.json'
        self.profile_lock = asyncio.Lock()
        self.lifecycle = Lifecycle(service_prefix(cfg))

    async def runtime(self, request):
        try:
            if request.method == 'GET':
                return web.json_response(await self.lifecycle.snapshot())
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'action'} or body['action'] not in {'start', 'stop'}:
                raise web.HTTPBadRequest(text='Only start and stop are supported')
            return web.json_response(await self.lifecycle.control(body['action']), status=202)
        except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    async def arm_profiles(self, request):
        try:
            body = await request.json() if request.method == 'POST' else None
            async with self.profile_lock:
                # Serialize operators and re-read before revision comparison.
                profiles = ArmProfiles(self.profile_path)
                if request.method == 'GET':
                    return web.json_response(profiles.snapshot())
                return web.json_response(profiles.save(body))
        except (ValueError, TypeError, KeyError) as exc:
            raise web.HTTPConflict(text=str(exc)) from exc

    async def start(self, app):
        self.http = aiohttp.ClientSession(headers={'Authorization': 'Bearer ' + self.cfg['token']},
            timeout=aiohttp.ClientTimeout(total=6))

    async def close(self, app):
        await self.http.close()

    @web.middleware
    async def guard(self, request, handler):
        host = request.headers.get('Host', '')
        hostname = urlsplit('http://' + host).hostname
        if hostname not in {'milo.local', 'localhost', '127.0.0.1', '10.42.0.1', '10.43.0.1'}:
            raise web.HTTPForbidden(text='host not allowed')
        if request.method not in {'GET', 'HEAD'}:
            if request.headers.get('Origin') not in {None, 'http://' + host}:
                raise web.HTTPForbidden(text='origin not allowed')
            if request.content_type != 'application/json':
                raise web.HTTPUnsupportedMediaType()
        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
        if request.path.startswith('/api/'):
            session = self.sessions.get(request.cookies.get(COOKIE))
            if session is None:
                raise web.HTTPUnauthorized(text='sign in required')
            if request.method != 'GET' and not secrets.compare_digest(
                    request.headers.get('X-Milo-CSRF', ''), session['csrf']):
                raise web.HTTPForbidden(text='request token required')
            request[SESSION] = session
        try:
            response = await handler(request)
        except (ValueError, KeyError, TypeError) as exc:
            raise web.HTTPBadRequest(text='invalid request') from exc
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; img-src 'self' blob:; script-src 'self'; style-src 'self'; "
                                       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
        return response

    async def login(self, request):
        now = time.monotonic()
        while self.attempts and now - self.attempts[0] > 60:
            self.attempts.popleft()
        if len(self.attempts) >= 30:
            raise web.HTTPTooManyRequests(text='wait one minute')
        self.attempts.append(now)
        body = await request.json()
        if not isinstance(body.get('code'), str) or not secrets.compare_digest(body['code'], self.code):
            raise web.HTTPUnauthorized(text='incorrect access code')
        if len(self.sessions) >= 64:
            self.sessions.pop(next(iter(self.sessions)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[token] = {'csrf': csrf, 'expires': now + 8 * 3600}
        response = web.json_response({'csrf': csrf})
        response.set_cookie(COOKIE, token, httponly=True, samesite='Strict', max_age=8 * 3600)
        return response

    async def session(self, request):
        return web.json_response({'csrf': request[SESSION]['csrf']})

    async def logout(self, request):
        self.sessions.pop(request.cookies.get(COOKIE), None)
        response = web.json_response({'ok': True})
        response.del_cookie(COOKIE)
        return response

    async def upstream(self, method, path, body=None, params=None):
        async with self.http.request(method, 'http://127.0.0.1:8780' + path,
                                     json=body, params=params,
                                     timeout=aiohttp.ClientTimeout(total=130 if body and body.get('action') in {'home', 'move'} else 12 if body and body.get('action') == 'recover' else 6)) as response:
            payload = await read_bounded(response, 256 * 1024)
            return web.Response(body=payload, status=response.status,
                                content_type=response.content_type)

    async def status(self, request):
        try:
            return await self.upstream('GET', '/status')
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return web.json_response({'brain_online': False, 'resources': {
                'jetson': self.diagnostics.snapshot()}, 'error': 'brain unavailable'}, status=503)

    async def command(self, request):
        body = await request.json()
        if body.get('action') == 'joystick_start':
            if set(body) != {'action', 'posture'} or body['posture'] is not True:
                raise web.HTTPForbidden(text='explicit posture permission required')
        elif body.get('action') in {'joystick_update', 'joystick_stop'}:
            keys = {'action', 'session'}
            if body['action'] == 'joystick_update':
                keys |= {'sequence', 'joint', 'direction', 'speed'}
                if (type(body.get('joint')) is not str or body['joint'] not in {'J1','J2','J3','J4','J5'}
                        or type(body.get('sequence')) is not int or body['sequence'] < 0
                        or type(body.get('direction')) is not int or body['direction'] not in {-1,0,1}
                        or type(body.get('speed')) is not int or not 4 <= body['speed'] <= 8):
                    raise web.HTTPForbidden(text='invalid joystick input')
            if set(body) != keys or type(body.get('session')) is not str or len(body['session']) != 48:
                raise web.HTTPForbidden(text='invalid joystick session')
        elif body.get('action') == 'nudge':
            auxiliary = body.get('joint') in {'J2', 'J4', 'J5'}
            keys = {'action', 'joint', 'delta', 'posture'} if auxiliary else {'action', 'joint', 'delta'}
            if (set(body) != keys or body.get('joint') not in {'J1', 'J2', 'J3', 'J4', 'J5'}
                    or (auxiliary and body.get('posture') is not True)
                    or type(body.get('delta')) is not int or body['delta'] not in {-1, 1}):
                raise web.HTTPForbidden(text='bounded steps require explicit posture permission; J6 stays locked')
        elif body.get('action') == 'move':
            if (set(body) != {'action', 'joint', 'target', 'posture'}
                    or type(body.get('joint')) is not str
                    or body.get('joint') not in {'J1','J2','J3','J4','J5'}
                    or type(body.get('target')) is not int or body.get('posture') is not True):
                raise web.HTTPForbidden(text='explicit J1-J5 target and posture permission required')
        elif body.get('action') not in {'arm', 'disarm', 'estop', 'reset', 'recover', 'home', 'scan'} or set(body) != {'action'}:
            raise web.HTTPForbidden(text='unsupported actuator action')
        return await self.upstream('POST', '/command', body)

    async def preview(self, request):
        async with self.preview_lock:
            if time.monotonic() - self.preview_at > .25:
                self.preview_frame = None
                try:
                    async with self.http.get('http://127.0.0.1:8872/snapshot?preview=1',
                            timeout=aiohttp.ClientTimeout(total=2)) as response:
                        response.raise_for_status()
                        frame = await read_bounded(response, 512 * 1024)
                        if not frame.startswith(b'\xff\xd8') or len(frame) > 512 * 1024:
                            raise ValueError('invalid camera preview')
                        self.preview_frame = frame
                        self.preview_at = time.monotonic()
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    raise web.HTTPServiceUnavailable(text='live camera unavailable') from exc
            return web.Response(body=self.preview_frame, content_type='image/jpeg')

    async def preferences(self, request):
        return await self.upstream('POST', '/operator/settings', await request.json())

    async def memory(self, request):
        return await self.upstream(request.method, '/operator/memory',
            await request.json() if request.method == 'POST' else None,
            {'person': request.query['person']} if 'person' in request.query else None)

    async def logs(self, request):
        component = request.query.get('component', 'brain')
        level = request.query.get('level', 'all')
        if component not in {'brain', 'web', 'rpc', 'dds', 'llm', 'stt', 'launch', 'stop'} or level not in {'all', 'warning', 'error'}:
            raise web.HTTPBadRequest(text='unsupported log filter')
        args = ['journalctl', '-b', f'_SYSTEMD_USER_UNIT={service_prefix(self.cfg)}-{component}.service',
                '-n', '80', '--no-pager', '-o', 'cat']
        if level != 'all':
            args += ['-p', level]
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        try:
            output = await asyncio.wait_for(proc.stdout.read(32768), 3)
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
        lines = output.decode(errors='replace').replace(self.cfg['token'], '[redacted]')
        return web.json_response({'lines': lines})


def create_app(cfg, code=None, profile_path=None):
    panel = Panel(cfg, code or access_code(), profile_path)
    app = web.Application(middlewares=[panel.guard], client_max_size=16384)
    app.on_startup.append(panel.start)
    app.on_cleanup.append(panel.close)
    app.router.add_post('/login', panel.login)
    app.router.add_get('/api/session', panel.session)
    app.router.add_post('/api/logout', panel.logout)
    app.router.add_get('/api/status', panel.status)
    app.router.add_get('/api/runtime', panel.runtime)
    app.router.add_post('/api/runtime', panel.runtime)
    app.router.add_post('/api/control', panel.command)
    app.router.add_get('/api/preview', panel.preview)
    app.router.add_get('/api/arm-profiles', panel.arm_profiles)
    app.router.add_post('/api/arm-profiles', panel.arm_profiles)
    app.router.add_post('/api/settings', panel.preferences)
    app.router.add_get('/api/memory', panel.memory)
    app.router.add_post('/api/memory', panel.memory)
    app.router.add_get('/api/logs', panel.logs)

    async def index(request):
        return web.FileResponse(ROOT / 'web/index.html')

    app.router.add_get('/', index)
    app.router.add_static('/assets/', ROOT / 'web', show_index=False)
    return app


def main():
    web.run_app(create_app(settings()), host='127.0.0.1', port=8784, access_log=None)


if __name__ == '__main__':
    main()
