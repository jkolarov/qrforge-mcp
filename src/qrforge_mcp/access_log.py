"""The access log of the prod apps: one JSON line per request on stdout, in the format every app behind the prod
tunnel shares (access-log/ in jkolarov/deploy), so one Grafana dashboard reads them all.

This file is copied as it is into each Python app; keep the copies the same (the reference copy is in jkolarov/deploy).

    WSGI (Flask under gunicorn):  app.wsgi_app = WSGIAccessLog(app.wsgi_app, "tresbien")
    ASGI (uvicorn, MCP servers):  app = ASGIAccessLog(app, "advance-mcp")

Turn the server's own access log off next to it (gunicorn: no --access-logfile; uvicorn: access_log=False), or every
request is written twice. An app can name the signed-in user for its line: environ["access_log.user"] (WSGI) or
scope["access_log.user"] (ASGI).
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

USER_KEY = "access_log.user"
# A secret in a path, wherever it is: an MCP link (/mcp-<token>, /mcp/<token>), a password reset link.
_SECRET = re.compile(r"/(mcp-|mcp/|reset-password/)[^/?#]+")
_lock = threading.Lock()


def clean_path(path: str) -> str:
    """The path as the log keeps it: no query string, no secret, at most 300 characters."""
    path = path.split("?", 1)[0].split("#", 1)[0]
    return _SECRET.sub(lambda m: f"/{m.group(1)}redacted", path)[:300]


def clean_referer(referer: str) -> str:
    """The page that linked here, cleaned the same way: scheme, host and path only."""
    try:
        parts = urlsplit(referer)
    except ValueError:
        return ""
    if parts.scheme and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}{clean_path(parts.path)}"[:300]
    return clean_path(referer)


def client_ip(cf_connecting_ip: str, forwarded_for: str, peer: str) -> str:
    """The visitor's address: Cloudflare's header, else the first X-Forwarded-For hop, else the socket's peer."""
    if cf_connecting_ip.strip():
        return cf_connecting_ip.strip()
    first = forwarded_for.split(",")[0].strip()
    return first or peer


def entry(app: str, *, host: str, ip: str, country: str, method: str, path: str, status: int, size: int,
          seconds: float, ua: str, referer: str, user: str = "") -> dict:
    return {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": "access",
        "app": app,
        "host": host[:200],
        "ip": ip[:64],
        "country": country[:8],
        "method": method[:16],
        "path": clean_path(path),
        "status": int(status),
        "bytes": int(size),
        "duration": round(seconds, 3),
        "ua": ua[:300],
        "referer": clean_referer(referer),
        "user": str(user)[:64],
    }


def write(record: dict, out=None) -> None:
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    out = out or sys.stdout
    with _lock:                       # one write per line: lines from several threads never mix
        out.write(line)
        out.flush()


class WSGIAccessLog:
    """WSGI middleware: logs each request once its response has been sent (or has failed)."""

    def __init__(self, app, name: str, out=None):
        self.app, self.name, self.out = app, name, out

    def __call__(self, environ, start_response):
        started = time.monotonic()
        state = {"status": 0, "bytes": 0}

        def recording_start_response(status, headers, exc_info=None):
            state["status"] = int(str(status).split(" ", 1)[0])
            write_body = start_response(status, headers, exc_info)

            def counting_write(data):
                state["bytes"] += len(data)
                return write_body(data)
            return counting_write

        def done():
            self._log(environ, state, time.monotonic() - started)

        try:
            body = self.app(environ, recording_start_response)
        except BaseException:
            state["status"] = state["status"] or 500
            done()
            raise
        return _Body(body, state, done)

    def _log(self, environ, state, seconds):
        try:
            get = environ.get
            write(entry(
                self.name, host=get("HTTP_HOST", ""),
                ip=client_ip(get("HTTP_CF_CONNECTING_IP", ""), get("HTTP_X_FORWARDED_FOR", ""), get("REMOTE_ADDR", "")),
                country=get("HTTP_CF_IPCOUNTRY", ""), method=get("REQUEST_METHOD", ""),
                path=get("RAW_URI") or get("REQUEST_URI") or (get("SCRIPT_NAME", "") + get("PATH_INFO", "")),
                status=state["status"], size=state["bytes"], seconds=seconds,
                ua=get("HTTP_USER_AGENT", ""), referer=get("HTTP_REFERER", ""), user=get(USER_KEY, "") or ""),
                self.out)
        except Exception:             # the log must never break a request
            pass


class _Body:
    """The response body, passed through: counts its bytes and logs the request when the server closes it."""

    def __init__(self, body, state, done):
        self.body, self.state, self.done = body, state, done

    def __iter__(self):
        for chunk in self.body:
            self.state["bytes"] += len(chunk)
            yield chunk

    def close(self):
        try:
            close = getattr(self.body, "close", None)
            if close is not None:
                close()
        finally:
            self.done()


class ASGIAccessLog:
    """ASGI middleware: logs each HTTP request when the application is done with it. Other scopes pass through."""

    def __init__(self, app, name: str, out=None):
        self.app, self.name, self.out = app, name, out

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        started = time.monotonic()
        state = {"status": 0, "bytes": 0}

        async def recording_send(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
            elif message["type"] == "http.response.body":
                state["bytes"] += len(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive, recording_send)
        except BaseException:
            state["status"] = state["status"] or 500
            raise
        finally:
            self._log(scope, state, time.monotonic() - started)

    def _log(self, scope, state, seconds):
        try:
            headers = {}
            for key, value in scope.get("headers") or []:
                headers.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))
            raw = scope.get("raw_path")
            path = raw.decode("latin-1") if raw else scope.get("path", "")
            peer = (scope.get("client") or ("",))[0] or ""
            write(entry(
                self.name, host=headers.get("host", ""),
                ip=client_ip(headers.get("cf-connecting-ip", ""), headers.get("x-forwarded-for", ""), peer),
                country=headers.get("cf-ipcountry", ""), method=scope.get("method", ""), path=path,
                status=state["status"], size=state["bytes"], seconds=seconds,
                ua=headers.get("user-agent", ""), referer=headers.get("referer", ""),
                user=scope.get(USER_KEY, "") or ""),
                self.out)
        except Exception:             # the log must never break a request
            pass
