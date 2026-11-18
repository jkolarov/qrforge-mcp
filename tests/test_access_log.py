"""The shared access log (access-log/ in jkolarov/deploy): the same tests in every Python app that carries it."""
import asyncio
import io
import json

import pytest

from qrforge_mcp.access_log import ASGIAccessLog, WSGIAccessLog, clean_path, client_ip

FIELDS = ["time", "type", "app", "host", "ip", "country", "method", "path", "status", "bytes", "duration", "ua",
          "referer", "user"]


def lines(out):
    return [json.loads(line) for line in out.getvalue().splitlines()]


def wsgi_hello(environ, start_response):
    if environ.get("PATH_INFO") == "/boom":
        raise RuntimeError("boom")
    environ["access_log.user"] = "georgi"
    start_response("201 Created", [("Content-Type", "text/plain")])
    return [b"hello", b" world"]


def call_wsgi(app, **environ):
    base = {"REQUEST_METHOD": "GET", "PATH_INFO": "/", "SCRIPT_NAME": "", "REMOTE_ADDR": "172.18.0.1"}
    base.update(environ)
    body = app(base, lambda status, headers, exc_info=None: None)
    chunks = list(body)
    body.close()
    return b"".join(chunks)


def test_wsgi_line_has_every_field_with_the_visitor_from_cloudflare():
    out = io.StringIO()
    app = WSGIAccessLog(wsgi_hello, "tresbien", out)
    assert call_wsgi(app, RAW_URI="/numero/2026-11-19?f=todo", HTTP_HOST="tresbien.fluxory.dev",
                     HTTP_CF_CONNECTING_IP="80.13.4.67", HTTP_CF_IPCOUNTRY="FR",
                     HTTP_X_FORWARDED_FOR="1.2.3.4", HTTP_USER_AGENT="Safari",
                     HTTP_REFERER="https://tresbien.fluxory.dev/numero/2026-11-19?edit=3") == b"hello world"
    [line] = lines(out)
    assert list(line) == FIELDS
    assert line["type"] == "access" and line["app"] == "tresbien"
    assert line["ip"] == "80.13.4.67" and line["country"] == "FR"
    assert line["path"] == "/numero/2026-11-19"                  # no query string
    assert line["referer"] == "https://tresbien.fluxory.dev/numero/2026-11-19"
    assert line["status"] == 201 and line["bytes"] == 11 and line["user"] == "georgi"
    assert line["time"].endswith("+00:00")


def test_wsgi_without_cloudflare_falls_back_to_forwarded_for_then_the_peer():
    out = io.StringIO()
    app = WSGIAccessLog(wsgi_hello, "qrforge", out)
    call_wsgi(app, HTTP_X_FORWARDED_FOR="5.6.7.8, 10.0.0.1")
    call_wsgi(app)
    assert [line["ip"] for line in lines(out)] == ["5.6.7.8", "172.18.0.1"]
    assert lines(out)[1]["country"] == ""


def test_wsgi_a_failing_request_is_logged_as_500():
    out = io.StringIO()
    app = WSGIAccessLog(wsgi_hello, "qrforge", out)
    with pytest.raises(RuntimeError):
        call_wsgi(app, PATH_INFO="/boom")
    assert lines(out)[0]["status"] == 500


def test_secrets_in_paths_never_reach_the_log():
    assert clean_path("/mcp-s3cr3t") == "/mcp-redacted"
    assert clean_path("/mcp-s3cr3t/extra?x=1") == "/mcp-redacted/extra"
    assert clean_path("/mcp/s3cr3t") == "/mcp/redacted"
    assert clean_path("/.well-known/oauth-protected-resource/mcp-s3cr3t") == \
        "/.well-known/oauth-protected-resource/mcp-redacted"
    assert clean_path("/mcp") == "/mcp" and clean_path("/mcp/") == "/mcp/"
    assert clean_path("/reset-password/Ab1-xyz") == "/reset-password/redacted"
    assert client_ip(" 9.9.9.9 ", "1.1.1.1", "2.2.2.2") == "9.9.9.9"


def test_asgi_line_and_secret_path():
    out = io.StringIO()

    async def inner(scope, receive, send):
        scope["access_log.user"] = "advance"
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})

    app = ASGIAccessLog(inner, "advance-mcp", out)
    scope = {"type": "http", "method": "POST", "path": "/mcp-s3cr3t", "raw_path": b"/mcp-s3cr3t",
             "client": ("172.21.0.1", 50000),
             "headers": [(b"host", b"advance-mcp.fluxory.dev"), (b"cf-connecting-ip", b"160.79.106.160"),
                         (b"cf-ipcountry", b"US"), (b"user-agent", b"Claude-User")]}

    async def nothing():
        return {"type": "http.request"}

    async def drop(message):
        pass

    asyncio.run(app(scope, nothing, drop))
    [line] = lines(out)
    assert list(line) == FIELDS
    assert line["path"] == "/mcp-redacted" and "s3cr3t" not in out.getvalue()
    assert (line["ip"], line["country"], line["status"], line["bytes"], line["ua"], line["user"]) == \
        ("160.79.106.160", "US", 404, 9, "Claude-User", "advance")


def test_asgi_other_scopes_pass_through_unlogged():
    out = io.StringIO()
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    asyncio.run(ASGIAccessLog(inner, "qrforge-mcp", out)({"type": "lifespan"}, None, None))
    assert seen == ["lifespan"] and out.getvalue() == ""
