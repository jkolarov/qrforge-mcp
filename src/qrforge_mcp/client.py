"""Thin REST client for the QR Forge API, shared by all MCP tools.

The MCP server never touches QR Forge's database — it only calls the public REST
API (``/api/v1``) with the user's API token. This module resolves the token and
base URL, performs requests, and normalises the API's error envelope into MCP
``ToolError``s.

Token resolution (in order):
  1. Hosted (HTTP) mode: the caller's ``Authorization: Bearer <token>`` header,
     read via ``fastmcp.server.dependencies.get_http_headers`` and forwarded.
  2. Local (stdio) mode: the ``QRFORGE_API_TOKEN`` environment variable.

Base URL: ``QRFORGE_API_URL`` (default ``https://qrforge.work``).
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

DEFAULT_BASE_URL = "https://qrforge.work"
MAX_FILE_BYTES = 50 * 1024 * 1024  # mirrors the API's 50 MB upload limit
TIMEOUT = httpx.Timeout(120.0, connect=15.0)


def api_base() -> str:
    base = os.environ.get("QRFORGE_API_URL", DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/api/v1"


def resolve_token() -> str:
    """Return the API token from the request header (hosted) or env (local)."""
    headers: dict[str, str] = {}
    try:
        # `authorization` is excluded from get_http_headers() by default; opt it in
        # so the hosted server can forward the caller's token to the API.
        headers = get_http_headers(include={"authorization"}) or {}
    except Exception:
        headers = {}
    # Header keys are normalised to lowercase by Starlette, but be defensive.
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token

    token = (os.environ.get("QRFORGE_API_TOKEN") or "").strip()
    if token:
        return token

    raise ToolError(
        "No QR Forge API token found. For local use, set the QRFORGE_API_TOKEN "
        "environment variable. For the hosted server, send "
        "'Authorization: Bearer <token>'. Create a token at "
        "https://qrforge.work/api/keys."
    )


def request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    data: dict | None = None,
    files: dict | None = None,
    expect: str = "json",
) -> Any:
    """Call the QR Forge API. ``expect`` is 'json', 'bytes', or 'text'.

    Raises ToolError on transport failures and on any 4xx/5xx, surfacing the
    API's ``{"error": ..., "code": ...}`` envelope.
    """
    url = f"{api_base()}{path}"
    headers = {"Authorization": f"Bearer {resolve_token()}"}
    # Strip None values so optional fields don't get sent as literal nulls.
    if json is not None:
        json = {k: v for k, v in json.items() if v is not None}
    if data is not None:
        data = {k: v for k, v in data.items() if v is not None}

    try:
        resp = httpx.request(
            method, url, params=params, json=json, data=data, files=files,
            headers=headers, timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"Could not reach the QR Forge API at {url}: {exc}") from exc

    if resp.status_code >= 400:
        code = msg = None
        try:
            body = resp.json()
            code, msg = body.get("code"), body.get("error")
        except Exception:
            msg = (resp.text or "")[:300]
        suffix = f"/{code}" if code else ""
        raise ToolError(
            f"QR Forge API error ({resp.status_code}{suffix}): {msg or 'request failed'}"
        )

    if expect == "bytes":
        return resp.content
    if expect == "text":
        return resp.text
    if not resp.content:
        return {}
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def load_file(
    *,
    file_path: str | None = None,
    file_url: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
) -> tuple[str, bytes]:
    """Resolve file content from a local path, a URL, or base64 (in that order).

    Returns ``(filename, content_bytes)``. Enforces the 50 MB API limit.
    """
    if file_path:
        path = Path(file_path).expanduser()
        if not path.is_file():
            raise ToolError(f"File not found: {file_path}")
        content = path.read_bytes()
        name = filename or path.name
    elif file_url:
        try:
            resp = httpx.get(file_url, timeout=TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not download file_url: {exc}") from exc
        content = resp.content
        name = filename or os.path.basename(urlparse(file_url).path) or "upload"
    elif file_base64:
        try:
            content = base64.b64decode(file_base64, validate=True)
        except Exception as exc:
            raise ToolError(f"Invalid base64 in file_base64: {exc}") from exc
        name = filename or "upload"
    else:
        raise ToolError("Provide one of: file_path, file_url, or file_base64.")

    if len(content) > MAX_FILE_BYTES:
        raise ToolError(
            f"File is {len(content) // (1024 * 1024)} MB; the QR Forge limit is 50 MB."
        )
    return name, content


# Style fields accepted by the API (see _style_from / _sanitise_style server-side).
STYLE_KEYS = ("module_drawer", "fill_color", "back_color", "fill_type", "fill_color_2")


def clean_style(style: dict | None) -> dict:
    """Keep only recognised, non-null style keys."""
    if not style:
        return {}
    return {k: v for k, v in style.items() if k in STYLE_KEYS and v is not None}
