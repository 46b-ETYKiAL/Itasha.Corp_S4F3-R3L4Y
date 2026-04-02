"""MCP Tool Catalog CLI.

Usage:
    python -m mcp_management refresh [--config PATH] [--timeout N]
    python -m mcp_management search <query>
    python -m mcp_management list [--server S] [--category C]
    python -m mcp_management stats
    python -m mcp_management health [-v]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _get_catalog():
    from .mcp_tool_catalog import ToolCatalog

    return ToolCatalog()


def _cmd_refresh(args):
    from .mcp_description_optimizer import DescriptionOptimizer
    from .mcp_schema_extractor import SchemaExtractor
    from .mcp_tool_catalog import ToolCatalog
    from .mcp_tool_categories import ToolCategories

    mcp_path = Path(args.config)
    if not mcp_path.exists():
        print(f"Error: {mcp_path} not found", file=sys.stderr)
        return 1
    with open(mcp_path, encoding="utf-8") as f:
        mcp_config = json.load(f)
    cat = ToolCatalog()
    ext = SchemaExtractor(cat, DescriptionOptimizer(), ToolCategories())
    stats = ext.extract_from_config(mcp_config, timeout_seconds=args.timeout)
    print(f"Servers processed: {stats.servers_processed}")
    print(f"Servers failed:    {stats.servers_failed}")
    print(f"Tools extracted:   {stats.tools_extracted}")
    print(f"Duration:          {stats.duration_seconds:.1f}s")
    if stats.errors:
        for err in stats.errors:
            print(f"  ERROR: {err}")
    return 0 if stats.servers_failed == 0 else 1


def _cmd_search(args):
    results = _get_catalog().search_tools(args.query)
    if not results:
        print(f"No tools matching '{args.query}'")
        return 0
    for t in results:
        print(f"  {t.name:<40} [{t.category}] {t.description}")
    return 0


def _cmd_list(args):
    cat = _get_catalog()
    tools = (
        cat.list_by_server(args.server)
        if args.server
        else cat.list_by_category(args.category)
        if args.category
        else cat.list_all()
    )
    for t in tools:
        print(f"  {t.name:<40} [{t.category}] ({t.server_name}) {t.description}")
    print(f"\nTotal: {len(tools)}")
    return 0


def _cmd_stats(args):
    cat = _get_catalog()
    print(f"Total tools: {cat.count()}")
    for srv, cnt in sorted(cat.count_by_server().items()):
        print(f"  {srv:<30} {cnt}")
    return 0


def _cmd_health(args):
    from .mcp_catalog_health import McpCatalogHealth

    result = McpCatalogHealth(_get_catalog()).check()
    print(f"{'HEALTHY' if result['healthy'] else 'UNHEALTHY'}: {result['summary']}")
    if args.verbose:
        for name, probe in result["probes"].items():
            print(f"  [{'OK' if probe['passed'] else 'FAIL'}] {name}: {probe['message']}")
    return 0 if result["healthy"] else 1


def main():
    parser = argparse.ArgumentParser(prog="mcp_management")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("refresh")
    p.add_argument("--config", default=".s4f3/config/mcp.json")
    p.add_argument("--timeout", type=int, default=30)
    p = sub.add_parser("search")
    p.add_argument("query")
    p = sub.add_parser("list")
    p.add_argument("--server", default="")
    p.add_argument("--category", default="")
    sub.add_parser("stats")
    p = sub.add_parser("health")
    p.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    handlers = {
        "refresh": _cmd_refresh,
        "search": _cmd_search,
        "list": _cmd_list,
        "stats": _cmd_stats,
        "health": _cmd_health,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
