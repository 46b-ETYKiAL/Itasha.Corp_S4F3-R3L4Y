"""Shared type definitions for the ComfyUI MCP package.

Defines immutable dataclasses and TypedDicts used across client, server,
template engine, validator, and sanitizer modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class SystemStats:
    """ComfyUI system resource statistics."""

    ram_total: int
    ram_free: int
    vram_total: int
    vram_free: int
    device_name: str
    device_type: str = "cuda"


@dataclass(frozen=True)
class ProgressEvent:
    """A progress update from ComfyUI during workflow execution."""

    prompt_id: str
    node: str
    step: int
    max_steps: int
    value: float


@dataclass(frozen=True)
class ImageOutput:
    """A single output image from a generation."""

    filename: str
    subfolder: str
    type: str


@dataclass(frozen=True)
class GenerationResult:
    """Result of a completed workflow execution."""

    prompt_id: str
    images: list[ImageOutput]
    execution_time_ms: float


@dataclass(frozen=True)
class QueueState:
    """Current state of the ComfyUI execution queue."""

    running: int
    pending: int
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class NodeSchema:
    """Schema definition for a ComfyUI node class."""

    class_type: str
    inputs: dict[str, Any]
    outputs: list[str]
    description: str = ""
    category: str = ""


@dataclass(frozen=True)
class ParamDef:
    """Definition of a template parameter."""

    name: str
    type: str
    default: Any = None
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class TemplateMeta:
    """Metadata for a workflow template."""

    name: str
    description: str
    parameters: list[ParamDef]
    media_type: str = "image"


@dataclass
class WorkflowTemplate:
    """A loaded workflow template with metadata and raw JSON."""

    meta: TemplateMeta
    raw_json: dict[str, Any]
    api_format: bool = True


@dataclass(frozen=True)
class VRAMCheck:
    """Result of a VRAM threshold check."""

    available_mb: float
    minimum_required_mb: float
    can_proceed: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationResult:
    """Result of workflow validation."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SanitizationViolation:
    """A single security violation found during sanitization."""

    field: str
    pattern: str
    description: str


@dataclass(frozen=True)
class SanitizationResult:
    """Result of input sanitization."""

    is_safe: bool
    violations: list[SanitizationViolation] = field(default_factory=list)
