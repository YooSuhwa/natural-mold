"""``python -m moldy_mcp_runtime``: serve the runtime (Compose service ``mcp-runtime``)."""

import os

import uvicorn

from moldy_mcp_runtime.app import create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host=os.environ.get("MCP_RUNTIME_HOST", "0.0.0.0"),  # container port
        port=int(os.environ.get("MCP_RUNTIME_PORT", "8096")),
        proxy_headers=False,
    )
