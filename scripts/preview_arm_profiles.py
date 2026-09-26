"""Local UI preview, no connection to Jetson/Pi. Never use real runtime credentials."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from milo_next.web_panel import create_app


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'artifacts/preview-profile-drafts.json'
    app = create_app({'token': 'LOCAL-PREVIEW-NOT-A-RUNTIME-TOKEN'}, 'profile-preview', path)
    web.run_app(app, host='127.0.0.1', port=8794, access_log=None)
