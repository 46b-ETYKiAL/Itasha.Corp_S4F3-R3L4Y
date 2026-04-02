"""VRAM threshold checker for pre-flight workflow validation.

ComfyUI v0.18.0+ uses Dynamic VRAM with a custom PyTorch allocator,
making static prediction unreliable. This module uses threshold-based
checks to validate minimum VRAM requirements before workflow execution.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import sys

from .types import SystemStats, VRAMCheck

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))

# Minimum VRAM thresholds in MB per model family
VRAM_THRESHOLDS: dict[str, float] = {
    "sd15_fp16": 2048,  # SD 1.5 fp16: min 2GB
    "sd15_fp32": 4096,  # SD 1.5 fp32: min 4GB
    "sdxl_fp16": 5120,  # SDXL fp16: min 5GB
    "sdxl_fp32": 10240,  # SDXL fp32: min 10GB
    "flux_fp8": 8192,  # Flux fp8: min 8GB
    "flux_fp16": 12288,  # Flux fp16: min 12GB
    "flux_fp4": 5120,  # Flux NVFP4 (RTX 50-series): min 5GB
}

# Add-on VRAM costs in MB
ADDON_COSTS: dict[str, float] = {
    "controlnet": 1536,  # +1.5GB
    "ip_adapter": 1024,  # +1GB
    "lora": 256,  # +256MB per LoRA
}

# Patterns for guessing model family from filename
_FAMILY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"flux.*fp4|fp4.*flux|nvfp4", re.IGNORECASE), "flux_fp4"),
    (re.compile(r"flux.*fp8|fp8.*flux", re.IGNORECASE), "flux_fp8"),
    (re.compile(r"flux.*fp16|fp16.*flux|flux", re.IGNORECASE), "flux_fp16"),
    (re.compile(r"sdxl.*fp32|fp32.*sdxl", re.IGNORECASE), "sdxl_fp32"),
    (re.compile(r"sdxl|sd_xl|stable.?diffusion.?xl", re.IGNORECASE), "sdxl_fp16"),
    (re.compile(r"sd.?1\.?5.*fp32|fp32.*sd.?1\.?5", re.IGNORECASE), "sd15_fp32"),
    (re.compile(r"sd.?1\.?5|stable.?diffusion.?1", re.IGNORECASE), "sd15_fp16"),
]

# Warn when available VRAM is within this fraction of the threshold
_MARGIN_FRACTION = 0.20


class VRAMEstimator:
    """Threshold-based VRAM checker for pre-flight validation.

    Uses minimum VRAM thresholds per model family rather than attempting
    exact memory prediction, which is unreliable under ComfyUI's dynamic
    VRAM allocator.
    """

    def check(
        self,
        system_stats: SystemStats,
        model_family: str = "sdxl_fp16",
        addons: list[str] | None = None,
    ) -> VRAMCheck:
        """Check if available VRAM meets minimum threshold.

        Args:
            system_stats: Current system resource statistics.
            model_family: Model family identifier (e.g. ``sdxl_fp16``).
            addons: Optional list of add-on identifiers that increase
                VRAM requirements (e.g. ``["controlnet", "lora"]``).

        Returns:
            VRAMCheck with availability, required minimum, proceed flag,
            and any warnings.
        """
        warnings: list[str] = []
        addons = addons or []

        # Look up base threshold
        base_threshold = VRAM_THRESHOLDS.get(model_family)
        if base_threshold is None:
            warnings.append(f"Unknown model family '{model_family}'; using sdxl_fp16 threshold as fallback")
            base_threshold = VRAM_THRESHOLDS["sdxl_fp16"]

        # Add addon costs
        addon_total = 0.0
        for addon in addons:
            cost = ADDON_COSTS.get(addon)
            if cost is None:
                warnings.append(f"Unknown addon '{addon}'; skipping cost")
            else:
                addon_total += cost

        minimum_required = base_threshold + addon_total
        available = float(system_stats.vram_free)

        # Determine if we can proceed
        can_proceed = available >= minimum_required

        # Warn if within margin even when passing
        if can_proceed:
            margin = available - minimum_required
            margin_threshold = minimum_required * _MARGIN_FRACTION
            if margin < margin_threshold:
                warnings.append(
                    f"VRAM headroom is tight: {margin:.0f} MB free "
                    f"above {minimum_required:.0f} MB minimum "
                    f"(recommended margin: {margin_threshold:.0f} MB)"
                )
        else:
            deficit = minimum_required - available
            warnings.append(
                f"Insufficient VRAM: need {minimum_required:.0f} MB "
                f"but only {available:.0f} MB available "
                f"(deficit: {deficit:.0f} MB)"
            )

        logger.debug(
            "VRAM check: family=%s available=%.0f required=%.0f proceed=%s",
            model_family,
            available,
            minimum_required,
            can_proceed,
        )

        return VRAMCheck(
            available_mb=available,
            minimum_required_mb=minimum_required,
            can_proceed=can_proceed,
            warnings=warnings,
        )

    def estimate_family(self, model_name: str) -> str:
        """Guess model family from a model filename.

        Matches against known patterns in the filename to determine
        the likely model family. Falls back to ``sdxl_fp16`` when no
        pattern matches.

        Args:
            model_name: Model filename (e.g. ``dreamshaper_8.safetensors``).

        Returns:
            Model family identifier string.
        """
        for pattern, family in _FAMILY_PATTERNS:
            if pattern.search(model_name):
                return family

        # Default fallback — most common consumer setup
        logger.debug(
            "Could not determine family for '%s'; defaulting to sd15_fp16",
            model_name,
        )
        return "sd15_fp16"

    @staticmethod
    def list_families() -> list[str]:
        """Return all known model family identifiers."""
        return list(VRAM_THRESHOLDS.keys())


# ---------------------------------------------------------------------------
# Dynamic VRAM status (ComfyUI v0.18.1+)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class DynamicVRAMStatus:
    """Live VRAM allocation status from ComfyUI.

    Attributes:
        vram_committed: VRAM actually committed to tensors (MB).
        vram_memory_mapped: VRAM memory-mapped by the custom allocator (MB).
        vram_reserved: VRAM reserved by the PyTorch caching allocator (MB).
        dynamic_vram_enabled: Whether ComfyUI's dynamic allocator is active.
        device_name: GPU device name.
        vram_total: Total VRAM on the device (MB).
    """

    vram_committed: float = 0.0
    vram_memory_mapped: float = 0.0
    vram_reserved: float = 0.0
    dynamic_vram_enabled: bool = False
    device_name: str = ""
    vram_total: float = 0.0

    @property
    def active_inference_mb(self) -> float:
        """VRAM actively used for inference (committed + memory-mapped).

        When Dynamic VRAM is enabled, this is the relevant metric for
        determining available headroom rather than ``vram_reserved``.
        """
        return self.vram_committed + self.vram_memory_mapped


async def query_dynamic_vram_status(base_url: str = "http://127.0.0.1:8188") -> DynamicVRAMStatus:
    """Query live VRAM allocation status from a ComfyUI instance.

    Calls the ``/system_stats`` endpoint and parses device memory
    fields.  Since ComfyUI v0.18.1, the default PyTorch allocator is
    replaced with a dynamic VRAM allocator, making ``vram_reserved``
    less meaningful; prefer ``active_inference_mb`` for headroom checks.

    Args:
        base_url: ComfyUI server base URL.

    Returns:
        DynamicVRAMStatus with live memory figures.
    """
    status = DynamicVRAMStatus()
    url = f"{base_url.rstrip('/')}/system_stats"

    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    logger.warning("ComfyUI /system_stats returned HTTP %d", resp.status)
                    return status
                data = await resp.json()
    except ImportError:
        logger.warning("aiohttp not available; cannot query ComfyUI system_stats")
        return status
    except Exception:
        logger.warning("Failed to reach ComfyUI at %s", url, exc_info=True)
        return status

    return _parse_system_stats(data)


def _parse_system_stats(data: dict) -> DynamicVRAMStatus:
    """Parse ComfyUI ``/system_stats`` response into DynamicVRAMStatus.

    Args:
        data: JSON response from ``/system_stats``.

    Returns:
        Populated DynamicVRAMStatus.
    """
    devices = data.get("devices", [])
    if not devices:
        return DynamicVRAMStatus()

    dev = devices[0]
    device_name = dev.get("name", "")
    vram_total = dev.get("vram_total", 0) / (1024 * 1024)
    vram_free = dev.get("vram_free", 0) / (1024 * 1024)

    # Compute committed = total - free (best proxy from the endpoint)
    vram_committed = vram_total - vram_free

    # torch_vram_total/free indicate reserved allocator memory when present
    torch_total = dev.get("torch_vram_total", 0) / (1024 * 1024)
    torch_free = dev.get("torch_vram_free", 0) / (1024 * 1024)
    vram_reserved = torch_total - torch_free if torch_total > 0 else 0.0

    # Dynamic VRAM is enabled when the custom allocator is active,
    # indicated by torch_vram_total being close to vram_total (no fixed pool).
    dynamic_enabled = torch_total > 0 and abs(torch_total - vram_total) < 512

    return DynamicVRAMStatus(
        vram_committed=round(vram_committed, 1),
        vram_memory_mapped=0.0,  # not exposed by the endpoint directly
        vram_reserved=round(vram_reserved, 1),
        dynamic_vram_enabled=dynamic_enabled,
        device_name=device_name,
        vram_total=round(vram_total, 1),
    )
