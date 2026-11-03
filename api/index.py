"""Vercel entrypoint for the Flask application."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server import app as flask_app


def _vercel_wsgi(environ, start_response):
    """Remove Vercel's function path before Flask route matching."""
    path_info = environ.get("PATH_INFO", "")
    if path_info == "/api/index":
        environ["PATH_INFO"] = "/"
    elif path_info.startswith("/api/index/"):
        environ["PATH_INFO"] = path_info[len("/api/index"):]
    return flask_app(environ, start_response)


app = _vercel_wsgi
