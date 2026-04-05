"""MCP Agent-Aware Preloader — Preload tools when agents are invoked.

Reads agent definitions and mcp.json required_by fields to determine
which tools to preload when a specific agent starts.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MCP_CONFIG = Path(
    os.environ.get("MCP_CONFIG_PATH", "mcp.json")
)


def get_tools_for_agent(
    agent_name: str,
    mcp_config_path: Path | None = None,
) -> list[str]:
    """Get MCP tools that should be preloaded for an agent.

    Reads the required_by field from mcp.json server entries
    and returns tools from servers that list this agent.

    Args:
        agent_name: Agent identifier (e.g., "vault-retriever").
        mcp_config_path: Path to mcp.json.

    Returns:
        List of tool names to preload.
    """
    path = mcp_config_path or _DEFAULT_MCP_CONFIG
    if not path.exists():
        return []

    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    servers = config.get("servers", config.get("mcpServers", {}))
    matching_servers = []

    for server_name, server_config in servers.items():
        if not isinstance(server_config, dict):
            continue
        required_by = server_config.get("required_by", [])
        if agent_name in required_by:
            matching_servers.append(server_name)

    return matching_servers


def get_preload_map(
    mcp_config_path: Path | None = None,
) -> dict[str, list[str]]:
    """Build a complete agent -> server mapping.

    Returns:
        Dict mapping agent names to lists of server names.
    """
    path = mcp_config_path or _DEFAULT_MCP_CONFIG
    if not path.exists():
        return {}

    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    servers = config.get("servers", config.get("mcpServers", {}))
    preload_map: dict[str, list[str]] = {}

    for server_name, server_config in servers.items():
        if not isinstance(server_config, dict):
            continue
        for agent in server_config.get("required_by", []):
            preload_map.setdefault(agent, []).append(server_name)

    return preload_map


__all__ = ["get_preload_map", "get_tools_for_agent"]
