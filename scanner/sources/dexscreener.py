"""Dexscreener source: enrichment + cross-chain liquidity snapshot.

No historical OHLCV — used solely to confirm/refresh mcap, age (pairCreatedAt),
liquidity, and to fetch a Dexscreener chart URL. Free, no auth required.
Rate limit ~300 req/min on tokens endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import pandas as pd
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scanner.sources.base import Token

logger = logging.getLogger(__name__)

API_BASE = "https://api.dexscreener.com/latest/dex"

CHAIN_ALIAS = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "bsc": "bsc",
    "solana": "solana",
    "base": "base",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "optimism": "optimism",
}


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


class DexscreenerSource:
    name = "dexscreener"

    def __init__(self, client: httpx.AsyncClient, rate_limit_per_min: int = 250):
        self.client = client
        self._sem = asyncio.Semaphore(8)
        self._min_interval = 60.0 / max(rate_limit_per_min, 1)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = asyncio.get_event_loop().time()

    async def _get(self, path: str) -> dict:
        await self._throttle()
        url = f"{API_BASE}{path}"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                async with self._sem:
                    r = await self.client.get(url)
                    r.raise_for_status()
                    return r.json()
        return {}

    async def enrich(self, token: Token) -> None:
        """Fill missing mcap/vol/age/chart_url on `token` in-place."""
        if not token.address:
            return
        try:
            data = await self._get(f"/tokens/{token.address}")
        except httpx.HTTPError as e:
            logger.debug("dexscreener enrich failed for %s: %s", token.symbol, e)
            return
        pairs = data.get("pairs") or []
        if not pairs:
            return

        chain_norm = CHAIN_ALIAS.get((token.chain or "").lower(), token.chain)
        same_chain = [p for p in pairs if p.get("chainId") == chain_norm] or pairs
        best = max(
            same_chain,
            key=lambda p: _safe_float((p.get("liquidity") or {}).get("usd")) or 0.0,
        )

        mcap = _safe_float(best.get("marketCap")) or _safe_float(best.get("fdv"))
        if mcap and (token.mcap_usd is None or token.mcap_usd <= 0):
            token.mcap_usd = mcap

        vol = _safe_float((best.get("volume") or {}).get("h24"))
        if vol is not None and (token.vol_24h_usd is None or token.vol_24h_usd <= 0):
            token.vol_24h_usd = vol

        created_ms = best.get("pairCreatedAt")
        if isinstance(created_ms, (int, float)) and created_ms > 0:
            ds_created = datetime.fromtimestamp(created_ms / 1000.0, tz=timezone.utc)
            if token.created_at is None or ds_created < token.created_at:
                token.created_at = ds_created

        if not token.chart_url and best.get("url"):
            token.chart_url = best["url"]

        if not token.pool_address and best.get("pairAddress"):
            token.pool_address = best["pairAddress"]

    async def list_universe(self) -> list[Token]:
        return []

    async def get_ohlcv(self, token: Token, days: int) -> pd.DataFrame:
        return pd.DataFrame()
