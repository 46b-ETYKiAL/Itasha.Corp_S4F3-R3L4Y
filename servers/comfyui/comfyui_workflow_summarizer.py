#!/usr/bin/env python3
"""
ComfyUI Workflow Summarizer

Extracts compact summaries from large ComfyUI workflow JSON files to prevent
context window exhaustion when working with Claude Code.

Problem: ComfyUI workflow files are 2,500-3,000+ lines. Reading full files
into conversation context causes "Prompt is too long" errors.

Solution: Extract only essential metadata into a 50-100 line summary that
provides enough context for targeted edits without exhausting context window.

Usage:
    # Full summary (default)
    python comfyui_workflow_summarizer.py workflow.json

    # Specific node types
    python comfyui_workflow_summarizer.py workflow.json --nodes KSampler,CLIPTextEncode

    # Node by ID
    python comfyui_workflow_summarizer.py workflow.json --id 20

    # JSON output for programmatic use
    python comfyui_workflow_summarizer.py workflow.json --json
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NodeSummary:
    """Compact summary of a workflow node."""

    id: int
    type: str
    title: str | None = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    widget_values: list[Any] = field(default_factory=list)
    connections_in: list[str] = field(default_factory=list)
    connections_out: list[str] = field(default_factory=list)

    def to_compact(self) -> str:
        """Return a single-line compact representation."""
        title_str = f" ({self.title})" if self.title and self.title != self.type else ""
        widgets = self._format_widgets()
        conn_in = f" <- {self.connections_in}" if self.connections_in else ""
        conn_out = f" -> {self.connections_out}" if self.connections_out else ""
        return f"[{self.id}] {self.type}{title_str}{widgets}{conn_in}{conn_out}"

    def _format_widgets(self) -> str:
        """Format widget values compactly."""
        if not self.widget_values:
            return ""
        # Truncate long strings
        formatted = []
        for v in self.widget_values[:5]:  # Max 5 values
            if isinstance(v, str) and len(v) > 40:
                formatted.append(f'"{v[:37]}..."')
            elif isinstance(v, str):
                formatted.append(f'"{v}"')
            else:
                formatted.append(str(v))
        suffix = "..." if len(self.widget_values) > 5 else ""
        return f" [{', '.join(formatted)}{suffix}]"


@dataclass
class WorkflowSummary:
    """Compact summary of an entire workflow."""

    file_path: str
    version: int | float
    node_count: int
    link_count: int
    group_count: int
    nodes_by_type: dict[str, int] = field(default_factory=dict)
    node_summaries: list[NodeSummary] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    loras: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)

    def to_text(self, verbose: bool = False) -> str:
        """Generate human-readable summary."""
        lines = [
            f"# Workflow Summary: {Path(self.file_path).name}",
            "",
            "## Overview",
            f"- Version: {self.version}",
            f"- Nodes: {self.node_count}",
            f"- Links: {self.link_count}",
            f"- Groups: {self.group_count}",
        ]

        if self.metadata:
            lines.append("")
            lines.append("## Metadata")
            for k, v in self.metadata.items():
                if isinstance(v, str) and len(v) > 60:
                    v = v[:57] + "..."
                lines.append(f"- {k}: {v}")

        if self.models:
            lines.append("")
            lines.append("## Models")
            for m in self.models:
                lines.append(f"- {m}")

        if self.loras:
            lines.append("")
            lines.append("## LoRAs")
            for lora in self.loras:
                lines.append(f"- {lora}")

        if self.prompts:
            lines.append("")
            lines.append("## Prompts")
            for name, prompt in self.prompts.items():
                truncated = prompt[:200] + "..." if len(prompt) > 200 else prompt
                lines.append(f"### {name}")
                lines.append(f"{truncated}")

        lines.append("")
        lines.append(f"## Node Types ({len(self.nodes_by_type)} unique)")
        for node_type, count in sorted(self.nodes_by_type.items(), key=lambda x: -x[1]):
            lines.append(f"- {node_type}: {count}")

        if verbose or len(self.node_summaries) <= 20:
            lines.append("")
            lines.append("## Node Details")
            for ns in self.node_summaries:
                lines.append(ns.to_compact())

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "file_path": self.file_path,
            "version": self.version,
            "node_count": self.node_count,
            "link_count": self.link_count,
            "group_count": self.group_count,
            "nodes_by_type": self.nodes_by_type,
            "metadata": self.metadata,
            "prompts": self.prompts,
            "loras": self.loras,
            "models": self.models,
            "nodes": [
                {
                    "id": ns.id,
                    "type": ns.type,
                    "title": ns.title,
                    "widgets": ns.widget_values[:5],
                    "connections_in": ns.connections_in,
                    "connections_out": ns.connections_out,
                }
                for ns in self.node_summaries
            ],
        }


class WorkflowSummarizer:
    """Summarizes ComfyUI workflow JSON files."""

    def __init__(self):
        self.link_map: dict[int, dict[str, Any]] = {}
        self.nodes_by_id: dict[int, dict[str, Any]] = {}

    def summarize_file(self, file_path: str | Path) -> WorkflowSummary:
        """Summarize a workflow file."""
        file_path = Path(file_path)
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return self.summarize_dict(data, str(file_path))

    def _extract_node_assets(self, ns) -> tuple[dict[str, str], list[str], list[str]]:
        """Extract prompts, loras, and models from a node summary."""
        prompts: dict[str, str] = {}
        loras: list[str] = []
        models: list[str] = []

        if ("CLIPTextEncode" in ns.type or "TextEncode" in ns.type) and ns.widget_values:
            prompt_text = str(ns.widget_values[0]) if ns.widget_values else ""
            prompts[ns.title or f"Node {ns.id}"] = prompt_text

        if "Lora" in ns.type:
            loras.extend(v for v in ns.widget_values if isinstance(v, str) and v.endswith(".safetensors"))

        if "Checkpoint" in ns.type or "Model" in ns.type:
            models.extend(v for v in ns.widget_values if isinstance(v, str) and v.endswith(".safetensors"))

        return prompts, loras, models

    @staticmethod
    def _extract_workflow_metadata(data: dict[str, Any]) -> dict:
        """Extract metadata from the workflow extra field."""
        extra = data.get("extra", {})
        if not isinstance(extra, dict):
            return {}
        info = extra.get("info", {})
        if not isinstance(info, dict):
            return {}
        return {k: v for k, v in info.items() if k in ["name", "description", "version", "author"]}

    def summarize_dict(self, data: dict[str, Any], source: str = "workflow") -> WorkflowSummary:
        """Summarize workflow data from a dictionary."""
        self._build_link_map(data.get("links", []))
        self._build_node_map(data.get("nodes", []))

        nodes = data.get("nodes", [])
        nodes_by_type: dict[str, int] = {}
        node_summaries = []
        all_prompts: dict[str, str] = {}
        all_loras: list[str] = []
        all_models: list[str] = []

        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type", "Unknown")
            nodes_by_type[node_type] = nodes_by_type.get(node_type, 0) + 1

            ns = self._summarize_node(node)
            node_summaries.append(ns)

            prompts, loras, models = self._extract_node_assets(ns)
            all_prompts.update(prompts)
            all_loras.extend(loras)
            all_models.extend(models)

        return WorkflowSummary(
            file_path=source,
            version=data.get("version", 0),
            node_count=len(nodes),
            link_count=len(data.get("links", [])),
            group_count=len(data.get("groups", [])),
            nodes_by_type=nodes_by_type,
            node_summaries=node_summaries,
            metadata=self._extract_workflow_metadata(data),
            prompts=all_prompts,
            loras=list(set(all_loras)),
            models=list(set(all_models)),
        )

    def _build_link_map(self, links: list[Any]):
        """Build a lookup map for links."""
        self.link_map = {}
        for link in links:
            if isinstance(link, list) and len(link) >= 6:
                self.link_map[link[0]] = {
                    "id": link[0],
                    "origin_id": link[1],
                    "origin_slot": link[2],
                    "target_id": link[3],
                    "target_slot": link[4],
                    "type": link[5],
                }
            elif isinstance(link, dict):
                link_id = link.get("id")
                if link_id is not None:
                    self.link_map[link_id] = link

    def _build_node_map(self, nodes: list[Any]):
        """Build a lookup map for nodes by ID."""
        self.nodes_by_id = {}
        for node in nodes:
            if isinstance(node, dict) and "id" in node:
                self.nodes_by_id[node["id"]] = node

    def _summarize_node(self, node: dict[str, Any]) -> NodeSummary:
        """Create a compact summary of a node."""
        node_id = node.get("id", 0)
        node_type = node.get("type", "Unknown")
        title = node.get("title")
        widgets = node.get("widgets_values", [])

        # Extract input/output names
        inputs = [inp.get("name", "") for inp in node.get("inputs", []) if isinstance(inp, dict)]
        outputs = [out.get("name", "") for out in node.get("outputs", []) if isinstance(out, dict)]

        # Find connections
        connections_in = []
        connections_out = []

        for inp in node.get("inputs", []):
            if isinstance(inp, dict):
                link_id = inp.get("link")
                if link_id is not None and link_id in self.link_map:
                    link = self.link_map[link_id]
                    origin_id = link.get("origin_id")
                    origin_node = self.nodes_by_id.get(origin_id, {})
                    origin_type = origin_node.get("type", f"#{origin_id}")
                    connections_in.append(f"{origin_type}")

        for out in node.get("outputs", []):
            if isinstance(out, dict):
                link_ids = out.get("links", [])
                if isinstance(link_ids, list):
                    for link_id in link_ids:
                        if link_id in self.link_map:
                            link = self.link_map[link_id]
                            target_id = link.get("target_id")
                            target_node = self.nodes_by_id.get(target_id, {})
                            target_type = target_node.get("type", f"#{target_id}")
                            if target_type not in connections_out:
                                connections_out.append(target_type)

        return NodeSummary(
            id=node_id,
            type=node_type,
            title=title,
            inputs=inputs,
            outputs=outputs,
            widget_values=widgets if isinstance(widgets, list) else [],
            connections_in=connections_in[:3],  # Limit for compactness
            connections_out=connections_out[:3],
        )

    def get_nodes_by_type(self, data: dict[str, Any], node_types: list[str]) -> list[dict[str, Any]]:
        """Extract full node data for specific node types."""
        nodes = data.get("nodes", [])
        result = []
        for node in nodes:
            if isinstance(node, dict) and node.get("type") in node_types:
                result.append(node)
        return result

    def get_node_by_id(self, data: dict[str, Any], node_id: int) -> dict[str, Any] | None:
        """Extract full node data for a specific node ID."""
        nodes = data.get("nodes", [])
        for node in nodes:
            if isinstance(node, dict) and node.get("id") == node_id:
                return node
        return None

    def get_prompts(self, data: dict[str, Any]) -> dict[str, str]:
        """Extract all prompts from the workflow."""
        prompts = {}
        nodes = data.get("nodes", [])
        for node in nodes:
            if isinstance(node, dict):
                node_type = node.get("type", "")
                if "CLIPTextEncode" in node_type or "TextEncode" in node_type:
                    widgets = node.get("widgets_values", [])
                    if widgets and isinstance(widgets[0], str):
                        name = node.get("title") or f"Node {node.get('id')}"
                        prompts[name] = widgets[0]
        return prompts

    def get_loras(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract all LoRA configurations from the workflow."""
        loras = []
        nodes = data.get("nodes", [])
        for node in nodes:
            if isinstance(node, dict):
                node_type = node.get("type", "")
                if "Lora" in node_type:
                    widgets = node.get("widgets_values", [])
                    title = node.get("title") or node_type
                    loras.append(
                        {
                            "node_id": node.get("id"),
                            "type": node_type,
                            "title": title,
                            "values": widgets,
                        }
                    )
        return loras


