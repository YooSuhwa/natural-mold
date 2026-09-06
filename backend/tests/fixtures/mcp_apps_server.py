"""Deterministic local MCP Apps server used by the item-9 contract proof."""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

APP_URI = "ui://weather/dashboard"
APP_HTML = """<!doctype html><html><body>
<main id="weather-app">
<p>Weather: 23 C</p>
<button id="refresh" type="button">Refresh weather</button>
<output id="result"></output>
</main>
<script>
let requestId = 0;
const pending = new Map();
addEventListener('message', (event) => {
  const message = event.data;
  if (!message || message.jsonrpc !== '2.0' || message.method || !pending.has(message.id)) return;
  pending.get(message.id)(message);
  pending.delete(message.id);
});
const rpc = (method, params) => new Promise((resolve) => {
  const id = ++requestId;
  pending.set(id, resolve);
  parent.postMessage({ jsonrpc: '2.0', id, method, params }, '*');
});
rpc('ui/initialize', { protocolVersion: '2025-06-18' }).then(() => {
  parent.postMessage({ jsonrpc: '2.0', method: 'notifications/initialized' }, '*');
});
document.querySelector('#refresh').addEventListener('click', async () => {
  const response = await rpc('tools/call', {
    name: 'refresh_weather', arguments: { city: 'Seoul' }
  });
  document.querySelector('#result').textContent = response.result.structuredContent.refreshed
    ? 'Weather refreshed' : 'Refresh failed';
});
</script></body></html>"""


def build_server(*, port: int) -> FastMCP:
    server = FastMCP(
        "Moldy MCP Apps fixture",
        host="127.0.0.1",
        port=port,
        stateless_http=True,
        json_response=True,
    )

    @server.tool(meta={"ui": {"resourceUri": APP_URI}})
    def weather(city: str = "Seoul") -> dict[str, object]:
        return {"city": city, "temperature": 23, "condition": "sunny"}

    @server.tool(meta={"ui": {"visibility": ["app"]}})
    def refresh_weather(city: str = "Seoul") -> dict[str, object]:
        return {"city": city, "refreshed": True}

    @server.resource(
        APP_URI,
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {
                "csp": {"connectDomains": ["https://api.weather.example"]},
                "prefersBorder": True,
            }
        },
    )
    def weather_app() -> str:
        return APP_HTML

    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    build_server(port=args.port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
