"""MCP Server Sync — Keep ToolCatalog in sync with mcp.json.

Called after server add/enable/disable to rebuild catalog entries.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .mcp_description_optimizer import DescriptionOptimizer
from .mcp_schema_extractor import SchemaExtractor
from .mcp_tool_catalog import ToolCatalog
from .mcp_tool_categories import ToolCategories

logger = logging.getLogger(__name__)

_DEFAULT_MCP_CONFIG = Path(".s4f3/config/mcp.json")


def refresh_catalog(
    mcp_config_path: Path | None = None,
    catalog: ToolCatalog | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Rebuild the tool catalog from mcp.json.

    Args:
        mcp_config_path: Path to mcp.json.
        catalog: ToolCatalog instance (creates default if None).
        timeout_seconds: Per-server extraction timeout.

    Returns:
        Dict with servers_processed, tools_extracted, errors.
    """
    path = mcp_config_path or _DEFAULT_MCP_CONFIG
    if not path.exists():
        return {"error": f"mcp.json not found at {path}"}

    with open(path, encoding="utf-8") as f:
        mcp_config = json.load(f)

    # Normalize key: mcp.json uses "servers", plan expects "mcpServers"
    if "servers" in mcp_config and "mcpServers" not in mcp_config:
        mcp_config["mcpServers"] = mcp_config["servers"]

    cat = catalog or ToolCatalog()
    ext = SchemaExtractor(cat, DescriptionOptimizer(), ToolCategories())
    stats = ext.extract_from_config(mcp_config, timeout_seconds=timeout_seconds)

    return {
        "servers_processed": stats.servers_processed,
        "servers_failed": stats.servers_failed,
        "tools_extracted": stats.tools_extracted,
        "errors": stats.errors,
        "duration_seconds": stats.duration_seconds,
    }


def sync_on_server_change(
    server_name: str,
    action: str,
    catalog: ToolCatalog | None = None,
) -> None:
    """Sync catalog when a server is added/enabled/disabled.

    Args:
        server_name: Name of the changed server.
        action: One of 'add', 'enable', 'disable', 'remove'.
        catalog: ToolCatalog instance.
    """
    cat = catalog or ToolCatalog()
    if action in ("disable", "remove"):
        removed = cat.remove_server_tools(server_name)
        logger.info(f"Removed {removed} tools for {action}d server {server_name}")
    elif action in ("add", "enable"):
        logger.info(f"Server {server_name} {action}d — run refresh_catalog() to update")


__all__ = ["refresh_catalog", "sync_on_server_change"]
