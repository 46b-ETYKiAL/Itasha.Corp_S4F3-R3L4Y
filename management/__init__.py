"""MCP Management Package — Tool catalog, schema extraction, and lazy-loading.

Provides infrastructure for intelligent MCP tool loading:
- ToolCatalog: SQLite-backed tool metadata store
- SchemaExtractor: Extracts schemas from MCP servers via tools/list
- DescriptionOptimizer: Generates concise tool descriptions
- ToolCategories: Rule-based tool categorization
"""

from .mcp_description_optimizer import DescriptionOptimizer
from .mcp_schema_extractor import SchemaExtractor
from .mcp_tool_catalog import ToolCatalog, ToolEntry
from .mcp_tool_categories import ToolCategories

__all__ = [
    "DescriptionOptimizer",
    "SchemaExtractor",
    "ToolCatalog",
    "ToolCategories",
    "ToolEntry",
]