def summarize_workflow(file_path: str | Path, verbose: bool = False) -> str:
    """Convenience function to get workflow summary as text."""
    summarizer = WorkflowSummarizer()
    summary = summarizer.summarize_file(file_path)
    return summary.to_text(verbose=verbose)


def get_workflow_summary(file_path: str | Path) -> WorkflowSummary:
    """Convenience function to get WorkflowSummary object."""
    summarizer = WorkflowSummarizer()
    return summarizer.summarize_file(file_path)


def _handle_nodes_command(args, data: dict, summarizer: WorkflowSummarizer) -> int:
    """Handle --nodes command."""
    node_types = [t.strip() for t in args.nodes.split(",")]
    nodes = summarizer.get_nodes_by_type(data, node_types)
    if args.json:
        print(json.dumps(nodes, indent=2))
    else:
        for node in nodes:
            print(json.dumps(node, indent=2))
            print("---")
    return 0


def _handle_id_command(args, data: dict, summarizer: WorkflowSummarizer) -> int:
    """Handle --id command."""
    node = summarizer.get_node_by_id(data, args.id)
    if node:
        print(json.dumps(node, indent=2))
        return 0
    logger.warning("Node ID %d not found", args.id)
    return 1


def _handle_prompts_command(args, data: dict, summarizer: WorkflowSummarizer) -> int:
    """Handle --prompts command."""
    prompts = summarizer.get_prompts(data)
    if args.json:
        print(json.dumps(prompts, indent=2))
    else:
        for name, prompt in prompts.items():
            print(f"## {name}")
            print(prompt)
    return 0


