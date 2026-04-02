"""FastMCP server exposing ComfyUI image generation as MCP tools.

Provides 36 tools for image generation, model discovery, queue management,
image operations, resource management, custom node authoring, publishing,
image search, real-time monitoring, training, quality optimization, and
extended control via the Model Context Protocol.
All logging uses stderr -- stdout is reserved for JSON-RPC transport.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import asdict
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .client import ComfyUIClient
from .templates import TemplateRegistry
from .types import GenerationResult, VRAMCheck

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("comfyui", instructions="ComfyUI image generation server")

_client: ComfyUIClient | None = None
_templates: TemplateRegistry | None = None

_VRAM_THRESHOLDS: dict[str, float] = {
    "sd15": 4_000,
    "sd1.5": 4_000,
    "sdxl": 8_000,
    "flux": 12_000,
    "cascade": 16_000,
}
_DEFAULT_VRAM_THRESHOLD = 6_000
_TEMPLATE_PARAM_RE = re.compile(r"\{\{(\w+)\}\}")


def _get_client() -> ComfyUIClient:
    """Return the shared ``ComfyUIClient``, creating it on first use."""
    global _client
    if _client is None:
        _client = ComfyUIClient()
    return _client


def _get_templates() -> TemplateRegistry:
    """Return the shared ``TemplateRegistry``, creating it on first use."""
    global _templates
    if _templates is None:
        _templates = TemplateRegistry()
    return _templates


def _result_to_dict(result: GenerationResult) -> dict[str, Any]:
    """Convert a ``GenerationResult`` dataclass to a JSON-safe dict."""
    return asdict(result)


def _build_standard_workflow(
    prompt: str,
    negative_prompt: str,
    model: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
) -> dict[str, Any]:
    """Build a minimal txt2img API-format workflow dict."""
    ckpt = model or "v1-5-pruned-emaonly.safetensors"
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative_prompt, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "mcp_gen", "images": ["8", 0]}},
    }


# -- Generation tools -------------------------------------------------------


@mcp.tool()
async def generate_image(
    prompt: str,
    negative_prompt: str = "",
    model: str = "",
    width: int = 512,
    height: int = 512,
    steps: int = 20,
    cfg: float = 7.0,
    seed: int = -1,
    template: str = "",
) -> dict[str, Any]:
    """Generate an image from a text prompt (txt2img).

    Builds a standard Stable Diffusion workflow or uses a named template.
    Returns image paths and generation metadata.
    """
    client = _get_client()
    try:
        if template:
            registry = _get_templates()
            params: dict[str, Any] = {
                "PROMPT": prompt,
                "NEGATIVE_PROMPT": negative_prompt,
                "WIDTH": width,
                "HEIGHT": height,
                "STEPS": steps,
                "CFG": cfg,
                "SEED": seed,
            }
            if model:
                params["MODEL"] = model
            workflow = registry.render_template(template, params)
        else:
            workflow = _build_standard_workflow(
                prompt,
                negative_prompt,
                model,
                width,
                height,
                steps,
                cfg,
                seed,
            )
        prompt_id = await client.submit_workflow(workflow)
        await client.get_progress(prompt_id)
        result = await client.get_result(prompt_id)
        return _result_to_dict(result)
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc
    except (KeyError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def run_workflow(
    workflow_json: str,
    params: str = "",
) -> dict[str, Any]:
    """Execute an arbitrary ComfyUI workflow from JSON.

    Accepts a full workflow as a JSON string. If the workflow contains
    ``{{PARAM}}`` placeholders, supply matching values via *params* as a
    JSON object string. Auto-detects UI/API format.
    """
    client = _get_client()
    try:
        workflow: dict[str, Any] = json.loads(workflow_json)
    except json.JSONDecodeError as exc:
        raise ToolError(f"Invalid workflow JSON: {exc}") from exc

    param_dict: dict[str, Any] = {}
    if params:
        try:
            param_dict = json.loads(params)
        except json.JSONDecodeError as exc:
            raise ToolError(f"Invalid params JSON: {exc}") from exc

    try:
        if param_dict:
            raw_str = json.dumps(workflow)
            if _TEMPLATE_PARAM_RE.search(raw_str):
                for key, value in param_dict.items():
                    raw_str = raw_str.replace(f"{{{{{key}}}}}", str(value))
                workflow = json.loads(raw_str)
        prompt_id = await client.submit_workflow(workflow)
        await client.get_progress(prompt_id)
        result = await client.get_result(prompt_id)
        return _result_to_dict(result)
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


@mcp.tool()
async def get_result(prompt_id: str) -> dict[str, Any]:
    """Retrieve generation results for a previously submitted workflow.

    Returns images, execution time, and metadata for the given prompt ID.
    """
    client = _get_client()
    try:
        result = await client.get_result(prompt_id)
        return _result_to_dict(result)
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


# -- Discovery tools --------------------------------------------------------


@mcp.tool()
async def list_models(folder: str = "checkpoints") -> list[str]:
    """List available models in a ComfyUI model folder.

    Common folders: checkpoints, loras, vae, controlnet, embeddings.
    """
    client = _get_client()
    try:
        return await client.list_models(folder)
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


@mcp.tool()
async def list_templates() -> list[dict[str, Any]]:
    """List all registered workflow templates with their parameter schemas."""
    registry = _get_templates()
    return [asdict(t) for t in registry.list_templates()]


@mcp.tool()
async def get_template(name: str) -> dict[str, Any]:
    """Get a specific workflow template by name with its parameter schema."""
    registry = _get_templates()
    try:
        tpl = registry.get_template(name)
        return {
            "name": tpl.meta.name,
            "description": tpl.meta.description,
            "media_type": tpl.meta.media_type,
            "parameters": [asdict(p) for p in tpl.meta.parameters],
            "workflow_json": tpl.raw_json,
            "api_format": tpl.api_format,
        }
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool()
async def get_node_info(node_class: str = "") -> dict[str, Any]:
    """Get schema information for ComfyUI node classes.

    Empty node_class returns all available nodes; a specific class returns
    its full input/output schema.
    """
    client = _get_client()
    try:
        nodes = await client.get_node_info(node_class or None)
        return {name: asdict(schema) for name, schema in nodes.items()}
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


# -- Queue management tools -------------------------------------------------


@mcp.tool()
async def get_queue() -> dict[str, Any]:
    """Get the current ComfyUI execution queue state.

    Returns running/pending counts and queued item details.
    """
    client = _get_client()
    try:
        return asdict(await client.get_queue())
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


@mcp.tool()
async def cancel_job(prompt_id: str) -> dict[str, Any]:
    """Cancel a queued or running ComfyUI job by prompt ID."""
    client = _get_client()
    try:
        success = await client.cancel_job(prompt_id)
        return {"prompt_id": prompt_id, "cancelled": success}
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


@mcp.tool()
async def get_status() -> dict[str, Any]:
    """Get ComfyUI server health, queue state, and system stats."""
    client = _get_client()
    try:
        healthy = await client.health_check()
    except ConnectionError:
        return {"healthy": False, "error": "Cannot connect to ComfyUI"}

    result: dict[str, Any] = {"healthy": healthy}
    if healthy:
        try:
            result["system"] = asdict(await client.get_system_stats())
            result["queue"] = asdict(await client.get_queue())
        except ConnectionError as exc:
            result["warning"] = f"Partial status: {exc}"
    return result


# -- Image operation tools ---------------------------------------------------


@mcp.tool()
async def upload_image(file_path: str) -> dict[str, Any]:
    """Upload a local image file to ComfyUI for use in workflows.

    The uploaded image can be referenced in img2img, ControlNet, or
    inpainting workflows.
    """
    client = _get_client()
    try:
        filename = await client.upload_image(file_path)
        return {"filename": filename, "uploaded": True}
    except FileNotFoundError as exc:
        raise ToolError(f"File not found: {file_path}") from exc
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


@mcp.tool()
async def get_image(
    filename: str,
    subfolder: str = "",
    image_type: str = "output",
) -> dict[str, str]:
    """Get the URL for a generated or uploaded image.

    The image_type is one of: output, input, temp.
    """
    client = _get_client()
    base = client.base_url
    qs = f"filename={filename}&type={image_type}"
    if subfolder:
        qs += f"&subfolder={subfolder}"
    return {
        "url": f"{base}/view?{qs}",
        "filename": filename,
        "subfolder": subfolder,
        "type": image_type,
    }


# -- Resource management tools ----------------------------------------------


@mcp.tool()
async def get_system_stats() -> dict[str, Any]:
    """Get RAM, VRAM, and GPU device info from ComfyUI."""
    client = _get_client()
    try:
        return asdict(await client.get_system_stats())
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


@mcp.tool()
async def check_vram(model_family: str = "sdxl") -> dict[str, Any]:
    """Pre-flight VRAM check before loading a model.

    Compares available VRAM against thresholds for the model family.
    Supported: sd15, sd1.5, sdxl, flux, cascade.
    """
    client = _get_client()
    threshold = _VRAM_THRESHOLDS.get(model_family.lower(), _DEFAULT_VRAM_THRESHOLD)
    try:
        stats = await client.get_system_stats()
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc

    available_mb = stats.vram_free / (1024 * 1024) if stats.vram_free else 0
    can_proceed = available_mb >= threshold
    warnings: list[str] = []
    if not can_proceed:
        warnings.append(
            f"Insufficient VRAM: {available_mb:.0f} MB available, {threshold:.0f} MB required for {model_family}"
        )
    if available_mb < threshold * 1.2:
        warnings.append("VRAM headroom is tight; consider clearing VRAM first")
    return asdict(
        VRAMCheck(
            available_mb=available_mb,
            minimum_required_mb=threshold,
            can_proceed=can_proceed,
            warnings=warnings,
        )
    )


@mcp.tool()
async def clear_vram() -> dict[str, Any]:
    """Unload all models and free GPU VRAM.

    Use before switching model families or when VRAM is low.
    """
    client = _get_client()
    try:
        return {"cleared": await client.clear_vram()}
    except ConnectionError as exc:
        raise ToolError(f"Cannot connect to ComfyUI: {exc}") from exc


# Register extended tools (node authoring, publishing, cache, monitoring,
# training, quality, control) from the companion module.
from . import extended_tools as _extended_tools  # noqa: F401

if __name__ == "__main__":
    mcp.run(transport="stdio")
