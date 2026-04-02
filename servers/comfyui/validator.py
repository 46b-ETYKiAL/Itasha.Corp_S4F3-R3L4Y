"""Pre-submission workflow validation for ComfyUI API-format workflows.

Validates node types against a registry, checks connections, detects
disconnected subgraphs, and suggests corrections via RapidFuzz fuzzy matching.
"""

from __future__ import annotations

import logging
import sys
from collections import deque
from typing import Any

from rapidfuzz import fuzz

from .types import NodeSchema, ValidationResult

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))
logger.setLevel(logging.DEBUG)

# Node class_types that produce final output (images, video, etc.)
OUTPUT_TYPES: frozenset[str] = frozenset(
    {
        "SaveImage",
        "PreviewImage",
        "SaveAnimatedWEBP",
        "SaveAnimatedPNG",
        "VHS_VideoCombine",
    }
)

_FUZZY_THRESHOLD = 80


class WorkflowValidator:
    """Validates ComfyUI workflows before submission."""

    def __init__(self, node_registry: dict[str, NodeSchema] | None = None) -> None:
        """Initialise the validator.

        Args:
            node_registry: class_type -> NodeSchema mapping from /object_info.
                If *None*, node-type validation is skipped (degraded mode).
        """
        self._registry_cache: dict[str, NodeSchema] = node_registry or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_registry(self, registry: dict[str, NodeSchema]) -> None:
        """Replace the node registry cache (e.g. from /object_info response)."""
        self._registry_cache = dict(registry)

    def validate(self, workflow: dict[str, Any]) -> ValidationResult:
        """Validate a workflow in ComfyUI API format.

        The API format is a flat dict of string node IDs ("1", "2", ...)
        mapping to node dicts with at least ``class_type`` and ``inputs``.

        Checks performed:
            1. Non-empty workflow with at least one node.
            2. All ``class_type`` values exist in the registry (with fuzzy
               suggestions when they do not).
            3. At least one output node is present.
            4. Linked node references are valid (node exists, output index
               in range).
            5. No disconnected subgraphs (nodes unreachable from outputs).

        Returns:
            A :class:`ValidationResult` with errors, warnings, and
            suggestions.
        """
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []

        # --- 1. Basic structure ---
        if not workflow:
            return ValidationResult(
                valid=False,
                errors=["Workflow is empty — at least one node is required."],
            )

        if not isinstance(workflow, dict):
            return ValidationResult(
                valid=False,
                errors=["Workflow must be a dict (API format)."],
            )

        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                errors.append(f"Node '{node_id}' is not a dict.")
            elif "class_type" not in node:
                errors.append(f"Node '{node_id}' is missing required key 'class_type'.")

        if errors:
            return ValidationResult(valid=False, errors=errors, warnings=warnings, suggestions=suggestions)

        # --- 2. Node types ---
        type_errors, type_suggestions = self._check_node_types(workflow)
        errors.extend(type_errors)
        suggestions.extend(type_suggestions)

        # --- 3. Output nodes ---
        output_errors = self._check_output_nodes(workflow)
        errors.extend(output_errors)

        # --- 4. Connection validity ---
        conn_errors = self._check_connections(workflow)
        errors.extend(conn_errors)

        # --- 5. Disconnected subgraphs ---
        disc_warnings = self._check_disconnected(workflow)
        warnings.extend(disc_warnings)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_node_types(self, workflow: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Check all class_type values against the registry.

        Returns:
            A tuple of (errors, suggestions).
        """
        errors: list[str] = []
        suggestions: list[str] = []

        if not self._registry_cache:
            logger.debug("Node registry is empty — skipping node-type validation (degraded mode).")
            return errors, suggestions

        registry_keys = list(self._registry_cache.keys())

        for node_id, node in workflow.items():
            class_type: str = node.get("class_type", "")
            if not class_type:
                continue  # already caught by structural check

            if class_type in self._registry_cache:
                continue

            errors.append(f"Node '{node_id}': unknown class_type '{class_type}'.")

            best_score = 0.0
            best_match = ""
            for known_type in registry_keys:
                score = max(
                    fuzz.ratio(class_type, known_type),
                    fuzz.partial_ratio(class_type, known_type),
                )
                if score > best_score:
                    best_score = score
                    best_match = known_type

            if best_score > _FUZZY_THRESHOLD:
                suggestions.append(f"Node '{node_id}': Did you mean '{best_match}'? (similarity: {best_score:.0f}%)")

        return errors, suggestions

    def _check_output_nodes(self, workflow: dict[str, Any]) -> list[str]:
        """Return errors if no output node is present."""
        for node in workflow.values():
            if node.get("class_type") in OUTPUT_TYPES:
                return []

        return [f"Workflow has no output node. Expected at least one of: {', '.join(sorted(OUTPUT_TYPES))}."]

    def _check_connections(self, workflow: dict[str, Any]) -> list[str]:
        """Validate that linked node IDs and output indices are valid.

        In ComfyUI API format, an input that references another node is
        encoded as a list ``[node_id_str, output_index]``.
        """
        errors: list[str] = []

        for node_id, node in workflow.items():
            inputs: dict[str, Any] = node.get("inputs", {})
            if not isinstance(inputs, dict):
                errors.append(f"Node '{node_id}': 'inputs' is not a dict.")
                continue

            for input_name, value in inputs.items():
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    continue  # literal value, not a link

                ref_id, output_idx = value
                ref_id_str = str(ref_id)

                if ref_id_str not in workflow:
                    errors.append(f"Node '{node_id}' input '{input_name}' references non-existent node '{ref_id_str}'.")
                    continue

                if not isinstance(output_idx, int) or output_idx < 0:
                    errors.append(f"Node '{node_id}' input '{input_name}' has invalid output index {output_idx!r}.")
                    continue

                # Validate output index against registry if available.
                ref_class = workflow[ref_id_str].get("class_type", "")
                if ref_class and ref_class in self._registry_cache:
                    schema = self._registry_cache[ref_class]
                    if output_idx >= len(schema.outputs):
                        errors.append(
                            f"Node '{node_id}' input '{input_name}' references "
                            f"output index {output_idx} of node '{ref_id_str}' "
                            f"('{ref_class}'), but it only has "
                            f"{len(schema.outputs)} output(s)."
                        )

        return errors

    def _check_disconnected(self, workflow: dict[str, Any]) -> list[str]:
        """Warn about nodes with no path to an output node.

        Performs a reverse BFS from every output node, following
        connections backward.  Any node not reached is disconnected.
        """
        if not workflow:
            return []

        output_ids: set[str] = set()
        for node_id, node in workflow.items():
            if node.get("class_type") in OUTPUT_TYPES:
                output_ids.add(node_id)

        if not output_ids:
            return []  # already reported by _check_output_nodes

        # Build reverse adjacency: for each node, which nodes feed into it?
        # We want to walk *backward* from outputs, so we need to know
        # "which nodes does this node depend on?" and traverse that.
        # Actually we want: from an output node, which nodes eventually
        # connect to it?  We traverse inputs (dependencies) backward.
        reachable: set[str] = set()
        queue: deque[str] = deque(output_ids)

        while queue:
            current = queue.popleft()
            if current in reachable:
                continue
            reachable.add(current)

            node = workflow.get(current)
            if node is None:
                continue

            inputs: dict[str, Any] = node.get("inputs", {})
            if not isinstance(inputs, dict):
                continue

            for value in inputs.values():
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    ref_id_str = str(value[0])
                    if ref_id_str in workflow and ref_id_str not in reachable:
                        queue.append(ref_id_str)

        disconnected = set(workflow.keys()) - reachable
        if not disconnected:
            return []

        return [
            f"Node(s) {', '.join(sorted(disconnected))} have no path to an "
            "output node and will not contribute to the result."
        ]
