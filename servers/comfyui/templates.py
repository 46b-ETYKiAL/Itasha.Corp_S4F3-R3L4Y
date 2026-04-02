"""Workflow template engine for ComfyUI.

Discovers, parses, and renders parameterized ComfyUI workflow templates
from JSON files with ``{{PARAM_NAME}}`` placeholder substitution and
automatic type coercion.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from .types import ParamDef, TemplateMeta, WorkflowTemplate

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))

_TEMPLATE_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _coerce_type(value: str, target_type: str) -> Any:
    """Convert a string value to the target type.

    Args:
        value: The string value to convert.
        target_type: One of ``"int"``, ``"float"``, ``"string"``, ``"bool"``,
            ``"list"``.

    Returns:
        The coerced value.

    Raises:
        ValueError: If the conversion fails.
    """
    target_type = target_type.lower()
    if target_type == "int":
        return int(value)
    if target_type == "float":
        return float(value)
    if target_type == "bool":
        return value.lower() in ("true", "1", "yes")
    if target_type == "list":
        return json.loads(value) if value.startswith("[") else [s.strip() for s in value.split(",")]
    return value


def _parse_meta(data: dict[str, Any]) -> TemplateMeta:
    """Extract and validate the ``_meta`` field from raw template JSON.

    Args:
        data: The full parsed JSON dictionary of the template file.

    Returns:
        A validated ``TemplateMeta`` instance.

    Raises:
        ValueError: If ``_meta`` is missing or malformed.
    """
    meta_raw = data.get("_meta")
    if not meta_raw or not isinstance(meta_raw, dict):
        msg = "Template JSON missing required '_meta' field"
        raise ValueError(msg)

    name = meta_raw.get("name")
    if not name:
        msg = "_meta.name is required"
        raise ValueError(msg)

    description = meta_raw.get("description", "")
    media_type = meta_raw.get("media_type", "image")

    params: list[ParamDef] = []
    for p in meta_raw.get("parameters", []):
        params.append(
            ParamDef(
                name=p.get("name", ""),
                type=p.get("type", "string"),
                default=p.get("default"),
                required=p.get("required", True),
                description=p.get("description", ""),
            )
        )

    return TemplateMeta(
        name=name,
        description=description,
        parameters=params,
        media_type=media_type,
    )


def _substitute_params(
    data: Any,
    params: dict[str, Any],
    param_defs: list[ParamDef],
) -> Any:
    """Recursively substitute ``{{PARAM_NAME}}`` placeholders in *data*.

    - If a string value is *entirely* a placeholder (e.g. ``"{{STEPS}}"``) and
      the corresponding ``ParamDef.type`` is ``int`` or ``float``, the returned
      value is the coerced number rather than a string.
    - Mixed placeholders (e.g. ``"masterpiece, {{PROMPT}}"``) are replaced via
      string interpolation.

    Args:
        data: The (sub-)tree of the template JSON to process.
        params: User-supplied parameter values keyed by name.
        param_defs: Parameter definitions from the template meta.

    Returns:
        A new object with all placeholders resolved.
    """
    type_map: dict[str, str] = {pd.name: pd.type for pd in param_defs}

    if isinstance(data, str):
        # Check for a pure-placeholder value first.
        pure_match = _TEMPLATE_PARAM_RE.fullmatch(data)
        if pure_match:
            key = pure_match.group(1)
            if key in params:
                target = type_map.get(key, "string")
                raw = params[key]
                if target in ("int", "float") and isinstance(raw, str):
                    return _coerce_type(raw, target)
                if target in ("int", "float") and isinstance(raw, (int, float)):
                    return raw
                return str(raw)
            return data  # leave unresolved if not supplied

        # Mixed placeholder replacement.
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in params:
                return str(params[key])
            return match.group(0)

        return _TEMPLATE_PARAM_RE.sub(_replace, data)

    if isinstance(data, dict):
        return {k: _substitute_params(v, params, param_defs) for k, v in data.items()}

    if isinstance(data, list):
        return [_substitute_params(item, params, param_defs) for item in data]

    return data


def _is_ui_format(data: dict[str, Any]) -> bool:
    """Return ``True`` if *data* looks like ComfyUI UI-format (has a ``nodes`` array)."""
    return isinstance(data.get("nodes"), list)


# ---------------------------------------------------------------------------
# TemplateRegistry
# ---------------------------------------------------------------------------


class TemplateRegistry:
    """Registry that discovers, caches, and renders ComfyUI workflow templates.

    Templates are JSON files stored in *template_dir* (default
    ``.claude/config/comfyui-workflows/``).  Each file must contain a
    ``_meta`` key describing the template's parameters.

    Args:
        template_dir: Path to the directory containing template JSON files.
            When ``None``, defaults to ``.claude/config/comfyui-workflows/``
            relative to the project root.
    """

    def __init__(self, template_dir: str | None = None) -> None:
        if template_dir is None:
            project_root = Path(__file__).resolve().parents[2]
            template_dir = str(project_root / "config" / "comfyui-workflows")

        self._template_dir = Path(template_dir)
        self._cache: dict[str, WorkflowTemplate] = {}
        self.discover()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self) -> int:
        """Scan *template_dir* for ``.json`` files and cache their metadata.

        Returns:
            The number of templates successfully discovered.
        """
        self._cache.clear()

        if not self._template_dir.is_dir():
            logger.warning("Template directory does not exist: %s", self._template_dir)
            return 0

        count = 0
        for path in sorted(self._template_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Skipping %s: %s", path.name, exc)
                continue

            try:
                meta = _parse_meta(raw)
            except ValueError as exc:
                logger.warning("Skipping %s: %s", path.name, exc)
                continue

            api_format = not _is_ui_format(raw)
            if not api_format:
                logger.info(
                    "Template '%s' is in UI format; attempting conversion",
                    meta.name,
                )
                try:
                    from comfyui_workflow_schema.converters import (
                        convert_workflow_to_v1,
                    )

                    raw = convert_workflow_to_v1(raw)
                    api_format = True
                    logger.info("Converted '%s' from UI to API format", meta.name)
                except ImportError:
                    logger.warning(
                        "comfyui_workflow_schema.converters not available; UI-format template '%s' stored as-is",
                        meta.name,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to convert '%s' to API format: %s",
                        meta.name,
                        exc,
                    )

            # Strip _meta from the stored raw JSON so it won't appear in output.
            workflow_data = {k: v for k, v in raw.items() if k != "_meta"}

            self._cache[meta.name] = WorkflowTemplate(
                meta=meta,
                raw_json=workflow_data,
                api_format=api_format,
            )
            count += 1

        logger.info("Discovered %d template(s) in %s", count, self._template_dir)
        return count

    def list_templates(self) -> list[TemplateMeta]:
        """Return metadata for all discovered templates.

        Returns:
            A list of ``TemplateMeta`` instances.
        """
        return [tpl.meta for tpl in self._cache.values()]

    def get_template(self, name: str) -> WorkflowTemplate:
        """Get a specific template by name.

        Args:
            name: The template name (from ``_meta.name``).

        Returns:
            The matching ``WorkflowTemplate``.

        Raises:
            KeyError: If no template with *name* exists.
        """
        if name not in self._cache:
            msg = f"Template '{name}' not found. Available: {list(self._cache)}"
            raise KeyError(msg)
        return self._cache[name]

    def render_template(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Render a template with parameter substitution.

        Steps:
        1. Retrieve the template by *name*.
        2. Deep-copy the raw JSON.
        3. Validate that all required parameters are provided.
        4. Walk all string values, replacing ``{{PARAM_NAME}}`` placeholders.
        5. Apply type coercion where the param schema expects int/float.
        6. Return the API-format workflow JSON ready for ``/prompt``.

        Args:
            name: The template name.
            params: A mapping of parameter names to values.

        Returns:
            A workflow dict ready to POST to ComfyUI's ``/prompt`` endpoint.

        Raises:
            KeyError: If the template is not found.
            ValueError: If required parameters are missing.
        """
        template = self.get_template(name)
        param_defs = template.meta.parameters

        # Validate required params.
        required_names = {pd.name for pd in param_defs if pd.required}
        missing = required_names - set(params)
        if missing:
            msg = f"Missing required parameter(s) for template '{name}': {sorted(missing)}"
            raise ValueError(msg)

        # Warn about extra params.
        known_names = {pd.name for pd in param_defs}
        extra = set(params) - known_names
        if extra:
            logger.warning(
                "Ignoring unknown parameter(s) for template '%s': %s",
                name,
                sorted(extra),
            )

        # Apply defaults for missing optional params.
        effective: dict[str, Any] = {}
        for pd in param_defs:
            if pd.name in params:
                effective[pd.name] = params[pd.name]
            elif pd.default is not None:
                effective[pd.name] = pd.default

        rendered = _substitute_params(
            copy.deepcopy(template.raw_json),
            effective,
            param_defs,
        )
        return rendered  # type: ignore[return-value]
