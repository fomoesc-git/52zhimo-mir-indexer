from __future__ import annotations

import asyncio

import httpx

from app.config import get_settings


class FetchClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._last_request = 0.0
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=self.settings.request_timeout_seconds,
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru,en;q=0.8,zh-CN;q=0.6",
            },
            http2=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_text(self, url: str) -> str:
        await self._respect_delay()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                return response.text
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                await asyncio.sleep(2 + attempt * 3)
        raise RuntimeError(f"Fetch failed: {last_error}") from last_error

    async def _respect_delay(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        wait = self.settings.request_delay_seconds - (now - self._last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request = loop.time()
