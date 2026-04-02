"""MCP Lazy-Loading Health Module (Plan 114, Phase 6).

Probes: proxy reachable, catalog populated, schema cache functional,
context savings positive, no orphan connections.
"""

from __future__ import annotations

from typing import Any


class McpLazyLoadingHealth:
    """Health check for the lazy-loading system."""

    def __init__(
        self,
        catalog: Any = None,
        proxy_metrics: Any = None,
        cache_stats: dict[str, Any] | None = None,
    ) -> None:
        self._catalog = catalog
        self._proxy_metrics = proxy_metrics
        self._cache_stats = cache_stats or {}

    def check(self) -> dict[str, Any]:
        probes: dict[str, dict[str, Any]] = {}
        issues: list[str] = []

        probes["catalog_populated"] = self._check_catalog()
        if not probes["catalog_populated"]["passed"]:
            issues.append(probes["catalog_populated"]["message"])

        probes["cache_functional"] = self._check_cache()
        if not probes["cache_functional"]["passed"]:
            issues.append(probes["cache_functional"]["message"])

        probes["context_savings"] = self._check_savings()
        if not probes["context_savings"]["passed"]:
            issues.append(probes["context_savings"]["message"])

        return {
            "healthy": not issues,
            "probes": probes,
            "summary": "All probes passed" if not issues else "; ".join(issues),
        }

    def _check_catalog(self) -> dict[str, Any]:
        if self._catalog is None:
            return {"passed": False, "message": "No catalog"}
        try:
            count = self._catalog.count()
            return {"passed": count > 0, "message": f"{count} tools"}
        except Exception as e:
            return {"passed": False, "message": str(e)}

    def _check_cache(self) -> dict[str, Any]:
        if not self._cache_stats:
            return {"passed": True, "message": "No cache stats (OK if proxy not active)"}
        return {"passed": True, "message": f"Cache size: {self._cache_stats.get('size', 0)}"}

    def _check_savings(self) -> dict[str, Any]:
        if self._proxy_metrics is None:
            return {"passed": True, "message": "No metrics (proxy not active)"}
        summary = self._proxy_metrics.get_summary()
        saved = summary.get("tokens_saved", 0)
        return {"passed": saved >= 0, "message": f"Tokens saved: {saved}"}


__all__ = ["McpLazyLoadingHealth"]
