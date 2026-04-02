"""ComfyUI REST + WebSocket client with circuit breaker pattern.

Wraps the ComfyUI API for workflow submission, progress tracking,
result retrieval, model discovery, and queue management.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import websockets

from .types import (
    CircuitState,
    GenerationResult,
    ImageOutput,
    NodeSchema,
    ProgressEvent,
    QueueState,
    SystemStats,
)

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8188"
_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_RECOVERY_TIMEOUT = 30.0
_HTTP_TIMEOUT = 30.0
_WS_MAX_RETRIES = 3
_POLL_INTERVAL = 2.0


class ComfyUIClient:
    """Async client for the ComfyUI REST and WebSocket API."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("COMFYUI_URL") or _DEFAULT_URL).rstrip("/")
        self.client_id = str(uuid.uuid4())

        # Circuit breaker state.
        self._circuit_state = CircuitState.CLOSED
        self._circuit_failures = 0
        self._last_failure_time = 0.0

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def _check_circuit(self) -> None:
        """Raise if the circuit breaker is OPEN (fast-fail)."""
        if self._circuit_state == CircuitState.CLOSED:
            return

        if self._circuit_state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= _CIRCUIT_RECOVERY_TIMEOUT:
                logger.info("Circuit breaker transitioning to HALF_OPEN")
                self._circuit_state = CircuitState.HALF_OPEN
                return
            raise ConnectionError("Circuit breaker OPEN — ComfyUI unreachable")

        # HALF_OPEN allows a single probe.

    def _record_success(self) -> None:
        """Record a successful request and reset the breaker."""
        if self._circuit_state != CircuitState.CLOSED:
            logger.info("Circuit breaker CLOSED after successful probe")
        self._circuit_state = CircuitState.CLOSED
        self._circuit_failures = 0

    def _record_failure(self) -> None:
        """Record a failed request; open the breaker after threshold."""
        self._circuit_failures += 1
        self._last_failure_time = time.monotonic()

        if self._circuit_failures >= _CIRCUIT_FAILURE_THRESHOLD:
            self._circuit_state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN after %d consecutive failures",
                self._circuit_failures,
            )

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Execute an HTTP request with circuit breaker protection."""
        self._check_circuit()
        url = f"{self.base_url}{path}"

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.request(
                    method,
                    url,
                    json=json_body,
                    data=data,
                    files=files,
                )
                resp.raise_for_status()
                self._record_success()
                return resp
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._record_failure()
            logger.error("Connection error for %s %s: %s", method, path, exc)
            raise
        except httpx.HTTPStatusError as exc:
            # Status errors are server-reachable; don't trip the breaker.
            logger.error("HTTP %s for %s %s", exc.response.status_code, method, path)
            raise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Check if ComfyUI is reachable via ``GET /system_stats``."""
        try:
            await self._request("GET", "/system_stats")
            return True
        except (httpx.TimeoutException, httpx.ConnectError, ConnectionError):
            return False
        except httpx.HTTPStatusError:
            return False

    async def submit_workflow(self, workflow_json: dict[str, Any]) -> str:
        """Submit a workflow for execution, returning the *prompt_id*."""
        payload = {"prompt": workflow_json, "client_id": self.client_id}
        resp = await self._request("POST", "/prompt", json_body=payload)
        body = resp.json()
        return body["prompt_id"]

    async def get_progress(
        self,
        prompt_id: str,
        callback: Callable[[ProgressEvent], Any] | None = None,
    ) -> list[ProgressEvent]:
        """Track execution progress via WebSocket with polling fallback.

        Connects to ``ws://{host}/ws?clientId={client_id}``, parses seven
        message types, and invokes *callback* for each ``ProgressEvent``.
        Falls back to polling ``/history/{prompt_id}`` if the WebSocket
        connection fails ``_WS_MAX_RETRIES`` times.
        """
        ws_failures = 0
        backoff = 1.0

        while ws_failures < _WS_MAX_RETRIES:
            try:
                return await self._ws_progress(prompt_id, callback)
            except (
                OSError,
                websockets.exceptions.WebSocketException,
            ) as exc:
                ws_failures += 1
                logger.warning(
                    "WebSocket attempt %d/%d failed: %s",
                    ws_failures,
                    _WS_MAX_RETRIES,
                    exc,
                )
                await asyncio.sleep(min(backoff, 30.0))
                backoff *= 2.0

        logger.info("Falling back to polling for prompt %s", prompt_id)
        return await self._poll_progress(prompt_id)

    async def get_result(self, prompt_id: str) -> GenerationResult:
        """Retrieve the completed result for *prompt_id*."""
        resp = await self._request("GET", f"/history/{prompt_id}")
        data = resp.json()

        outputs = data.get(prompt_id, {}).get("outputs", {})
        images: list[ImageOutput] = []
        for node_out in outputs.values():
            for img in node_out.get("images", []):
                images.append(
                    ImageOutput(
                        filename=img["filename"],
                        subfolder=img.get("subfolder", ""),
                        type=img.get("type", "output"),
                    )
                )

        status = data.get(prompt_id, {}).get("status", {})
        exec_info = status.get("messages", [[]])[0]
        exec_time = 0.0
        if isinstance(exec_info, dict):
            exec_time = exec_info.get("execution_time", 0.0)

        return GenerationResult(
            prompt_id=prompt_id,
            images=images,
            execution_time_ms=exec_time * 1000,
        )

    async def get_system_stats(self) -> SystemStats:
        """Return typed ``SystemStats`` from ``GET /system_stats``."""
        resp = await self._request("GET", "/system_stats")
        data = resp.json()
        devices = data.get("devices", [{}])
        dev = devices[0] if devices else {}
        return SystemStats(
            ram_total=data.get("system", {}).get("ram_total", 0),
            ram_free=data.get("system", {}).get("ram_free", 0),
            vram_total=dev.get("vram_total", 0),
            vram_free=dev.get("vram_free", 0),
            device_name=dev.get("name", "unknown"),
            device_type=dev.get("type", "cuda"),
        )

    async def get_node_info(self, node_class: str | None = None) -> dict[str, NodeSchema]:
        """Return node schema definitions from ``GET /object_info``."""
        path = f"/object_info/{node_class}" if node_class else "/object_info"
        resp = await self._request("GET", path)
        raw = resp.json()
        result: dict[str, NodeSchema] = {}
        for name, info in raw.items():
            result[name] = NodeSchema(
                class_type=name,
                inputs=info.get("input", {}),
                outputs=info.get("output", []),
                description=info.get("description", ""),
                category=info.get("category", ""),
            )
        return result

    async def list_models(self, folder: str = "checkpoints") -> list[str]:
        """List available models in *folder*."""
        try:
            resp = await self._request("GET", f"/models/{folder}")
            return resp.json()
        except (httpx.TimeoutException, httpx.ConnectError, ConnectionError):
            return []
        except httpx.HTTPStatusError:
            return []

    async def upload_image(
        self,
        file_path: str,
        subfolder: str = "",
        overwrite: bool = False,
    ) -> str:
        """Upload an image file, returning the stored filename."""
        path_obj = Path(file_path)
        with path_obj.open("rb") as fh:
            files = {"image": (path_obj.name, fh, "image/png")}
            data: dict[str, Any] = {"subfolder": subfolder}
            if overwrite:
                data["overwrite"] = "true"
            resp = await self._request("POST", "/upload/image", data=data, files=files)
        body = resp.json()
        return body.get("name", path_obj.name)

    async def cancel_job(self, prompt_id: str) -> bool:
        """Cancel a queued/running job by *prompt_id*."""
        try:
            await self._request("POST", "/queue", json_body={"delete": [prompt_id]})
            return True
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.HTTPStatusError,
            ConnectionError,
        ):
            return False

    async def get_queue(self) -> QueueState:
        """Return the current execution queue state."""
        resp = await self._request("GET", "/queue")
        data = resp.json()
        running = data.get("queue_running", [])
        pending = data.get("queue_pending", [])
        return QueueState(
            running=len(running),
            pending=len(pending),
            items=running + pending,
        )

    async def clear_vram(self) -> bool:
        """Unload models and free VRAM."""
        try:
            await self._request(
                "POST",
                "/free",
                json_body={"unload_models": True, "free_memory": True},
            )
            return True
        except (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.HTTPStatusError,
            ConnectionError,
        ):
            return False

    # ------------------------------------------------------------------
    # WebSocket progress (simplified)
    # ------------------------------------------------------------------

    async def _ws_progress(
        self,
        prompt_id: str,
        callback: Callable[[ProgressEvent], Any] | None,
    ) -> list[ProgressEvent]:
        """Read progress events from the ComfyUI WebSocket."""
        ws_url = self.base_url.replace("http", "ws", 1)
        uri = f"{ws_url}/ws?clientId={self.client_id}"

        events: list[ProgressEvent] = []
        async with websockets.connect(uri) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                data = msg.get("data", {})

                if msg_type == "execution_start":
                    evt = ProgressEvent(
                        prompt_id=data.get("prompt_id", prompt_id),
                        node="",
                        step=0,
                        max_steps=0,
                        value=0.0,
                    )
                    events.append(evt)
                    if callback:
                        callback(evt)

                elif msg_type == "progress":
                    evt = ProgressEvent(
                        prompt_id=data.get("prompt_id", prompt_id),
                        node=data.get("node", ""),
                        step=data.get("value", 0),
                        max_steps=data.get("max", 0),
                        value=(data.get("value", 0) / max(data.get("max", 1), 1)),
                    )
                    events.append(evt)
                    if callback:
                        callback(evt)

                elif msg_type in ("executed", "execution_error"):
                    if data.get("prompt_id") == prompt_id:
                        break

                elif msg_type == "status":
                    queue = data.get("status", {}).get("exec_info", {})
                    if queue.get("queue_remaining", 1) == 0:
                        break

        return events

    # ------------------------------------------------------------------
    # Polling fallback
    # ------------------------------------------------------------------

    async def _poll_progress(self, prompt_id: str) -> list[ProgressEvent]:
        """Poll ``/history/{prompt_id}`` until completion."""
        for _ in range(150):  # ~5 minutes max
            try:
                resp = await self._request("GET", f"/history/{prompt_id}")
                data = resp.json()
                if prompt_id in data:
                    return []
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                ConnectionError,
            ):
                pass
            await asyncio.sleep(_POLL_INTERVAL)
        logger.warning("Polling timed out for prompt %s", prompt_id)
        return []