def _handle_loras_command(args, data: dict, summarizer: WorkflowSummarizer) -> int:
    """Handle --loras command."""
    loras = summarizer.get_loras(data)
    if args.json:
        print(json.dumps(loras, indent=2))
    else:
        for lora in loras:
            print(f"[{lora['node_id']}] {lora['title']}: {lora['values']}")
    return 0


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Summarize ComfyUI workflow JSON files to prevent context exhaustion")
    parser.add_argument("file", help="Path to workflow JSON file")
    parser.add_argument("--nodes", "-n", help="Comma-separated node types to extract (e.g., KSampler,CLIPTextEncode)")
    parser.add_argument("--id", "-i", type=int, help="Extract specific node by ID")
    parser.add_argument("--prompts", "-p", action="store_true", help="Extract only prompts")
    parser.add_argument("--loras", "-l", action="store_true", help="Extract only LoRA configs")
    parser.add_argument("--json", "-j", action="store_true", help="Output as JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Include all node details")

    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        logger.error("File not found: %s", file_path)
        return 1

    data = json.loads(file_path.read_text(encoding="utf-8"))
    summarizer = WorkflowSummarizer()

    if args.nodes:
        return _handle_nodes_command(args, data, summarizer)
    if args.id is not None:
        return _handle_id_command(args, data, summarizer)
    if args.prompts:
        return _handle_prompts_command(args, data, summarizer)
    if args.loras:
        return _handle_loras_command(args, data, summarizer)

    # Full summary
    summary = summarizer.summarize_dict(data, str(file_path))
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
    else:
        print(summary.to_text(verbose=args.verbose))

    return 0


if __name__ == "__main__":
    sys.exit(main())
