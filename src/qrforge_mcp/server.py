"""QR Forge MCP server: every QR Forge REST API capability as an MCP tool.

Tools are a thin mirror of ``/api/v1``. Authentication is the caller's QR Forge
API token (forwarded header when hosted, ``QRFORGE_API_TOKEN`` env when local).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import TextContent
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import client

mcp = FastMCP(
    name="QR Forge",
    instructions=(
        "Tools to create and manage trackable QR codes via the QR Forge API "
        "(https://qrforge.work). Authenticate with a QR Forge API token. Use "
        "list_qr_types and list_qr_styles to discover options. 'style' is an "
        "optional object with keys: module_drawer (square|rounded|circle|gapped|"
        "vertical_bars|horizontal_bars), fill_type (solid|radial|horizontal|"
        "vertical), fill_color, fill_color_2, back_color (hex like #000000)."
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Unauthenticated liveness probe for uptime monitors (HTTP transport only)."""
    return JSONResponse({"status": "ok"})

Style = Annotated[dict | None, Field(
    default=None,
    description="Optional QR style: module_drawer, fill_type, fill_color, "
                "fill_color_2, back_color.",
)]

# Fields surfaced to the user/model. We drop the raw API `links` and the
# `download_url` (auth-gated /api/v1 paths) so the model never hands the user a
# URL that 401s — image/file access goes through the dedicated tools instead.
_PRESENT_FIELDS = (
    "id", "name", "type", "public_id", "public_url", "is_active", "is_expired",
    "expires_at", "total_scans", "has_logo", "style", "target",
)


def present(doc: dict) -> dict:
    """Trim a QR document to user-facing fields (no internal API links)."""
    out = {k: doc.get(k) for k in _PRESENT_FIELDS if doc.get(k) is not None}
    target = out.get("target")
    if isinstance(target, dict):
        out["target"] = {k: v for k, v in target.items() if k != "download_url"}
    return out


def _render_png(doc_id: int) -> bytes | None:
    """Best-effort: render the saved QR as PNG. Never fails the calling tool."""
    try:
        return client.request("GET", f"/qrcodes/{doc_id}/qr.png",
                               params={"size": 10}, expect="bytes")
    except Exception:
        return None


def _qr_result(doc: dict, note: str) -> ToolResult:
    """Return the QR image inline (for the client to display) plus clean metadata
    the model can reason over. The image is also re-fetchable via get_qrcode_png."""
    summary = present(doc)
    blocks: list = []
    png = _render_png(doc["id"])
    if png is not None:
        blocks.append(Image(data=png, format="png").to_image_content())
    public = doc.get("public_url")
    text = (f"{note} QR code #{doc['id']} (type={doc.get('type')})."
            + (f" Encoded/public link: {public}." if public else "")
            + " The QR image is attached above; call get_qrcode_png(id="
            f"{doc['id']}) to fetch it again.")
    blocks.append(TextContent(type="text", text=text))
    return ToolResult(content=blocks, structured_content=summary)


# ── Identity & tokens ─────────────────────────────────────────────────────────

@mcp.tool
def whoami() -> dict:
    """Return the authenticated account's identity and QR-code count (GET /me)."""
    return client.request("GET", "/me")


@mcp.tool
def list_api_tokens() -> dict:
    """List the account's API tokens (secrets are never returned)."""
    return client.request("GET", "/tokens")


@mcp.tool
def create_api_token(name: str = "API Token") -> dict:
    """Create a new API token. The full secret is returned exactly once."""
    return client.request("POST", "/tokens", json={"name": name})


@mcp.tool
def revoke_api_token(token_id: int) -> dict:
    """Revoke (delete) an API token by id."""
    return client.request("DELETE", f"/tokens/{token_id}")


# ── Discovery ─────────────────────────────────────────────────────────────────

@mcp.tool
def list_qr_types() -> dict:
    """List available QR code types and their required fields (GET /qr/types)."""
    return client.request("GET", "/qr/types")


@mcp.tool
def list_qr_styles() -> dict:
    """List QR style presets and defaults (GET /qr/styles)."""
    return client.request("GET", "/qr/styles")


# ── Stateless render ──────────────────────────────────────────────────────────

