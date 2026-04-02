"""MCP Catalog Health Check."""

from __future__ import annotations

import time
from typing import Any

from .mcp_tool_catalog import ToolCatalog


class McpCatalogHealth:
    def __init__(self, catalog: ToolCatalog | None = None, staleness_hours: int = 72) -> None:
        self._catalog = catalog
        self._staleness_hours = staleness_hours

    def check(self) -> dict[str, Any]:
        probes: dict[str, dict[str, Any]] = {}
        issues: list[str] = []
        probes["catalog_exists"] = self._check_exists()
        if not probes["catalog_exists"]["passed"]:
            return {"healthy": False, "probes": probes, "summary": "Catalog not available"}
        probes["tool_count"] = self._check_count()
        if not probes["tool_count"]["passed"]:
            issues.append(probes["tool_count"]["message"])
        probes["staleness"] = self._check_stale()
        if not probes["staleness"]["passed"]:
            issues.append(probes["staleness"]["message"])
        probes["description_quality"] = self._check_descs()
        if not probes["description_quality"]["passed"]:
            issues.append(probes["description_quality"]["message"])
        probes["category_distribution"] = self._check_cats()
        if not probes["category_distribution"]["passed"]:
            issues.append(probes["category_distribution"]["message"])
        return {
            "healthy": not issues,
            "probes": probes,
            "summary": "All probes passed" if not issues else "; ".join(issues),
        }

    def _check_exists(self) -> dict[str, Any]:
        if self._catalog is None:
            return {"passed": False, "message": "No catalog instance"}
        try:
            self._catalog.count()
            return {"passed": True, "message": "OK"}
        except Exception as e:
            return {"passed": False, "message": str(e)}

    def _check_count(self) -> dict[str, Any]:
        c = self._catalog.count()
        return {"passed": c > 0, "message": f"{c} tools", "count": c}

    def _check_stale(self) -> dict[str, Any]:
        tools = self._catalog.list_all()
        if not tools:
            return {"passed": True, "message": "No tools", "stale_count": 0}
        threshold = time.time() - self._staleness_hours * 3600
        stale = [t for t in tools if t.last_updated < threshold]
        return {"passed": len(stale) <= len(tools) * 0.5, "message": f"{len(stale)} stale", "stale_count": len(stale)}

    def _check_descs(self) -> dict[str, Any]:
        tools = self._catalog.list_all()
        if not tools:
            return {"passed": True, "message": "No tools", "too_long": 0, "empty": 0}
        too_long = [t for t in tools if len(t.description) > 80]
        empty = [t for t in tools if not t.description.strip()]
        issues = []
        if too_long:
            issues.append(f"{len(too_long)} too long")
        if empty:
            issues.append(f"{len(empty)} empty")
        return {
            "passed": not issues,
            "message": "; ".join(issues) if issues else "OK",
            "too_long": len(too_long),
            "empty": len(empty),
        }

    def _check_cats(self) -> dict[str, Any]:
        tools = self._catalog.list_all()
        if not tools:
            return {"passed": True, "message": "No tools"}
        uncat = [t for t in tools if t.category == "uncategorized"]
        ratio = len(uncat) / len(tools)
        return {
            "passed": ratio <= 0.5,
            "message": f"{len(uncat)} uncategorized ({ratio:.0%})",
            "uncategorized_count": len(uncat),
            "categories": self._catalog.get_categories(),
        }


__all__ = ["McpCatalogHealth"]
