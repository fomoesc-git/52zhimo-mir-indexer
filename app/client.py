from __future__ import annotations

import asyncio
import random
from typing import Callable

import httpx

from app.config import get_settings


class FetchClient:
    def __init__(
        self,
        pause_checker: Callable[[], None] | None = None,
        request_delay_seconds: float | None = None,
        request_jitter_seconds: float | None = None,
    ) -> None:
        self.settings = get_settings()
        self._last_request = 0.0
        self.pause_checker = pause_checker
        self.request_delay_seconds = (
            request_delay_seconds
            if request_delay_seconds is not None
            else self.settings.request_delay_seconds
        )
        self.request_jitter_seconds = (
            request_jitter_seconds
            if request_jitter_seconds is not None
            else self.settings.request_jitter_seconds
        )
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
        if self.pause_checker:
            self.pause_checker()
        await self._respect_delay()
        if self.pause_checker:
            self.pause_checker()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(url)
                if response.status_code in {403, 429, 500, 502, 503, 504}:
                    await self._sleep(10 + attempt * 20)
                response.raise_for_status()
                response.encoding = response.encoding or "utf-8"
                return response.text
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                await self._sleep(2 + attempt * 3)
        raise RuntimeError(f"Fetch failed: {last_error}") from last_error

    async def _respect_delay(self) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        base_delay = self.request_delay_seconds + random.uniform(0, self.request_jitter_seconds)
        wait = base_delay - (now - self._last_request)
        if wait > 0:
            await self._sleep(wait)
        self._last_request = loop.time()

    async def _sleep(self, seconds: float) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0:
            if self.pause_checker:
                self.pause_checker()
            step = min(1.0, remaining)
            await asyncio.sleep(step)
            remaining -= step
        if self.pause_checker:
            self.pause_checker()
