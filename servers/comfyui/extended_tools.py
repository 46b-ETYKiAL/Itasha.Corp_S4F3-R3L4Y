"""Extended MCP tool registrations for ComfyUI deep integration.

Registers 21 additional tools on the shared ``mcp`` FastMCP instance
covering custom node authoring, publishing, image cache, monitoring,
training, quality optimization, and extended control.

All tools use lazy imports to avoid heavy startup cost.
"""

from __future__ import annotations

import json
from typing import Any

from fastmcp.exceptions import ToolError

from .server import mcp

# -- Node authoring tools ----------------------------------------------------


@mcp.tool()
async def create_custom_node(spec_json: str) -> dict[str, Any]:
    """Create a ComfyUI custom node from a JSON specification."""
    from ..comfyui_node_authoring import create_custom_node as _create

    try:
        result = _create(spec_json)
        return {"source": result.source, "path": str(result.file_path), "valid": result.valid}
    except (ValueError, json.JSONDecodeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def validate_custom_node(node_path: str) -> dict[str, Any]:
    """Validate a generated ComfyUI custom node file."""
    from ..comfyui_node_authoring import validate_node as _validate

    try:
        result = _validate(node_path)
        return {"passed": result.passed, "checks": result.checks, "errors": result.errors}
    except FileNotFoundError as exc:
        raise ToolError(f"Node file not found: {node_path}") from exc


# -- Node publishing tools ---------------------------------------------------


@mcp.tool()
async def package_node(node_dir: str, config_json: str) -> dict[str, Any]:
    """Package a ComfyUI custom node for distribution."""
    from ..comfyui_node_publishing import PackageConfig
    from ..comfyui_node_publishing import package_node as _package

    try:
        config = PackageConfig(**json.loads(config_json))
        path = _package(node_dir, config)
        return {"package_path": path}
    except (ValueError, json.JSONDecodeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def validate_node_package(package_dir: str) -> dict[str, Any]:
    """Validate a packaged ComfyUI node for completeness."""
    from ..comfyui_node_publishing import validate_package as _validate

    issues = _validate(package_dir)
    return {"valid": len(issues) == 0, "issues": issues}


@mcp.tool()
async def publish_node(package_dir: str, target: str = "registry") -> dict[str, str]:
    """Get publishing instructions for a packaged node."""
    from ..comfyui_node_publishing import publish_node as _publish

    try:
        instructions = _publish(package_dir, target)
        return {"instructions": instructions}
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


# -- Image cache tools -------------------------------------------------------


@mcp.tool()
async def search_images(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search cached ComfyUI images by prompt, model, or metadata."""
    from ..comfyui_image_cache import search_images as _search

    return await _search(query, limit)


@mcp.tool()
async def get_image_metadata(image_id: int) -> dict[str, Any]:
    """Get full provenance metadata for a cached image."""
    from ..comfyui_image_cache import get_image_metadata as _get_meta

    result = await _get_meta(image_id)
    if result is None:
        raise ToolError(f"Image not found: {image_id}")
    return result


@mcp.tool()
async def get_recent_images(count: int = 20) -> list[dict[str, Any]]:
    """Return the most recently generated/cached images."""
    from ..comfyui_image_cache import get_recent_images as _get_recent

    return await _get_recent(count)


# -- Monitoring tools --------------------------------------------------------


@mcp.tool()
async def start_monitoring(host: str = "127.0.0.1", port: int = 8188) -> dict[str, Any]:
    """Start real-time monitoring of a ComfyUI instance."""
    from ..comfyui_monitoring import start_monitoring as _start

    return await _start(host, port)


@mcp.tool()
async def stop_monitoring() -> dict[str, Any]:
    """Stop the ComfyUI monitoring service."""
    from ..comfyui_monitoring import stop_monitoring as _stop

    return await _stop()


@mcp.tool()
async def get_monitoring_state() -> dict[str, Any]:
    """Get the current aggregated monitoring state."""
    from ..comfyui_monitoring import get_monitoring_state as _get_state

    state = _get_state()
    return {
        "status": state.status,
        "progress_pct": state.progress_pct,
        "queue_remaining": state.queue_remaining,
    }


# -- Training tools ----------------------------------------------------------


@mcp.tool()
async def prepare_training_dataset(
    image_dir: str,
    target_resolution: int = 1024,
) -> dict[str, Any]:
    """Prepare an image dataset for LoRA/training."""
    from ..comfyui_training import prepare_dataset as _prepare

    stats = await _prepare(image_dir, target_resolution=target_resolution)
    return {
        "total": stats.total,
        "valid": stats.valid,
        "skipped": stats.skipped,
        "output_dir": stats.output_dir,
    }


@mcp.tool()
async def start_lora_training(config_json: str) -> dict[str, str]:
    """Start a LoRA training job."""
    from ..comfyui_training import TrainingConfig
    from ..comfyui_training import start_training as _start

    try:
        config = TrainingConfig(**json.loads(config_json))
        job_id = await _start(config)
        return {"job_id": job_id}
    except (ValueError, json.JSONDecodeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def get_training_progress(job_id: str) -> dict[str, Any]:
    """Get progress for a running training job."""
    from ..comfyui_training import get_training_progress as _progress

    progress = _progress(job_id)
    if progress is None:
        return {"status": "unknown", "job_id": job_id}
    return {
        "step": progress.step,
        "total": progress.total_steps,
        "loss": progress.loss,
        "eta_seconds": progress.eta_seconds,
    }


@mcp.tool()
async def stop_training(job_id: str) -> dict[str, Any]:
    """Stop a running training job."""
    from ..comfyui_training import stop_training as _stop

    stopped = await _stop(job_id)
    return {"job_id": job_id, "stopped": stopped}


# -- Quality optimization tools ----------------------------------------------


@mcp.tool()
async def get_optimal_settings(model: str) -> dict[str, Any]:
    """Get optimal generation settings for a model checkpoint."""
    from ..comfyui_quality import get_optimal_settings as _settings

    preset = _settings(model)
    return {
        "sampler": preset.sampler,
        "scheduler": preset.scheduler,
        "cfg": preset.cfg,
        "steps": preset.steps,
        "model_family": preset.model_family,
    }


@mcp.tool()
async def enhance_prompt(prompt: str, model: str) -> dict[str, str]:
    """Enhance a prompt with model-aware quality tags."""
    from ..comfyui_quality import enhance_prompt as _enhance

    return {"original": prompt, "enhanced": _enhance(prompt, model)}


@mcp.tool()
async def build_upscale_workflow(
    image_path: str,
    scale: int = 4,
    content_type: str = "photo",
) -> dict[str, Any]:
    """Build a ComfyUI upscale workflow for an image."""
    from ..comfyui_quality import build_upscale_workflow as _upscale

    return _upscale(image_path, scale, content_type)


# -- Extended control tools --------------------------------------------------


@mcp.tool()
async def manage_workflow_templates(action: str, **kwargs: Any) -> Any:
    """Manage workflow templates (list, get, import, export, render, save, delete)."""
    from ..comfyui_control import manage_templates as _manage

    try:
        return _manage(action, **kwargs)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def batch_generate(config_json: str) -> dict[str, Any]:
    """Run a batch generation with parameter/seed sweeping."""
    from ..comfyui_control import BatchConfig
    from ..comfyui_control import batch_generate as _batch

    try:
        config = BatchConfig(**json.loads(config_json))
        result = await _batch(config)
        return {
            "total": result.total,
            "succeeded": result.succeeded,
            "failed": result.failed,
            "outputs": result.outputs,
        }
    except (ValueError, json.JSONDecodeError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def server_control(action: str, comfyui_path: str | None = None) -> Any:
    """Control the ComfyUI server (start, stop, restart, status, health)."""
    from ..comfyui_control import server_control as _control

    try:
        return await _control(action, comfyui_path)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
