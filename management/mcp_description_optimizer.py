"""MCP Description Optimizer — Generate concise tool descriptions."""

from __future__ import annotations

import re

_MAX_DESCRIPTION_LENGTH = 80


class DescriptionOptimizer:
    """Generates concise, intent-focused tool descriptions. No LLM needed."""

    def __init__(self, max_length: int = _MAX_DESCRIPTION_LENGTH) -> None:
        self.max_length = max_length

    def optimize(self, tool_name: str, schema: dict) -> str:
        description = schema.get("description", "")
        if description:
            optimized = self._extract_first_sentence(description)
            if len(optimized) <= self.max_length:
                return optimized
            return self._truncate_at_word(optimized)
        return self._fallback_description(tool_name, schema)

    def optimize_batch(self, tools: list[tuple[str, dict]]) -> dict[str, str]:
        return {name: self.optimize(name, schema) for name, schema in tools}

    def _extract_first_sentence(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text.strip())
        match = re.match(r"^(.+?[.!?])\s", text)
        if match:
            return match.group(1).strip()
        first_line = text.split("\n")[0].strip()
        return first_line or text

    def _truncate_at_word(self, text: str) -> str:
        if len(text) <= self.max_length:
            return text
        truncated = text[: self.max_length - 3]
        last_space = truncated.rfind(" ")
        if last_space > self.max_length // 2:
            truncated = truncated[:last_space]
        return truncated.rstrip(".,;: ") + "..."

    def _fallback_description(self, tool_name: str, schema: dict) -> str:
        input_schema = schema.get("inputSchema", {})
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])
        if required:
            params = ", ".join(required[:3])
        elif properties:
            params = ", ".join(list(properties.keys())[:3])
        else:
            params = "..."
        readable_name = tool_name.replace("_", " ").replace("-", " ")
        desc = f"{readable_name}({params})"
        return desc if len(desc) <= self.max_length else self._truncate_at_word(desc)


__all__ = ["DescriptionOptimizer"]
