# QR Forge MCP server — hosted (streamable-HTTP) image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8001

WORKDIR /app

# Install the package (and its deps) from the build context.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Drop privileges.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8001

# Hosted mode: each request carries its own Authorization: Bearer <token>.
CMD ["qrforge-mcp", "--http", "--host", "0.0.0.0", "--port", "8001"]
