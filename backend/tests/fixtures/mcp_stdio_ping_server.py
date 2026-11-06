"""A tiny stdio MCP server (FastMCP 4) for the stdio probe test.

The tool description echoes ``FOO`` so the test sees the environment reached the child.
"""

from __future__ import annotations

import os

from fastmcp import FastMCP

server = FastMCP("stub", version="0.0.1")


@server.tool(
    description=f"ping {os.environ.get('FOO', '')}".strip(),
    meta={
        "ui": {
            "resourceUri": "ui://ping/dashboard",
            "visibility": ["model", "app"],
            "unknown": "drop",
        },
        "Authorization": "drop",
    },
)
def ping() -> str:
    return "pong"


if __name__ == "__main__":
    server.run("stdio", show_banner=False)
