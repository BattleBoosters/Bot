"""CoinGecko source: listed small-caps in the gem mcap window.

Free public API at https://api.coingecko.com/api/v3 (~30 req/min).
Pulls /coins/markets ascending by market cap and keeps rows whose mcap
falls within [mcap_min, mcap_max]. OHLCV via /coins/{id}/market_chart
(daily close + volume; OHLC requires a separate endpoint, but for a 30-day
trend close+volume is sufficient — open/high/low are reconstructed as close).
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

API_BASE = "https://api.coingecko.com/api/v3"
PRO_API_BASE = "https://pro-api.coingecko.com/api/v3"


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


class CoinGeckoSource:
    name = "coingecko"

    def __init__(
        self,
        client: httpx.AsyncClient,
        mcap_min: float,
        mcap_max: float,
        api_key: str = "",
        rate_limit_per_min: int = 25,
    ):
        self.client = client
        self.mcap_min = mcap_min
        self.mcap_max = mcap_max
        self.api_key = api_key
        self.base = PRO_API_BASE if api_key else API_BASE
        self._sem = asyncio.Semaphore(2)
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

    def _headers(self) -> dict:
        if self.api_key:
            return {"x-cg-pro-api-key": self.api_key}
        return {}

    async def _get(self, path: str, params: dict | None = None) -> Any:
        await self._throttle()
        url = f"{self.base}{path}"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(min=2, max=30),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                async with self._sem:
                    r = await self.client.get(
                        url, params=params, headers=self._headers()
                    )
                    if r.status_code == 429:
                        await asyncio.sleep(10)
                        r.raise_for_status()
                    r.raise_for_status()
                    return r.json()
        return {}

    async def list_universe(self) -> list[Token]:
        tokens: list[Token] = []
        # Walk pages ascending by mcap until we exceed mcap_max. Cap at 40
        # pages (10k tokens) to keep one full scan within ~2 minutes on the
        # free tier even when mcap_max is bumped to $300M+.
        for page in range(1, 41):
            try:
                rows = await self._get(
                    "/coins/markets",
                    params={
                        "vs_currency": "usd",
                        "order": "market_cap_asc",
                        "per_page": 250,
                        "page": page,
                        "sparkline": "false",
                        "price_change_percentage": "24h,7d",
                    },
                )
            except httpx.HTTPError as e:
                logger.warning("coingecko markets page %d failed: %s", page, e)
                break
            if not isinstance(rows, list) or not rows:
                break
            stop_paging = False
            for row in rows:
                mcap = _safe_float(row.get("market_cap"))
                if mcap is None or mcap <= 0:
                    continue
                if mcap < self.mcap_min:
                    continue
                if mcap > self.mcap_max:
                    stop_paging = True
                    continue
                vol = _safe_float(row.get("total_volume"))
                price = _safe_float(row.get("current_price"))
                cg_id = row.get("id")
                tokens.append(
                    Token(
                        symbol=(row.get("symbol") or "").upper(),
                        name=row.get("name") or "",
                        chain=None,
                        address=None,
                        coingecko_id=cg_id,
                        mcap_usd=mcap,
                        vol_24h_usd=vol,
                        price_usd=price,
                        source=self.name,
                        chart_url=(
                            f"https://www.coingecko.com/en/coins/{cg_id}" if cg_id else None
                        ),
                    )
                )
            if stop_paging:
                break
        return tokens

    async def fetch_genesis_dates(self, tokens: list[Token]) -> None:
        """Populate `created_at` on tokens that lack it.

        Free CoinGecko gives us /coins/{id} with `genesis_date` but each call
        is rate-limited. We only enrich tokens still missing an age.
        """
        for tok in tokens:
            if tok.created_at is not None or not tok.coingecko_id:
                continue
            try:
                data = await self._get(
                    f"/coins/{tok.coingecko_id}",
                    params={
                        "localization": "false",
                        "tickers": "false",
                        "market_data": "false",
                        "community_data": "false",
                        "developer_data": "false",
                        "sparkline": "false",
                    },
                )
            except httpx.HTTPError as e:
                logger.debug("coingecko genesis_date failed for %s: %s", tok.coingecko_id, e)
                continue
            gd = data.get("genesis_date") if isinstance(data, dict) else None
            if gd:
                try:
                    dt = datetime.fromisoformat(gd).replace(tzinfo=timezone.utc)
                    tok.created_at = dt
                except ValueError:
                    pass

    async def get_ohlcv(self, token: Token, days: int) -> pd.DataFrame:
        if not token.coingecko_id:
            return pd.DataFrame()
        try:
            data = await self._get(
                f"/coins/{token.coingecko_id}/market_chart",
                params={"vs_currency": "usd", "days": days, "interval": "daily"},
            )
        except httpx.HTTPError as e:
            logger.warning("coingecko market_chart failed for %s: %s", token.symbol, e)
            return pd.DataFrame()
        prices = data.get("prices") or []
        volumes = data.get("total_volumes") or []
        if not prices:
            return pd.DataFrame()
        price_df = pd.DataFrame(prices, columns=["ts", "close"])
        vol_df = pd.DataFrame(volumes, columns=["ts", "volume"])
        df = price_df.merge(vol_df, on="ts", how="left")
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.normalize()
        df = df.drop_duplicates("ts", keep="last").set_index("ts").sort_index()
        df["open"] = df["close"]
        df["high"] = df["close"]
        df["low"] = df["close"]
        df = df[["open", "high", "low", "close", "volume"]]
        return df.tail(days)
