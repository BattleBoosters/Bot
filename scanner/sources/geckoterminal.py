"""GeckoTerminal source: primary DEX universe + OHLCV provider.

Free public API at https://api.geckoterminal.com/api/v2 (~30 req/min).
Pulls trending and newly-created pools per network, extracts the base token
(the gem, not the WETH/USDC quote), and serves daily OHLCV from pool history.
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

from scanner.sources.base import Source, Token

logger = logging.getLogger(__name__)

API_BASE = "https://api.geckoterminal.com/api/v2"

QUOTE_SYMBOLS_TO_SKIP = {
    "USDC", "USDT", "DAI", "BUSD", "TUSD", "USDE", "FDUSD", "PYUSD",
    "WETH", "ETH", "WBTC", "BTC", "WSOL", "SOL", "WBNB", "BNB",
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


def _wash_signal(
    buys: Any, sells: Any, buyers: Any, sellers: Any
) -> dict:
    """Heuristic wash-trade flags from h24 transaction counts.

    - unique_traders / total_tx ratio: low = lots of repeat traders
      (wash signature).
    - buy/sell skew: extreme imbalance can indicate one-sided manipulation.

    Returns flag fields merged into Token.extra.
    """
    out: dict = {}
    try:
        b = int(buys or 0)
        s = int(sells or 0)
        br = int(buyers or 0)
        sr = int(sellers or 0)
    except (TypeError, ValueError):
        return out
    total_tx = b + s
    unique = br + sr
    if total_tx >= 30 and unique > 0:
        ratio = unique / total_tx
        out["unique_trader_ratio"] = round(ratio, 3)
        if ratio < 0.20:
            out["wash_trade_warning"] = True
    if total_tx >= 50:
        skew = abs(b - s) / total_tx
        out["buy_sell_skew"] = round(skew, 3)
        if skew > 0.85:
            out["one_sided_warning"] = True
    return out


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        if isinstance(v, str):
            s = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
        else:
            return None
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class GeckoTerminalSource:
    name = "geckoterminal"

    def __init__(
        self,
        networks: list[str],
        client: httpx.AsyncClient,
        rate_limit_per_min: int = 25,
        top_pools_pages: int = 5,
    ):
        self.networks = networks
        self.client = client
        self.top_pools_pages = max(1, int(top_pools_pages))
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

    async def _get(self, path: str, params: dict | None = None) -> dict:
        await self._throttle()
        url = f"{API_BASE}{path}"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(min=1, max=20),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                async with self._sem:
                    r = await self.client.get(url, params=params)
                    if r.status_code == 429:
                        await asyncio.sleep(5)
                        r.raise_for_status()
                    r.raise_for_status()
                    return r.json()
        return {}

    async def list_universe(self) -> list[Token]:
        tokens: dict[str, Token] = {}
        # Three discovery streams per chain:
        #   trending_pools  — currently buzzing
        #   new_pools       — freshly created, the early-stage candidates
        #   pools (top by 24h volume) — the broad mid-tier we used to miss
        # We paginate `pools` more aggressively because that's where most
        # established gems live (rank 50–500 by volume per chain).
        for network in self.networks:
            for endpoint, pages in (
                ("trending_pools", (1, 2)),
                ("new_pools",      (1, 2)),
                ("pools",          tuple(range(1, self.top_pools_pages + 1))),
            ):
                try:
                    for page in pages:
                        data = await self._get(
                            f"/networks/{network}/{endpoint}",
                            params={"page": page, "include": "base_token,quote_token"},
                        )
                        included = {
                            (item["type"], item["id"]): item
                            for item in data.get("included", [])
                        }
                        rows = data.get("data", []) or []
                        if not rows:
                            break
                        for pool in rows:
                            tok = self._pool_to_token(pool, included, network)
                            if tok and tok.key not in tokens:
                                tokens[tok.key] = tok
                except httpx.HTTPError as e:
                    logger.warning(
                        "geckoterminal %s/%s failed: %s", network, endpoint, e
                    )
                    continue
        return list(tokens.values())

    def _pool_to_token(
        self, pool: dict, included: dict, network: str
    ) -> Token | None:
        attrs = pool.get("attributes") or {}
        rels = pool.get("relationships") or {}
        base_ref = (rels.get("base_token") or {}).get("data") or {}
        base_id = base_ref.get("id")
        base = included.get(("token", base_id)) if base_id else None
        if not base:
            return None
        b = base.get("attributes") or {}
        symbol = (b.get("symbol") or "").upper()
        if symbol in QUOTE_SYMBOLS_TO_SKIP:
            return None
        address = b.get("address")
        if not address:
            return None

        mcap = _safe_float(attrs.get("market_cap_usd")) or _safe_float(
            attrs.get("fdv_usd")
        )
        vol = _safe_float((attrs.get("volume_usd") or {}).get("h24"))
        price = _safe_float(attrs.get("base_token_price_usd"))
        created = _parse_dt(attrs.get("pool_created_at"))
        pool_addr = attrs.get("address")

        chart_url = None
        if pool_addr:
            chart_url = f"https://www.geckoterminal.com/{network}/pools/{pool_addr}"

        extra: dict = {}
        tx_h24 = (attrs.get("transactions") or {}).get("h24") or {}
        buys = tx_h24.get("buys")
        sells = tx_h24.get("sells")
        buyers = tx_h24.get("buyers")
        sellers = tx_h24.get("sellers")
        if any(v is not None for v in (buys, sells, buyers, sellers)):
            extra["tx_h24"] = {
                "buys": buys,
                "sells": sells,
                "buyers": buyers,
                "sellers": sellers,
            }
            extra.update(_wash_signal(buys, sells, buyers, sellers))

        return Token(
            symbol=symbol,
            name=b.get("name") or symbol,
            chain=network,
            address=address,
            mcap_usd=mcap,
            vol_24h_usd=vol,
            price_usd=price,
            created_at=created,
            source=self.name,
            pool_address=pool_addr,
            chart_url=chart_url,
            extra=extra,
        )

    async def get_ohlcv(self, token: Token, days: int) -> pd.DataFrame:
        if not token.chain or not token.pool_address:
            return pd.DataFrame()
        limit = min(max(days + 2, 5), 1000)
        try:
            data = await self._get(
                f"/networks/{token.chain}/pools/{token.pool_address}/ohlcv/day",
                params={"aggregate": 1, "limit": limit, "currency": "usd"},
            )
        except httpx.HTTPError as e:
            logger.warning("geckoterminal ohlcv failed for %s: %s", token.symbol, e)
            return pd.DataFrame()
        rows = (
            ((data.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        )
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(
            rows, columns=["ts", "open", "high", "low", "close", "volume"]
        )
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.normalize()
        df = df.sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close"])
        return df.tail(days)
