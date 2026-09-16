"""
ComfyUI REST API client.

ComfyUI API endpoints:
- GET  /object_info       → list available node types
- POST /prompt             → submit workflow for execution
- GET  /queue              → queue status
- GET  /history/{id}       → execution result
- GET  /view?filename=...  → download generated file
"""

import httpx
import asyncio
import logging
from ...core.config import get_settings

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """Async HTTP client for ComfyUI REST API."""

    def __init__(self, base_url: str | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.comfyui_base_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str, **kwargs) -> dict | list | bytes:
        client = await self._get_client()
        resp = await client.request(method, path, **kwargs)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "image" in content_type or "octet-stream" in content_type:
            return resp.content
        return resp.json()

    # ── Node Info ────────────────────────────────────────────

    async def get_object_info(self) -> dict:
        """GET /object_info — returns available node types and their inputs/outputs."""
        return await self._request("GET", "/object_info")  # type: ignore[return-value]

    async def get_node_names(self) -> list[str]:
        """Convenience: returns sorted list of available class_type names."""
        info = await self.get_object_info()
        return sorted(info.keys())

    # ── Prompt / Execution ────────────────────────────────────

    async def queue_prompt(self, prompt: dict, client_id: str = "jumeng-canvas") -> dict:
        """
        POST /prompt — submit a workflow for execution.
        Returns { prompt_id, number, node_errors }.
        """
        payload = {"prompt": prompt, "client_id": client_id}
        return await self._request("POST", "/prompt", json=payload)  # type: ignore[return-value]

    async def get_queue(self) -> dict:
        """GET /queue — returns current queue status."""
        return await self._request("GET", "/queue")  # type: ignore[return-value]

    async def get_history(self, prompt_id: str) -> dict:
        """GET /history/{prompt_id} — returns execution history for a prompt."""
        return await self._request("GET", f"/history/{prompt_id}")  # type: ignore[return-value]

    async def get_history_max(self, max_items: int = 100) -> dict:
        """GET /history?max_items=N"""
        return await self._request("GET", f"/history?max_items={max_items}")  # type: ignore[return-value]

    # ── View / Download ───────────────────────────────────────

    async def view_image(self, filename: str, subfolder: str = "", folder_type: str = "") -> bytes:
        """
        GET /view?filename=... — download a generated image.
        Returns raw image bytes.
        """
        params = {"filename": filename}
        if subfolder:
            params["subfolder"] = subfolder
        if folder_type:
            params["type"] = folder_type
        return await self._request("GET", "/view", params=params)  # type: ignore[return-value]

    # ── Interrupt / Free ──────────────────────────────────────

    async def interrupt(self) -> dict:
        """POST /interrupt — cancel current execution."""
        return await self._request("POST", "/interrupt")  # type: ignore[return-value]

    async def free(self, unload_models: bool = False, free_memory: bool = False) -> dict:
        """POST /free — free memory / unload models."""
        payload = {
            "unload_models": unload_models,
            "free_memory": free_memory,
        }
        return await self._request("POST", "/free", json=payload)  # type: ignore[return-value]

    # ── Embeddings ────────────────────────────────────────────

    async def get_embeddings(self) -> list[str]:
        """GET /embeddings — list available embeddings."""
        return await self._request("GET", "/embeddings")  # type: ignore[return-value]

    async def get_extensions(self) -> list[str]:
        """GET /extensions"""
        return await self._request("GET", "/extensions")  # type: ignore[return-value]

    # ── High-Level Helpers ────────────────────────────────────

    async def submit_and_wait(
        self,
        prompt: dict,
        poll_interval: float = 0.5,
        max_wait_s: float = 300.0,
    ) -> dict | None:
        """Submit a prompt and poll until execution completes. Returns history entry or None on timeout."""
        result = await self.queue_prompt(prompt)
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            logger.error(f"No prompt_id in response: {result}")
            return None

        elapsed = 0.0
        while elapsed < max_wait_s:
            history = await self.get_history(prompt_id)
            if prompt_id in history:
                return history[prompt_id]
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(f"Prompt {prompt_id} timed out after {max_wait_s}s")
        return None

    async def get_output_images(self, prompt_id: str) -> list[dict]:
        """
        Get output image metadata from history.
        Returns list of { filename, subfolder, type } dicts.
        """
        history = await self.get_history(prompt_id)
        entry = history.get(prompt_id)
        if not entry:
            return []

        outputs = entry.get("outputs", {})
        images: list[dict] = []
        for _node_id, node_output in outputs.items():
            for item in node_output.get("images", []):
                images.append({
                    "filename": item["filename"],
                    "subfolder": item.get("subfolder", ""),
                    "type": item.get("type", "output"),
                })
        return images
