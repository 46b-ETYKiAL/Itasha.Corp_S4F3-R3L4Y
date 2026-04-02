"""MCP Schema Extractor — Extract tool schemas from MCP servers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from .mcp_description_optimizer import DescriptionOptimizer
from .mcp_tool_catalog import ToolCatalog, ToolEntry
from .mcp_tool_categories import ToolCategories

logger = logging.getLogger(__name__)


@dataclass
class ExtractionStats:
    servers_processed: int = 0
    servers_failed: int = 0
    tools_extracted: int = 0
    tools_updated: int = 0
    duration_seconds: float = 0.0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class SchemaExtractor:
    def __init__(
        self,
        catalog: ToolCatalog,
        optimizer: DescriptionOptimizer | None = None,
        categories: ToolCategories | None = None,
    ) -> None:
        self._catalog = catalog
        self._optimizer = optimizer or DescriptionOptimizer()
        self._categories = categories or ToolCategories()

    def extract_from_config(self, mcp_config: dict[str, Any], *, timeout_seconds: int = 30) -> ExtractionStats:
        start = time.time()
        stats = ExtractionStats()
        for name, cfg in mcp_config.get("mcpServers", {}).items():
            if cfg.get("disabled", False):
                continue
            try:
                tools = self._extract_from_server(name, cfg, timeout_seconds=timeout_seconds)
                entries = self._tools_to_entries(name, tools)
                self._catalog.add_tools(entries)
                stats.servers_processed += 1
                stats.tools_extracted += len(entries)
            except Exception as e:
                stats.servers_failed += 1
                stats.errors.append(f"{name}: {e}")
        stats.duration_seconds = time.time() - start
        return stats

    def extract_from_static_tools(self, server_name: str, tools: list[dict[str, Any]]) -> list[ToolEntry]:
        entries = self._tools_to_entries(server_name, tools)
        self._catalog.add_tools(entries)
        return entries

    def _extract_from_server(
        self, server_name: str, server_config: dict[str, Any], *, timeout_seconds: int = 30
    ) -> list[dict[str, Any]]:
        command = server_config.get("command", "")
        if not command:
            raise RuntimeError(f"No command for server {server_name}")
        args = server_config.get("args", [])
        env = server_config.get("env", {})
        init_req = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "s4f3-schema-extractor", "version": "1.0.0"},
                },
            }
        )
        tools_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        merged_env = {**os.environ, **env} if env else None
        try:
            result = subprocess.run(
                [command, *args],
                check=False,
                input=init_req + "\n" + tools_req + "\n",
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=merged_env,
            )
            if result.returncode != 0 and not result.stdout:
                raise RuntimeError(f"Server failed: {result.stderr[:200]}")
            return self._parse_tools_response(result.stdout)
        except subprocess.TimeoutExpired as err:
            raise RuntimeError(f"Timed out after {timeout_seconds}s") from err
        except FileNotFoundError as err:
            raise RuntimeError(f"Command not found: {command}") from err

    def _parse_tools_response(self, stdout: str) -> list[dict[str, Any]]:
        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                response = json.loads(line)
                if "tools" in response.get("result", {}):
                    return response["result"]["tools"]
            except json.JSONDecodeError:
                continue
        return []

    def _tools_to_entries(self, server_name: str, tools: list[dict[str, Any]]) -> list[ToolEntry]:
        entries = []
        now = time.time()
        for tool in tools:
            name = tool.get("name", "")
            if not name:
                continue
            entries.append(
                ToolEntry(
                    name=name,
                    description=self._optimizer.optimize(name, tool),
                    server_name=server_name,
                    category=self._categories.categorize(name, server_name),
                    full_schema=tool,
                    last_updated=now,
                )
            )
        return entries


__all__ = ["ExtractionStats", "SchemaExtractor"]
