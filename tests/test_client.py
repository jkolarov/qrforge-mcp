"""Unit tests for the REST client helpers (token resolution, style, file load)."""
import base64

import pytest
from fastmcp.exceptions import ToolError

from qrforge_mcp import client


def test_api_base_default(monkeypatch):
    monkeypatch.delenv("QRFORGE_API_URL", raising=False)
    assert client.api_base() == "https://qrforge.work/api/v1"


def test_api_base_override(monkeypatch):
    monkeypatch.setenv("QRFORGE_API_URL", "http://localhost:5000/")
    assert client.api_base() == "http://localhost:5000/api/v1"


def test_resolve_token_prefers_header(monkeypatch):
    monkeypatch.setenv("QRFORGE_API_TOKEN", "env-token")
    monkeypatch.setattr(client, "get_http_headers",
                        lambda **_: {"authorization": "Bearer hdr-token"})
    assert client.resolve_token() == "hdr-token"


def test_resolve_token_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("QRFORGE_API_TOKEN", "env-token")
    monkeypatch.setattr(client, "get_http_headers", lambda **_: {})
    assert client.resolve_token() == "env-token"


def test_resolve_token_missing_raises(monkeypatch):
    monkeypatch.delenv("QRFORGE_API_TOKEN", raising=False)
    monkeypatch.setattr(client, "get_http_headers", lambda **_: {})
    with pytest.raises(ToolError):
        client.resolve_token()


def test_clean_style_filters_unknown_and_null():
    style = {"module_drawer": "circle", "fill_color": None, "bogus": "x"}
    assert client.clean_style(style) == {"module_drawer": "circle"}
    assert client.clean_style(None) == {}


def test_load_file_base64_and_limit():
    name, content = client.load_file(file_base64=base64.b64encode(b"hello").decode(),
                                     filename="a.txt")
    assert name == "a.txt" and content == b"hello"


def test_load_file_requires_a_source():
    with pytest.raises(ToolError):
        client.load_file()
