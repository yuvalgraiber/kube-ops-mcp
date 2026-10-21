from kube_ops_mcp.server import mcp


def test_server_identity() -> None:
    assert mcp.name == "kube-ops"
    assert "Read-only" in mcp.instructions


def test_server_reports_package_version() -> None:
    assert mcp.version
