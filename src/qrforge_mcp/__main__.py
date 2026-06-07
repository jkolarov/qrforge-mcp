"""CLI entrypoint. Default transport is stdio (local); ``--http`` runs the
hosted streamable-HTTP server."""
from __future__ import annotations

import argparse
import os

from .server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="qrforge-mcp",
        description="MCP server for the QR Forge API (https://qrforge.work).",
    )
    parser.add_argument(
        "--http", action="store_true",
        help="Run as a hosted HTTP (streamable-http) server instead of stdio.",
    )
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8001")))
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
