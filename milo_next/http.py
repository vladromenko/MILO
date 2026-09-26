import secrets
from aiohttp import web


def authentication(token):
    @web.middleware
    async def authenticate(request, handler):
        supplied = request.headers.get("Authorization", "")
        if not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
            raise web.HTTPUnauthorized()
        try:
            return await handler(request)
        except (ValueError, KeyError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
    return authenticate