@mcp.tool
def render_qr(url: str, format: str = "png", size: int = 12, style: Style = None) -> Any:
    """Render a styled QR code for an arbitrary URL without saving it.

    Returns a PNG image (format='png') or the SVG markup as text (format='svg').
    """
    body: dict[str, Any] = {"url": url, "format": format, "size": size}
    cleaned = client.clean_style(style)
    if cleaned:
        body["style"] = cleaned  # render nests style under "style"
    if format == "svg":
        return client.request("POST", "/qr/render", json=body, expect="text")
    png = client.request("POST", "/qr/render", json=body, expect="bytes")
    return Image(data=png, format="png")


# ── Create QR codes (per type) ────────────────────────────────────────────────

def _create(type_: str, fields: dict, style: dict | None) -> ToolResult:
    # POST /qrcodes reads style fields at the TOP LEVEL of the body.
    body = {"type": type_, **{k: v for k, v in fields.items() if v is not None}}
    body.update(client.clean_style(style))
    doc = client.request("POST", "/qrcodes", json=body)
    return _qr_result(doc, "Created")


@mcp.tool
def create_url_qrcode(
    target_url: str,
    name: str | None = None,
    static: bool = False,
    expires_in_days: int | None = None,
    style: Style = None,
) -> ToolResult:
    """Create a Website-link QR code. static=True encodes the URL directly
    (no redirect, no scan tracking, not editable); default is a tracked, editable
    dynamic link."""
    return _create("url", {
        "target_url": target_url, "name": name,
        "static": static, "expires_in_days": expires_in_days,
    }, style)


@mcp.tool
def create_whatsapp_qrcode(
    target_url: str,
    name: str | None = None,
    expires_in_days: int | None = None,
    style: Style = None,
) -> ToolResult:
    """Create a WhatsApp QR code from a wa.me link (tracked dynamic link)."""
    return _create("whatsapp", {
        "target_url": target_url, "name": name, "expires_in_days": expires_in_days,
    }, style)


@mcp.tool
def create_wifi_qrcode(
    ssid: str,
    auth: str = "WPA",
    password: str | None = None,
    hidden: bool = False,
    name: str | None = None,
    style: Style = None,
) -> ToolResult:
    """Create a Wi-Fi QR code (static; encodes credentials directly).
    auth is one of WPA, WPA3, WEP, nopass."""
    return _create("wifi", {
        "ssid": ssid, "auth": auth, "password": password,
        "hidden": hidden, "name": name,
    }, style)


@mcp.tool
def create_phone_qrcode(phone: str, name: str | None = None, style: Style = None) -> ToolResult:
    """Create a phone-number QR code (static; dials when scanned)."""
    return _create("phone", {"phone": phone, "name": name}, style)


@mcp.tool
def create_email_qrcode(
    email: str,
    subject: str | None = None,
    body: str | None = None,
    name: str | None = None,
    style: Style = None,
) -> ToolResult:
    """Create an email QR code (static; opens a prefilled email when scanned)."""
    return _create("email", {
        "email": email, "subject": subject, "body": body, "name": name,
    }, style)


@mcp.tool
def create_file_qrcode(
    file_path: str | None = None,
    file_url: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
    name: str | None = None,
    expires_in_days: int | None = None,
    style: Style = None,
) -> ToolResult:
    """Create a file/document QR code. Provide the file via file_path (local),
    file_url (downloaded by the server), or file_base64. Accepts images, PDF,
    Office, OpenDocument and text files up to 50 MB."""
    fname, content = client.load_file(
        file_path=file_path, file_url=file_url, file_base64=file_base64, filename=filename)
    data = {"type": "pdf", "name": name, "expires_in_days": expires_in_days}
    data.update(client.clean_style(style))
    files = {"pdf_file": (fname, content, "application/octet-stream")}
    doc = client.request("POST", "/qrcodes", data=data, files=files)
    return _qr_result(doc, "Created")


# ── Read / update / delete ────────────────────────────────────────────────────

@mcp.tool
def list_qrcodes(
    q: str | None = None,
    active: bool | None = None,
    page: int = 1,
    limit: int = 25,
) -> dict:
    """List the account's QR codes with optional search (q matches name/filename),
    active filter, and pagination (limit max 100). Returns data + pagination."""
    params: dict[str, Any] = {"page": page, "limit": limit}
    if q:
        params["q"] = q
    if active is not None:
        params["active"] = "true" if active else "false"
    result = client.request("GET", "/qrcodes", params=params)
    result["data"] = [present(d) for d in result.get("data", [])]
    return result


@mcp.tool
def get_qrcode(id: int) -> dict:
    """Get a single QR code's details by id. Use get_qrcode_png to view the image."""
    return present(client.request("GET", f"/qrcodes/{id}"))


