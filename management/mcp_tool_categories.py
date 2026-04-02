"""MCP Tool Categories — Rule-based tool categorization."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(".s4f3/config/mcp-tool-categories.yaml")

_BUILTIN_RULES: list[dict[str, Any]] = [
    {"category": "vault", "server_patterns": ["obsidian"], "tool_patterns": ["obsidian", "vault"]},
    {"category": "docs", "server_patterns": ["context7"], "tool_patterns": ["resolve.*library", "get.*docs"]},
    {
        "category": "browser",
        "server_patterns": ["playwright", "puppeteer"],
        "tool_patterns": ["browser_", "puppeteer_"],
    },
    {"category": "code", "server_patterns": [], "tool_patterns": ["analyze_.*file", "find_long_functions"]},
    {
        "category": "media",
        "server_patterns": ["blender", "elevenlabs"],
        "tool_patterns": ["ffmpeg", "imagemagick", "text_to_speech"],
    },
    {"category": "data", "server_patterns": ["neon"], "tool_patterns": ["run_sql", "describe_table", "get_database"]},
    {
        "category": "search",
        "server_patterns": ["arxiv", "wikipedia"],
        "tool_patterns": ["search_.*papers", "paper_search", "search_wikipedia"],
    },
    {
        "category": "observability",
        "server_patterns": ["grafana"],
        "tool_patterns": ["list_loki_", "query_loki_", "query_prometheus"],
    },
]


class ToolCategories:
    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or _DEFAULT_CONFIG_PATH
        self._rules: list[dict[str, Any]] = []
        self._always_loaded: list[str] = []
        self._load_groups: dict[str, list[str]] = {}
        self._load_config()

    def _load_config(self) -> None:
        if self._config_path.exists():
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                self._rules = config.get("category_rules", _BUILTIN_RULES)
                self._always_loaded = config.get("always_loaded", [])
                self._load_groups = config.get("load_groups", {})
                return
            except (OSError, yaml.YAMLError) as e:
                logger.warning(f"Failed to load categories config: {e}")
        self._rules = _BUILTIN_RULES

    def categorize(self, tool_name: str, server_name: str = "") -> str:
        tool_lower = tool_name.lower()
        server_lower = server_name.lower()
        for rule in self._rules:
            for pattern in rule.get("server_patterns", []):
                if pattern.lower() in server_lower:
                    return rule["category"]
            for pattern in rule.get("tool_patterns", []):
                if re.search(pattern.lower(), tool_lower):
                    return rule["category"]
        return "uncategorized"

    def categorize_batch(self, tools: list[tuple[str, str]]) -> dict[str, str]:
        return {name: self.categorize(name, server) for name, server in tools}

    @property
    def always_loaded(self) -> list[str]:
        return list(self._always_loaded)

    @property
    def load_groups(self) -> dict[str, list[str]]:
        return dict(self._load_groups)

    def get_group_for_category(self, category: str) -> list[str]:
        for group_categories in self._load_groups.values():
            if category in group_categories:
                return list(group_categories)
        return [category]


__all__ = ["ToolCategories"]
