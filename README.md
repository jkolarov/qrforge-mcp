# QR Forge MCP server

An [MCP](https://modelcontextprotocol.io) server for [QR Forge](https://qrforge.work) —
create and manage trackable QR codes (links, files, Wi-Fi, WhatsApp, phone, email)
straight from an AI agent. It's a thin client over the public QR Forge REST API; it
stores nothing and authenticates with **your** QR Forge API token.

Get a token at **https://qrforge.work/api/keys**.

## Two ways to use it

### 1. Local (stdio) — install and run on your machine

```bash
uvx qrforge-mcp            # or: pipx install qrforge-mcp
```

Add it to your MCP client (Claude Desktop / Claude Code) — `claude_desktop_config.json`
or `.mcp.json`:

```json
{
  "mcpServers": {
    "qrforge": {
      "command": "qrforge-mcp",
      "env": { "QRFORGE_API_TOKEN": "your-token-here" }
    }
  }
}
```

Or with Claude Code's CLI:

```bash
claude mcp add qrforge --env QRFORGE_API_TOKEN=your-token-here -- qrforge-mcp
```

Environment variables:

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `QRFORGE_API_TOKEN` | yes (local) | — | Your QR Forge API token |
| `QRFORGE_API_URL` | no | `https://qrforge.work` | API base URL |

### 2. Hosted — connect to our server, no install

The hosted server runs at **`https://mcp.qrforge.work/mcp/`**. Pass your token in the
`X-QRForge-Token` header:

```bash
claude mcp add --transport http qrforge https://mcp.qrforge.work/mcp/ \
  --header "X-QRForge-Token: your-token-here"
```

> The hosted server sits behind Cloudflare, which strips the `Authorization` header
> on streaming requests — so use **`X-QRForge-Token`** for the hosted endpoint.
> (`Authorization: Bearer <token>` still works for local/self-hosted instances.)

The hosted server is stateless and multi-user: it forwards each request's token to
the API and never stores it.

## Tools

- **Identity/tokens:** `whoami`, `list_api_tokens`, `create_api_token`, `revoke_api_token`
- **Discovery:** `list_qr_types`, `list_qr_styles`
- **Render (no save):** `render_qr`
- **Create:** `create_url_qrcode`, `create_whatsapp_qrcode`, `create_wifi_qrcode`,
  `create_phone_qrcode`, `create_email_qrcode`, `create_file_qrcode`
- **Manage:** `list_qrcodes`, `get_qrcode`, `update_qrcode`, `delete_qrcode`
- **Images:** `get_qrcode_png`, `get_qrcode_svg`
- **Files/logo:** `download_qrcode_file`, `replace_qrcode_file`, `set_qrcode_logo`,
  `remove_qrcode_logo`
- **Analytics:** `get_qrcode_history`, `get_qrcode_scans`

`style` (optional, on create/render/update) is an object with keys `module_drawer`
(`square|rounded|circle|gapped|vertical_bars|horizontal_bars`), `fill_type`
(`solid|radial|horizontal|vertical`), `fill_color`, `fill_color_2`, `back_color` (hex).

File tools accept the file via `file_path` (local), `file_url` (server downloads it),
or `file_base64` — up to 50 MB.

## Develop

```bash
pip install -e ".[dev]"
pytest -q
```

Run the hosted server locally:

```bash
qrforge-mcp --http --host 0.0.0.0 --port 8001     # streamable-HTTP at /mcp/
# or stdio:
QRFORGE_API_TOKEN=... qrforge-mcp
```

## License

MIT
