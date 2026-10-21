"""MCP server entry point: creates the app and registers tools, resources and prompts."""

from importlib.metadata import version

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="kube-ops",
    version=version("kube-ops-mcp"),
    instructions=(
        "Read-only Kubernetes incident triage. Use these tools to inspect pod status, "
        "events, logs and Prometheus metrics, then explain the most likely root cause."
    ),
)


def main() -> None:
    """Run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
