"""ComfyUI version compatibility checker.

Queries a ComfyUI instance for version and installed extensions,
then checks feature compatibility against a known matrix.  Warns
the user when features are unavailable on their ComfyUI version.
"""

from __future__ import annotations

import dataclasses
import logging
import re

logger = logging.getLogger(__name__)

# Feature compatibility matrix: feature -> (min_version, requires_extension)
_FEATURE_MATRIX: dict[str, tuple[str, str]] = {
    "dynamic_vram": ("0.18.1", ""),
    "v3_nodes": ("0.18.0", ""),
    "gguf_models": ("0.0.0", "ComfyUI-GGUF"),
    "sage_attention": ("0.0.0", "SageAttention"),
    "flash_attention": ("0.0.0", "flash-attn"),
    "fp8_support": ("0.17.0", ""),
    "workflow_v1_schema": ("0.14.0", ""),
    "api_v1": ("0.18.0", ""),
    "native_video": ("0.18.0", ""),
    "latent_preview": ("0.10.0", ""),
}


@dataclasses.dataclass
class CompatibilityReport:
    """Report on ComfyUI version and feature compatibility.

    Attributes:
        comfyui_version: Detected ComfyUI version string.
        extensions: List of installed extension names.
        supported_features: Map of feature name to support status.
        warnings: Human-readable compatibility warnings.
    """

    comfyui_version: str = ""
    extensions: list[str] = dataclasses.field(default_factory=list)
    supported_features: dict[str, bool] = dataclasses.field(default_factory=dict)
    warnings: list[str] = dataclasses.field(default_factory=list)


class CompatibilityChecker:
    """Checks ComfyUI instance for version and feature compatibility.

    Args:
        base_url: ComfyUI server base URL.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8188") -> None:
        self._base_url = base_url.rstrip("/")

    async def check(self) -> CompatibilityReport:
        """Query ComfyUI and produce a full compatibility report.

        Calls ``/system_stats`` to get version info and ``/object_info``
        to detect installed extensions.  Falls back gracefully if
        endpoints are unavailable.

        Returns:
            CompatibilityReport with version, extensions, and feature
            support status.
        """
        report = CompatibilityReport()

        # Fetch system stats for version
        system_stats = await self._fetch_system_stats()
        if system_stats:
            report = dataclasses.replace(
                report,
                comfyui_version=system_stats.get("comfyui_version", ""),
            )

        # Detect extensions from object_info
        extensions = await self._fetch_extensions()
        report = dataclasses.replace(report, extensions=extensions)

        # Check feature support
        features = self.check_feature_support(
            report.comfyui_version,
            extensions,
        )
        report = dataclasses.replace(report, supported_features=features)

        # Generate warnings for unsupported features
        warnings = _generate_warnings(features, report.comfyui_version)
        report = dataclasses.replace(report, warnings=warnings)

        return report

    def check_feature_support(
        self,
        version: str,
        extensions: list[str] | None = None,
    ) -> dict[str, bool]:
        """Check which features are supported by a given version.

        Args:
            version: ComfyUI version string (e.g. "0.18.1").
            extensions: List of installed extension names.

        Returns:
            Dict mapping feature name to bool (supported or not).
        """
        extensions = extensions or []
        ext_names_lower = {e.lower() for e in extensions}
        result: dict[str, bool] = {}

        for feature, (min_ver, required_ext) in _FEATURE_MATRIX.items():
            version_ok = _version_gte(version, min_ver) if version else False
            ext_ok = True
            if required_ext:
                ext_ok = required_ext.lower() in ext_names_lower
            result[feature] = version_ok and ext_ok

        return result

    async def _fetch_system_stats(self) -> dict:
        """Fetch system stats from ComfyUI.

        Returns:
            Dict with system stats or empty dict on failure.
        """
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"{self._base_url}/system_stats"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Extract version from system stats
                        system = data.get("system", {})
                        return {
                            "comfyui_version": system.get("comfyui_version", ""),
                            "python_version": system.get("python_version", ""),
                            "devices": data.get("devices", []),
                        }
        except ImportError:
            logger.debug("aiohttp not available; cannot query ComfyUI")
        except Exception:
            logger.debug("Failed to fetch system stats from %s", self._base_url)

        return {}

    async def _fetch_extensions(self) -> list[str]:
        """Detect installed extensions from ComfyUI object_info.

        Parses the class types returned by ``/object_info`` and
        identifies extension prefixes.

        Returns:
            List of detected extension names.
        """
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                url = f"{self._base_url}/object_info"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return _extract_extensions(data)
        except ImportError:
            logger.debug("aiohttp not available; cannot detect extensions")
        except Exception:
            logger.debug("Failed to fetch object_info from %s", self._base_url)

        return []


def _version_gte(version: str, minimum: str) -> bool:
    """Check if a version string is greater than or equal to a minimum.

    Handles version strings like "0.18.1", "0.18.1-dev", etc.

    Args:
        version: Version to check.
        minimum: Minimum required version.

    Returns:
        True if version >= minimum.
    """
    try:
        ver_parts = _parse_version(version)
        min_parts = _parse_version(minimum)
        return ver_parts >= min_parts
    except (ValueError, TypeError):
        logger.debug("Could not compare versions: %s vs %s", version, minimum)
        return False


def _parse_version(version: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple.

    Args:
        version: Version string (e.g. "0.18.1", "0.18.1-dev").

    Returns:
        Tuple of integers for comparison.
    """
    # Strip anything after a hyphen (pre-release tags)
    clean = re.split(r"[-+]", version)[0]
    parts = clean.strip().split(".")
    return tuple(int(p) for p in parts if p.isdigit())


