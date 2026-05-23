from django.conf import settings
from fastmcp import FastMCP

from apps.mcp_server.auth import AwesomeReposAPIKeyVerifier
from apps.mcp_server.tools import register_tools

MCP_PATH = "/mcp"
MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_MCP_PROTOCOL_VERSIONS = {
    MCP_PROTOCOL_VERSION,
    "2025-06-18",
    "2025-03-26",
}


def build_mcp_server() -> FastMCP:
    server = FastMCP(
        name="awesome-repos",
        instructions=(
            "Use these read-only tools to search Awesome Repos data. "
            "Authenticate with the same API key used for the HTTP API."
        ),
        version="0.1.0",
        website_url=settings.SITE_URL,
        auth=AwesomeReposAPIKeyVerifier(
            base_url=settings.SITE_URL,
            resource_base_url=settings.SITE_URL,
            required_scopes=["awesome-repos:read"],
        ),
    )
    register_tools(server)
    return server


mcp_server = build_mcp_server()
mcp_asgi_app = mcp_server.http_app(
    path=MCP_PATH,
    transport="streamable-http",
    json_response=True,
    stateless_http=True,
)
