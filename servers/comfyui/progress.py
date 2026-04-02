"""WebSocket progress handler for ComfyUI workflow execution.

Manages real-time progress tracking via WebSocket connection to a ComfyUI
server, with automatic polling fallback when WebSocket is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import websockets

from .types import GenerationResult, ImageOutput, ProgressEvent

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stderr))

_MAX_RECONNECT_ATTEMPTS = 3
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 2.0


class ProgressHandler:
    """Manages WebSocket connection to ComfyUI for real-time progress."""

    def __init__(
        self,
        base_url: str = "http://localhost:8188",
        timeout: float = 300.0,
    ) -> None:
        ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
        self._ws_url = ws_url
        self._http_url = base_url
        self._timeout = timeout
        self._client_id = str(uuid.uuid4())
        self._results: dict[str, GenerationResult] = {}

    async def track_progress(
        self,
        prompt_id: str,
        callback: Callable[[ProgressEvent], None] | None = None,
    ) -> GenerationResult:
        """Track workflow execution via WebSocket with polling fallback.

        Tries WebSocket up to 3 times with exponential backoff, then falls
        back to HTTP polling. Raises TimeoutError on timeout, RuntimeError
        on ComfyUI execution errors.

        Args:
            prompt_id: The prompt ID returned by ComfyUI when queueing.
            callback: Optional function called with each ProgressEvent.
        """
        backoff = _INITIAL_BACKOFF_SECONDS
        last_error: Exception | None = None

        for attempt in range(_MAX_RECONNECT_ATTEMPTS):
            try:
                logger.info(
                    "WebSocket attempt %d/%d for prompt %s",
                    attempt + 1,
                    _MAX_RECONNECT_ATTEMPTS,
                    prompt_id,
                )
                result = await asyncio.wait_for(
                    self._ws_track(prompt_id, callback),
                    timeout=self._timeout,
                )
                self._results[prompt_id] = result
                return result
            except TimeoutError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "WebSocket attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
                if attempt < _MAX_RECONNECT_ATTEMPTS - 1:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

        logger.warning(
            "All WebSocket attempts failed (last error: %s). Falling back to polling for prompt %s",
            last_error,
            prompt_id,
        )
        result = await asyncio.wait_for(
            self._poll_track(prompt_id, callback),
            timeout=self._timeout,
        )
        self._results[prompt_id] = result
        return result

    async def _ws_track(
        self,
        prompt_id: str,
        callback: Callable[[ProgressEvent], None] | None,
    ) -> GenerationResult:
        """WebSocket-based progress tracking.

        Connects to ComfyUI WebSocket and processes messages until the
        workflow completes or errors. Handles all 7 message types.
        """
        ws_endpoint = f"{self._ws_url}/ws?clientId={self._client_id}"
        start_time = time.monotonic()
        images: list[ImageOutput] = []
        current_node = ""

        async with websockets.connect(ws_endpoint) as ws:
            async for raw_message in ws:
                if isinstance(raw_message, bytes):
                    continue

                data: dict[str, Any] = json.loads(raw_message)
                msg_type = data.get("type", "")
                msg_data: dict[str, Any] = data.get("data", {})

                if msg_type == "status":
                    logger.debug("Queue status: %s", msg_data)

                elif msg_type == "execution_start":
                    logger.info(
                        "Execution started for prompt %s",
                        msg_data.get("prompt_id", prompt_id),
                    )

                elif msg_type == "executing":
                    node = msg_data.get("node")
                    if node is None:
                        elapsed_ms = (time.monotonic() - start_time) * 1000
                        return GenerationResult(
                            prompt_id=prompt_id,
                            images=images,
                            execution_time_ms=elapsed_ms,
                        )
                    current_node = node
                    logger.debug("Executing node: %s", node)

                elif msg_type == "execution_cached":
                    logger.debug(
                        "Cached nodes skipped: %s",
                        msg_data.get("nodes", []),
                    )

                elif msg_type == "progress":
                    step = msg_data.get("value", 0)
                    max_steps = msg_data.get("max", 1)
                    value = step / max_steps if max_steps > 0 else 0.0
                    event = ProgressEvent(
                        prompt_id=prompt_id,
                        node=current_node,
                        step=step,
                        max_steps=max_steps,
                        value=value,
                    )
                    if callback is not None:
                        callback(event)

                elif msg_type == "executed":
                    output_data = msg_data.get("output", {})
                    for img in output_data.get("images", []):
                        images.append(
                            ImageOutput(
                                filename=img.get("filename", ""),
                                subfolder=img.get("subfolder", ""),
                                type=img.get("type", "output"),
                            )
                        )

                elif msg_type == "execution_error":
                    error_msg = msg_data.get("exception_message", "Unknown error")
                    node_id = msg_data.get("node_id", "unknown")
                    node_type = msg_data.get("node_type", "unknown")
                    raise RuntimeError(f"ComfyUI execution error on node {node_id} ({node_type}): {error_msg}")

        # Connection closed without completion signal
        elapsed_ms = (time.monotonic() - start_time) * 1000
        return GenerationResult(
            prompt_id=prompt_id,
            images=images,
            execution_time_ms=elapsed_ms,
        )

    async def _poll_track(
        self,
        prompt_id: str,
        callback: Callable[[ProgressEvent], None] | None,
    ) -> GenerationResult:
        """Polling fallback via GET /history/{prompt_id} every 2 seconds."""
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp is required for polling fallback. Install it with: pip install aiohttp")
            raise

        start_time = time.monotonic()
        history_url = f"{self._http_url}/history/{prompt_id}"

        async with aiohttp.ClientSession() as session:
            while True:
                async with session.get(history_url) as resp:
                    if resp.status == 200:
                        history: dict[str, Any] = await resp.json()
                        if prompt_id in history:
                            images = _extract_images(history[prompt_id])
                            elapsed_ms = (time.monotonic() - start_time) * 1000
                            if callback is not None:
                                callback(
                                    ProgressEvent(
                                        prompt_id=prompt_id,
                                        node="complete",
                                        step=1,
                                        max_steps=1,
                                        value=1.0,
                                    )
                                )
                            return GenerationResult(
                                prompt_id=prompt_id,
                                images=images,
                                execution_time_ms=elapsed_ms,
                            )
                logger.debug(
                    "Prompt %s not in history yet, polling...",
                    prompt_id,
                )
                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def get_cached_result(self, prompt_id: str) -> GenerationResult | None:
        """Retrieve cached result for recovery after restart."""
        return self._results.get(prompt_id)


def _extract_images(history_entry: dict[str, Any]) -> list[ImageOutput]:
    """Extract ImageOutput list from a ComfyUI history entry."""
    images: list[ImageOutput] = []
    for node_output in history_entry.get("outputs", {}).values():
        for img in node_output.get("images", []):
            images.append(
                ImageOutput(
                    filename=img.get("filename", ""),
                    subfolder=img.get("subfolder", ""),
                    type=img.get("type", "output"),
                )
            )
    return images
