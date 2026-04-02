"""MCP Loading Strategy Factory (Plan 114, Phase 5).

Produces the optimal tool loading strategy based on the detected
coding CLI and tool count.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StrategyType(Enum):
    DIRECT = "direct"  # Small tool count (<10), load everything
    TOOL_SEARCH = "tool_search"  # Claude Code native defer_loading
    PROXY = "proxy"  # Other CLIs — route through proxy
    HYBRID = "hybrid"  # Both proxy + native when available


@dataclass
class LoadingStrategy:
    """Selected loading strategy with configuration."""

    strategy_type: StrategyType
    reason: str
    proxy_enabled: bool = False
    defer_loading_enabled: bool = False
    always_loaded_tools: list[str] | None = None

    def __post_init__(self) -> None:
        if self.always_loaded_tools is None:
            self.always_loaded_tools = []


class McpLoadingStrategyFactory:
    """Factory that produces the optimal loading strategy.

    Args:
        tool_count: Total tools in catalog.
        supports_tool_deferral: Whether the host CLI supports native defer_loading.
        tool_count_threshold: Below this, load everything directly.
    """

    def __init__(
        self,
        tool_count: int = 0,
        supports_tool_deferral: bool = False,
        tool_count_threshold: int = 10,
    ) -> None:
        self.tool_count = tool_count
        self.supports_tool_deferral = supports_tool_deferral
        self.tool_count_threshold = tool_count_threshold

    def select(self, always_loaded: list[str] | None = None) -> LoadingStrategy:
        """Select the optimal loading strategy.

        Args:
            always_loaded: Tools to always load regardless of strategy.

        Returns:
            LoadingStrategy with the selected approach.
        """
        always = always_loaded or []

        # Small tool count — load everything
        if self.tool_count <= self.tool_count_threshold:
            return LoadingStrategy(
                strategy_type=StrategyType.DIRECT,
                reason=f"Only {self.tool_count} tools (threshold: {self.tool_count_threshold})",
                always_loaded_tools=always,
            )

        # Host supports native deferral (e.g., Claude Code)
        if self.supports_tool_deferral:
            return LoadingStrategy(
                strategy_type=StrategyType.TOOL_SEARCH,
                reason="Host supports native tool search/defer_loading",
                defer_loading_enabled=True,
                always_loaded_tools=always,
            )

        # Default: proxy strategy
        return LoadingStrategy(
            strategy_type=StrategyType.PROXY,
            reason=f"Host lacks native deferral, {self.tool_count} tools exceed threshold",
            proxy_enabled=True,
            always_loaded_tools=always,
        )


__all__ = ["LoadingStrategy", "McpLoadingStrategyFactory", "StrategyType"]