def _extract_extensions(object_info: dict) -> list[str]:
    """Extract extension names from ComfyUI object_info response.

    Extensions typically register nodes with a common prefix or
    in specific categories.

    Args:
        object_info: Response from ``/object_info`` endpoint.

    Returns:
        List of unique extension names detected.
    """
    # Known extension prefixes mapped to extension names
    prefix_map: dict[str, str] = {
        "GGUF": "ComfyUI-GGUF",
        "SageAttention": "SageAttention",
        "IPAdapter": "ComfyUI-IPAdapter",
        "ControlNet": "ComfyUI-ControlNet",
        "AnimateDiff": "ComfyUI-AnimateDiff",
        "WanVideo": "ComfyUI-WanVideo",
        "LTXVideo": "ComfyUI-LTXVideo",
    }

    found: set[str] = set()
    for class_type in object_info:
        for prefix, ext_name in prefix_map.items():
            if prefix.lower() in class_type.lower():
                found.add(ext_name)

    return sorted(found)


def _generate_warnings(
    features: dict[str, bool],
    version: str,
) -> list[str]:
    """Generate human-readable warnings for unsupported features.

    Args:
        features: Feature support dict from check_feature_support.
        version: Detected ComfyUI version.

    Returns:
        List of warning strings.
    """
    warnings: list[str] = []

    warning_messages: dict[str, str] = {
        "dynamic_vram": ("Dynamic VRAM requires ComfyUI v0.18.1+. Current version may use static VRAM allocation."),
        "v3_nodes": ("V3 node API requires ComfyUI v0.18.0+. Some modern nodes may not load."),
        "gguf_models": (
            "GGUF model support requires the ComfyUI-GGUF extension. "
            "Install from: https://github.com/city96/ComfyUI-GGUF"
        ),
        "fp8_support": ("FP8 model support requires ComfyUI v0.17.0+. Upgrade for FP8 inference capability."),
    }

    for feature, supported in features.items():
        if not supported and feature in warning_messages:
            msg = warning_messages[feature]
            if version:
                msg = f"[ComfyUI {version}] {msg}"
            warnings.append(msg)

    return warnings
