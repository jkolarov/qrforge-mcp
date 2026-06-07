"""Tool-level tests: drive the real MCP tools via an in-memory FastMCP client,
with the QR Forge REST API mocked by respx. Verifies request shaping (incl. the
top-level-vs-nested style quirk), auth header forwarding, and error surfacing.
"""
import base64

import httpx
import pytest
import respx
from fastmcp import Client

from qrforge_mcp.server import mcp

API = "https://qrforge.work/api/v1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("QRFORGE_API_TOKEN", "test-token")
    monkeypatch.setenv("QRFORGE_API_URL", "https://qrforge.work")


async def _call(name, args=None):
    async with Client(mcp) as c:
        return await c.call_tool(name, args or {})


@respx.mock
async def test_whoami_forwards_bearer():
    route = respx.get(f"{API}/me").mock(
        return_value=httpx.Response(200, json={"email": "a@b.com", "qrcode_count": 3}))
    res = await _call("whoami")
    assert route.called
    assert route.calls[0].request.headers["authorization"] == "Bearer test-token"
    assert res.data["email"] == "a@b.com"


@respx.mock
async def test_list_qrcodes_sends_filters():
    route = respx.get(f"{API}/qrcodes").mock(
        return_value=httpx.Response(200, json={"data": [], "pagination": {"total": 0}}))
    await _call("list_qrcodes", {"q": "menu", "active": True, "page": 2, "limit": 50})
    req = route.calls[0].request
    assert dict(req.url.params) == {"page": "2", "limit": "50", "q": "menu", "active": "true"}


@respx.mock
async def test_create_url_puts_style_at_top_level():
    route = respx.post(f"{API}/qrcodes").mock(
        return_value=httpx.Response(201, json={"id": 1, "type": "url"}))
    respx.get(url__regex=r".+/qr\.png").mock(return_value=httpx.Response(200, content=b"\x89PNG\r\n"))
    await _call("create_url_qrcode", {
        "target_url": "https://example.com", "name": "X", "static": True,
        "style": {"module_drawer": "circle", "fill_color": "#ff0000"},
    })
    import json as _json
    sent = _json.loads(route.calls[0].request.content)
    assert sent["type"] == "url"
    assert sent["target_url"] == "https://example.com"
    assert sent["static"] is True
    # style fields are TOP-LEVEL on create
    assert sent["module_drawer"] == "circle"
    assert sent["fill_color"] == "#ff0000"
    assert "style" not in sent


@respx.mock
async def test_update_nests_style():
    route = respx.patch(f"{API}/qrcodes/7").mock(
        return_value=httpx.Response(200, json={"id": 7}))
    await _call("update_qrcode", {"id": 7, "name": "New",
                                  "style": {"fill_color": "#00ff00"}})
    import json as _json
    sent = _json.loads(route.calls[0].request.content)
    assert sent["name"] == "New"
    # style is NESTED on PATCH
    assert sent["style"] == {"fill_color": "#00ff00"}
    assert "fill_color" not in sent


@respx.mock
async def test_create_wifi_body():
    route = respx.post(f"{API}/qrcodes").mock(
        return_value=httpx.Response(201, json={"id": 2, "type": "wifi"}))
    respx.get(url__regex=r".+/qr\.png").mock(return_value=httpx.Response(200, content=b"\x89PNG\r\n"))
    await _call("create_wifi_qrcode", {"ssid": "Net", "auth": "WPA", "password": "p"})
    import json as _json
    sent = _json.loads(route.calls[0].request.content)
    assert sent == {"type": "wifi", "ssid": "Net", "auth": "WPA",
                    "password": "p", "hidden": False}


@respx.mock
async def test_create_file_qrcode_multipart():
    route = respx.post(f"{API}/qrcodes").mock(
        return_value=httpx.Response(201, json={"id": 3, "type": "pdf"}))
    respx.get(url__regex=r".+/qr\.png").mock(return_value=httpx.Response(200, content=b"\x89PNG\r\n"))
    await _call("create_file_qrcode", {
        "file_base64": base64.b64encode(b"PK\x03\x04").decode(),
        "filename": "sheet.xlsx", "name": "Sheet",
    })
    req = route.calls[0].request
    assert req.headers["content-type"].startswith("multipart/form-data")
    body = req.content
    assert b"sheet.xlsx" in body and b'name="pdf_file"' in body and b"pdf" in body


@respx.mock
async def test_create_returns_inline_image_and_clean_metadata():
    respx.post(f"{API}/qrcodes").mock(return_value=httpx.Response(201, json={
        "id": 9, "type": "url", "public_url": "https://qrforge.work/d/abc",
        "links": {"qr_png": "/api/v1/qrcodes/9/qr.png"},
        "target": {"kind": "url", "url": "https://x.io", "download_url": "/api/v1/qrcodes/9/file"},
    }))
    respx.get(url__regex=r".+/qr\.png").mock(
        return_value=httpx.Response(200, content=b"\x89PNG\r\nDATA"))
    res = await _call("create_url_qrcode", {"target_url": "https://x.io"})
    # an image content block is returned for the client to display
    assert any(getattr(b, "type", "") == "image" for b in res.content)
    # structured metadata is clean: no auth-gated api links surfaced
    assert res.data["id"] == 9 and res.data["public_url"] == "https://qrforge.work/d/abc"
    assert "links" not in res.data
    assert "download_url" not in res.data["target"]


@respx.mock
async def test_render_svg_returns_text():
    respx.post(f"{API}/qr/render").mock(
        return_value=httpx.Response(200, text="<svg>ok</svg>",
                                    headers={"content-type": "image/svg+xml"}))
    res = await _call("render_qr", {"url": "https://x.io", "format": "svg"})
    text = res.data if isinstance(res.data, str) else res.content[0].text
    assert "<svg>" in text


@respx.mock
async def test_api_error_becomes_tool_error():
    respx.post(f"{API}/qrcodes").mock(return_value=httpx.Response(
        400, json={"error": "Missing or invalid required field: target_url",
                   "code": "invalid_target_url"}))
    with pytest.raises(Exception) as exc:
        await _call("create_url_qrcode", {"target_url": "not a url"})
    assert "invalid_target_url" in str(exc.value)
