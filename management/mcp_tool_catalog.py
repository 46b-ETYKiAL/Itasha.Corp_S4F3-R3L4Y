"""MCP Tool Catalog — SQLite-backed tool metadata store."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from db_connect import db_connect

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(".s4f3-data/mcp_tool_catalog.db")


@dataclass
class ToolEntry:
    """Lightweight tool metadata for the catalog."""

    name: str
    description: str
    server_name: str
    category: str = "uncategorized"
    full_schema: dict[str, Any] = field(default_factory=dict)
    last_updated: float = 0.0

    def __post_init__(self) -> None:
        if self.last_updated == 0.0:
            self.last_updated = time.time()


class ToolCatalog:
    """SQLite-backed catalog of MCP tool metadata. Thread-safe, WAL mode."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = db_connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tools (
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    server_name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'uncategorized',
                    full_schema TEXT NOT NULL DEFAULT '{}',
                    last_updated REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_server ON tools(server_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tools_category ON tools(category)")

    def add_tool(self, entry: ToolEntry) -> None:
        schema_json = json.dumps(entry.full_schema, separators=(",", ":"))
        with self._lock, self._get_conn() as conn:
            conn.execute(
                """INSERT INTO tools (name, description, server_name, category, full_schema, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     description=excluded.description, server_name=excluded.server_name,
                     category=excluded.category, full_schema=excluded.full_schema,
                     last_updated=excluded.last_updated""",
                (entry.name, entry.description, entry.server_name, entry.category, schema_json, entry.last_updated),
            )

    def add_tools(self, entries: list[ToolEntry]) -> int:
        rows = [
            (
                e.name,
                e.description,
                e.server_name,
                e.category,
                json.dumps(e.full_schema, separators=(",", ":")),
                e.last_updated,
            )
            for e in entries
        ]
        with self._lock, self._get_conn() as conn:
            conn.executemany(
                """INSERT INTO tools (name, description, server_name, category, full_schema, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     description=excluded.description, server_name=excluded.server_name,
                     category=excluded.category, full_schema=excluded.full_schema,
                     last_updated=excluded.last_updated""",
                rows,
            )
        return len(rows)

    def get_tool(self, name: str) -> ToolEntry | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM tools WHERE name = ?", (name,)).fetchone()
        return self._row_to_entry(row) if row else None

    def get_tool_schema(self, name: str) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT full_schema FROM tools WHERE name = ?", (name,)).fetchone()
        return json.loads(row["full_schema"]) if row else None

    def search_tools(self, query: str) -> list[ToolEntry]:
        pattern = f"%{query}%"
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT name, description, server_name, category, last_updated FROM tools WHERE name LIKE ? OR description LIKE ? ORDER BY name",
                (pattern, pattern),
            ).fetchall()
        return [self._row_to_entry(r, include_schema=False) for r in rows]

    def list_by_category(self, category: str) -> list[ToolEntry]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT name, description, server_name, category, last_updated FROM tools WHERE category = ? ORDER BY name",
                (category,),
            ).fetchall()
        return [self._row_to_entry(r, include_schema=False) for r in rows]

    def list_by_server(self, server_name: str) -> list[ToolEntry]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT name, description, server_name, category, last_updated FROM tools WHERE server_name = ? ORDER BY name",
                (server_name,),
            ).fetchall()
        return [self._row_to_entry(r, include_schema=False) for r in rows]

    def list_all(self) -> list[ToolEntry]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT name, description, server_name, category, last_updated FROM tools ORDER BY name"
            ).fetchall()
        return [self._row_to_entry(r, include_schema=False) for r in rows]

    def remove_tool(self, name: str) -> bool:
        with self._lock, self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM tools WHERE name = ?", (name,))
        return cursor.rowcount > 0

    def remove_server_tools(self, server_name: str) -> int:
        with self._lock, self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM tools WHERE server_name = ?", (server_name,))
        return cursor.rowcount

    def count(self) -> int:
        with self._get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM tools").fetchone()
        return row["cnt"]

    def count_by_server(self) -> dict[str, int]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT server_name, COUNT(*) as cnt FROM tools GROUP BY server_name").fetchall()
        return {r["server_name"]: r["cnt"] for r in rows}

    def get_categories(self) -> list[str]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT DISTINCT category FROM tools ORDER BY category").fetchall()
        return [r["category"] for r in rows]

    def get_lightweight_catalog(self) -> list[dict[str, str]]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT name, description, server_name, category FROM tools ORDER BY name").fetchall()
        return [
            {"name": r["name"], "description": r["description"], "server": r["server_name"], "category": r["category"]}
            for r in rows
        ]

    def clear(self) -> None:
        with self._lock, self._get_conn() as conn:
            conn.execute("DELETE FROM tools")

    @staticmethod
    def _row_to_entry(row: sqlite3.Row, *, include_schema: bool = True) -> ToolEntry:
        schema = {}
        if include_schema and "full_schema" in row.keys():
            schema = json.loads(row["full_schema"])
        return ToolEntry(
            name=row["name"],
            description=row["description"],
            server_name=row["server_name"],
            category=row["category"],
            full_schema=schema,
            last_updated=row["last_updated"],
        )


__all__ = ["ToolCatalog", "ToolEntry"]