@mcp.tool
def update_qrcode(
    id: int,
    name: str | None = None,
    is_active: bool | None = None,
    target_url: str | None = None,
    ssid: str | None = None,
    auth: str | None = None,
    password: str | None = None,
    hidden: bool | None = None,
    phone: str | None = None,
    email: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    expires_in_days: int | None = None,
    style: Style = None,
) -> dict:
    """Update a QR code's mutable fields. Only fields valid for the QR's type
    apply (e.g. target_url for url; ssid/auth/password/hidden for wifi). Set
    expires_in_days to 0/null to clear expiry."""
    payload: dict[str, Any] = {
        "name": name, "is_active": is_active, "target_url": target_url,
        "ssid": ssid, "auth": auth, "password": password, "hidden": hidden,
        "phone": phone, "email": email, "subject": subject, "body": body,
        "expires_in_days": expires_in_days,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    cleaned = client.clean_style(style)
    if cleaned:
        payload["style"] = cleaned  # PATCH nests style under "style"
    return present(client.request("PATCH", f"/qrcodes/{id}", json=payload))


@mcp.tool
def delete_qrcode(id: int) -> dict:
    """Delete a QR code (and its underlying file, if any)."""
    return client.request("DELETE", f"/qrcodes/{id}")


# ── Rendered images ───────────────────────────────────────────────────────────

@mcp.tool
def get_qrcode_png(id: int, size: int = 30, no_logo: bool = False) -> Image:
    """Render a saved QR code as a PNG image using its stored style/logo."""
    params: dict[str, Any] = {"size": size}
    if no_logo:
        params["no_logo"] = "1"
    png = client.request("GET", f"/qrcodes/{id}/qr.png", params=params, expect="bytes")
    return Image(data=png, format="png")


@mcp.tool
def get_qrcode_svg(id: int) -> str:
    """Return a saved QR code as SVG markup (text)."""
    return client.request("GET", f"/qrcodes/{id}/qr.svg", expect="text")


# ── Underlying file & logo ────────────────────────────────────────────────────

@mcp.tool
def download_qrcode_file(id: int, save_path: str | None = None) -> dict:
    """Download a file-backed QR code's current file. With save_path, writes it
    there and returns the path; otherwise returns base64 (small files only)."""
    import base64 as _b64
    from pathlib import Path as _Path

    content = client.request("GET", f"/qrcodes/{id}/file", expect="bytes")
    if save_path:
        p = _Path(save_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return {"saved_to": str(p), "bytes": len(content)}
    if len(content) > 8 * 1024 * 1024:
        raise ToolError(
            f"File is {len(content) // (1024 * 1024)} MB — too large to inline. "
            "Pass save_path to write it to disk instead."
        )
    return {"bytes": len(content), "base64": _b64.b64encode(content).decode()}


@mcp.tool
def replace_qrcode_file(
    id: int,
    file_path: str | None = None,
    file_url: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
) -> dict:
    """Replace a file-backed QR code's file while keeping its QR/link unchanged."""
    fname, content = client.load_file(
        file_path=file_path, file_url=file_url, file_base64=file_base64, filename=filename)
    files = {"pdf_file": (fname, content, "application/octet-stream")}
    return present(client.request("PUT", f"/qrcodes/{id}/file", files=files))


@mcp.tool
def set_qrcode_logo(
    id: int,
    file_path: str | None = None,
    file_url: str | None = None,
    file_base64: str | None = None,
    filename: str | None = None,
) -> dict:
    """Set/replace a QR code's center logo (PNG, JPG, or WebP)."""
    fname, content = client.load_file(
        file_path=file_path, file_url=file_url, file_base64=file_base64, filename=filename)
    files = {"logo": (fname, content, "application/octet-stream")}
    return present(client.request("PUT", f"/qrcodes/{id}/logo", files=files))


@mcp.tool
def remove_qrcode_logo(id: int) -> dict:
    """Remove a QR code's center logo."""
    return present(client.request("DELETE", f"/qrcodes/{id}/logo"))


# ── Analytics & history ───────────────────────────────────────────────────────

@mcp.tool
def get_qrcode_history(id: int) -> dict:
    """File-replacement history for a file-backed QR code (newest first)."""
    return client.request("GET", f"/qrcodes/{id}/history")


@mcp.tool
def get_qrcode_scans(id: int, days: int = 30) -> dict:
    """Scan analytics for a dynamic QR code: daily series + totals (days 1-365)."""
    return client.request("GET", f"/qrcodes/{id}/scans", params={"days": days})
