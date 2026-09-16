"""
Credit integration with the main site (legacy-v.example.com).
Handles credit reservation, commit, and release via internal API calls.
"""

import httpx
import uuid
import logging
from ...core.config import get_settings

logger = logging.getLogger(__name__)


class CreditGateway:
    """Client for calling main site credit management APIs."""

    def __init__(self):
        settings = get_settings()
        self._base_url = settings.main_site_api_url.rstrip("/")
        self._token = settings.main_site_internal_token
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(10.0, connect=5.0),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, data: dict) -> dict:
        client = await self._get_client()
        resp = await client.post(path, json=data)
        resp.raise_for_status()
        return resp.json()

    # ── Reserve ───────────────────────────────────────────────

    async def reserve_credits(
        self,
        user_id: str,
        amount: int,
        idempotency_key: str,
        reference: str = "",
    ) -> dict | None:
        """
        Reserve credits before AI generation.
        POST /api/internal/credits/reserve
        Returns { ok, reservation_id, remaining } or None on failure.
        """
        try:
            result = await self._post("/api/internal/credits/reserve", {
                "user_id": user_id,
                "amount": amount,
                "idempotency_key": idempotency_key,
                "reference": reference,
            })
            if result.get("ok"):
                return result
            logger.warning(f"Credit reserve failed: {result}")
            return None
        except Exception as e:
            logger.error(f"Credit reserve error: {e}")
            return None

    # ── Commit ─────────────────────────────────────────────────

    async def commit_credits(self, reservation_id: str) -> dict | None:
        """
        Confirm credit deduction after successful generation.
        POST /api/internal/credits/commit
        """
        try:
            result = await self._post("/api/internal/credits/commit", {
                "reservation_id": reservation_id,
            })
            return result
        except Exception as e:
            logger.error(f"Credit commit error: {e}")
            return None

    # ── Release ────────────────────────────────────────────────

    async def release_credits(self, reservation_id: str) -> dict | None:
        """
        Release reserved credits after failed generation.
        POST /api/internal/credits/release
        """
        try:
            result = await self._post("/api/internal/credits/release", {
                "reservation_id": reservation_id,
            })
            return result
        except Exception as e:
            logger.error(f"Credit release error: {e}")
            return None

    # ── Balance ────────────────────────────────────────────────

    async def get_balance(self, user_id: str) -> dict | None:
        """
        Query user's current credit balance.
        GET /api/internal/credits/balance?user_id=xxx
        """
        try:
            client = await self._get_client()
            resp = await client.get("/api/internal/credits/balance", params={"user_id": user_id})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Credit balance query error: {e}")
            return None
