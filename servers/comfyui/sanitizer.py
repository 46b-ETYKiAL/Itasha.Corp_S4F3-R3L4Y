"""Security-focused input validation for ComfyUI MCP tools.

Provides sanitization of tool parameters, workflow JSON, and file paths
to prevent command injection, code injection, path traversal, and
resource exhaustion attacks.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from .types import SanitizationResult, SanitizationViolation

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))


class InputSanitizer:
    """Security-focused input validation for all MCP tool parameters."""

    # Dangerous shell patterns
    COMMAND_INJECTION_PATTERNS: list[tuple[str, str]] = [
        (r";\s*", "semicolon command chaining"),
        (r"&&", "AND command chaining"),
        (r"\|\|", "OR command chaining"),
        (r"\|", "pipe command chaining"),
        (r"`", "backtick command substitution"),
        (r"\$\(", "dollar-paren command substitution"),
        (r"\$\{", "dollar-brace variable expansion"),
    ]

    # Dangerous Python code patterns in workflow JSON strings.
    # These are exact prefix patterns (e.g. "eval(") to avoid
    # false positives on normal English words like "evaluate" or "open".
    DANGEROUS_CODE_PATTERNS: list[str] = [
        "eval(",
        "exec(",
        "os.system(",
        "subprocess.",
        "__import__(",
        "compile(",
        "globals(",
        "locals(",
        "getattr(",
        "setattr(",
        "delattr(",
        "open(",
        "importlib.",
    ]

    # Path traversal patterns
    PATH_TRAVERSAL: list[tuple[str, str]] = [
        (r"\.\./", "dot-dot-slash traversal"),
        (r"\.\.\\", "dot-dot-backslash traversal"),
        (r"%2e%2e", "URL-encoded dot-dot traversal"),
        (r"%2f", "URL-encoded slash traversal"),
    ]

    # Numeric bounds for generation parameters
    BOUNDS: dict[str, tuple[float, float]] = {
        "width": (64, 8192),
        "height": (64, 8192),
        "steps": (1, 150),
        "cfg": (0.0, 30.0),
        "seed": (0, 2**32 - 1),
        "denoise": (0.0, 1.0),
    }

    # Allowed pattern for filenames (no path separators)
    _FILENAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]+$")

    # Allowed pattern for ComfyUI class_type values
    _CLASS_TYPE_RE = re.compile(r"^[a-zA-Z0-9_]+$")

    def sanitize_params(self, **params: Any) -> SanitizationResult:
        """Validate individual tool parameters (width, height, steps, etc.)."""
        violations: list[SanitizationViolation] = []

        for name, value in params.items():
            # Check numeric bounds
            if name in self.BOUNDS and isinstance(value, (int, float)):
                low, high = self.BOUNDS[name]
                if not (low <= value <= high):
                    violations.append(
                        SanitizationViolation(
                            field=name,
                            pattern=f"[{low}, {high}]",
                            description=(f"Value {value} out of allowed range [{low}, {high}]"),
                        )
                    )

            # Check string params for command injection patterns
            if isinstance(value, str):
                violations.extend(self._check_string(name, value))

        is_safe = len(violations) == 0
        if not is_safe:
            logger.warning("Parameter sanitization found %d violation(s)", len(violations))
        return SanitizationResult(is_safe=is_safe, violations=violations)

    def sanitize_workflow(self, workflow: dict[str, Any]) -> SanitizationResult:
        """Scan workflow JSON for security violations."""
        violations = self._walk_workflow_strings(workflow)

        # Additionally validate class_type fields at the top level
        for node_id, node_data in workflow.items():
            if isinstance(node_data, dict) and "class_type" in node_data:
                class_type = node_data["class_type"]
                if isinstance(class_type, str) and not self._CLASS_TYPE_RE.match(class_type):
                    violations.append(
                        SanitizationViolation(
                            field=f"{node_id}.class_type",
                            pattern=r"[a-zA-Z0-9_]+",
                            description=(f"class_type '{class_type}' contains disallowed characters"),
                        )
                    )

        is_safe = len(violations) == 0
        if not is_safe:
            logger.warning("Workflow sanitization found %d violation(s)", len(violations))
        return SanitizationResult(is_safe=is_safe, violations=violations)

    def sanitize_file_path(self, path: str) -> SanitizationResult:
        """Validate file paths are within allowed directories."""
        violations: list[SanitizationViolation] = []

        # Block path traversal
        path_lower = path.lower()
        for pattern, desc in self.PATH_TRAVERSAL:
            if re.search(pattern, path_lower, re.IGNORECASE):
                violations.append(
                    SanitizationViolation(
                        field="path",
                        pattern=pattern,
                        description=f"Path traversal detected: {desc}",
                    )
                )

        # Extract filename (last component after any separator)
        # Reject paths containing directory separators — only bare filenames
        if "/" in path or "\\" in path:
            violations.append(
                SanitizationViolation(
                    field="path",
                    pattern="no path separators",
                    description=("Path contains directory separators; only bare filenames are allowed"),
                )
            )
        elif not self._FILENAME_RE.match(path):
            violations.append(
                SanitizationViolation(
                    field="path",
                    pattern=r"[a-zA-Z0-9_.\-]+",
                    description=(
                        f"Filename '{path}' contains disallowed characters; "
                        "only alphanumeric, hyphens, dots, and underscores "
                        "are permitted"
                    ),
                )
            )

        is_safe = len(violations) == 0
        if not is_safe:
            logger.warning("File path sanitization found %d violation(s)", len(violations))
        return SanitizationResult(is_safe=is_safe, violations=violations)

    def _check_string(self, field: str, value: str) -> list[SanitizationViolation]:
        """Check a string value for command injection patterns."""
        violations: list[SanitizationViolation] = []
        for pattern, desc in self.COMMAND_INJECTION_PATTERNS:
            if re.search(pattern, value):
                violations.append(
                    SanitizationViolation(
                        field=field,
                        pattern=pattern,
                        description=f"Command injection pattern detected: {desc}",
                    )
                )
        return violations

    def _check_dangerous_code(self, field: str, value: str) -> list[SanitizationViolation]:
        """Check a string for dangerous code patterns (e.g. ``eval(``)."""
        violations: list[SanitizationViolation] = []
        for pattern in self.DANGEROUS_CODE_PATTERNS:
            if pattern in value:
                violations.append(
                    SanitizationViolation(
                        field=field,
                        pattern=pattern,
                        description=(f"Dangerous code pattern '{pattern}' found in workflow string"),
                    )
                )
        return violations

    def _walk_workflow_strings(self, data: Any, path: str = "") -> list[SanitizationViolation]:
        """Recursively walk workflow JSON and check all string values."""
        violations: list[SanitizationViolation] = []

        if isinstance(data, dict):
            for key, value in data.items():
                child_path = f"{path}.{key}" if path else key
                violations.extend(self._walk_workflow_strings(value, child_path))

        elif isinstance(data, list):
            for idx, item in enumerate(data):
                child_path = f"{path}[{idx}]"
                violations.extend(self._walk_workflow_strings(item, child_path))

        elif isinstance(data, str):
            # Check for dangerous code patterns
            violations.extend(self._check_dangerous_code(path, data))

            # Check for path traversal in string values
            data_lower = data.lower()
            for pattern, desc in self.PATH_TRAVERSAL:
                if re.search(pattern, data_lower, re.IGNORECASE):
                    violations.append(
                        SanitizationViolation(
                            field=path,
                            pattern=pattern,
                            description=(f"Path traversal in workflow value: {desc}"),
                        )
                    )

        return violations
