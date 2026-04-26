"""Helius / Solana mainnet RPC client for on-chain accumulation signals.

We hit Helius's hosted RPC endpoint when an API key is configured (free
tier: ~100k req/mo, very generous for a daily snapshot job), and fall back
to the public Solana RPC otherwise (much stricter rate limits, suitable
only for sparse one-off lookups).

Two signals are exposed:

    get_top_holders(mint)
        → top 20 accounts + their balance share. Standard RPC method
          `getTokenLargestAccounts`, always works regardless of provider.

    get_holder_count(mint)
        → total number of accounts holding the SPL token. Implemented via
          `getProgramAccounts` against the SPL Token program with a
          dataSize=165 filter and a memcmp on the mint at offset 0. This
          call is heavy (Helius free tier handles it; public RPC will
          rate-limit). The filter is the canonical SPL token account
          layout — see Solana docs.

Holder count is then snapshotted in SQLite so growth over time becomes
computable across runs (the actual leading-indicator signal).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key={key}"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


class HeliusClient:
    name = "helius"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str = "",
        rate_limit_per_min: int = 100,
    ):
        self.client = client
        self.api_key = api_key
        self.url = HELIUS_RPC.format(key=api_key) if api_key else PUBLIC_RPC
        self._sem = asyncio.Semaphore(4)
        self._min_interval = 60.0 / max(rate_limit_per_min, 1)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = asyncio.get_event_loop().time()

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        await self._throttle()
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(min=2, max=30),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                async with self._sem:
                    r = await self.client.post(
                        self.url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": method,
                            "params": params,
                        },
                    )
                    if r.status_code == 429:
                        await asyncio.sleep(10)
                        r.raise_for_status()
                    r.raise_for_status()
                    data = r.json()
                    if isinstance(data, dict) and data.get("error"):
                        # RPC-level error, don't retry blindly.
                        raise RuntimeError(f"RPC error: {data['error']}")
                    return data.get("result")

    async def get_token_supply(self, mint: str) -> float | None:
        try:
            result = await self._rpc("getTokenSupply", [mint])
        except (httpx.HTTPError, RuntimeError) as e:
            logger.debug("getTokenSupply failed for %s: %s", mint, e)
            return None
        if not isinstance(result, dict):
            return None
        val = (result.get("value") or {}).get("uiAmount")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    async def get_top_holders(self, mint: str) -> list[tuple[str, float]] | None:
        """Return up to 20 (address, ui_amount) tuples sorted by balance desc."""
        try:
            result = await self._rpc("getTokenLargestAccounts", [mint])
        except (httpx.HTTPError, RuntimeError) as e:
            logger.debug("getTokenLargestAccounts failed for %s: %s", mint, e)
            return None
        accounts = (result or {}).get("value") or []
        out: list[tuple[str, float]] = []
        for a in accounts:
            addr = a.get("address")
            ui = a.get("uiAmount")
            try:
                amount = float(ui) if ui is not None else 0.0
            except (TypeError, ValueError):
                amount = 0.0
            if addr:
                out.append((str(addr), amount))
        return out

    async def get_holder_count(self, mint: str) -> int | None:
        """Count SPL token accounts holding `mint` — heavy call, prefer
        Helius RPC. Returns None on public-RPC rate-limit / failure."""
        try:
            result = await self._rpc(
                "getProgramAccounts",
                [
                    SPL_TOKEN_PROGRAM,
                    {
                        "encoding": "jsonParsed",
                        "filters": [
                            {"dataSize": 165},
                            {"memcmp": {"offset": 0, "bytes": mint}},
                        ],
                    },
                ],
            )
        except (httpx.HTTPError, RuntimeError) as e:
            logger.debug("getProgramAccounts failed for %s: %s", mint, e)
            return None
        if not isinstance(result, list):
            return None
        # Count only accounts with non-zero balance — ghost zero-balance
        # ATAs (closed wallets) shouldn't inflate the holder figure.
        non_zero = 0
        for entry in result:
            try:
                info = (
                    ((entry.get("account") or {}).get("data") or {})
                    .get("parsed", {})
                    .get("info", {})
                )
                amt = (info.get("tokenAmount") or {}).get("uiAmount")
                if amt is not None and float(amt) > 0:
                    non_zero += 1
            except (AttributeError, TypeError, ValueError):
                continue
        return non_zero


def concentration_share(
    holders: list[tuple[str, float]] | None, total_supply: float | None, top_k: int
) -> float | None:
    """Sum of the top-`top_k` holders' balances as a share of supply."""
    if not holders or not total_supply or total_supply <= 0:
        return None
    top = sorted(holders, key=lambda x: x[1], reverse=True)[:top_k]
    s = sum(b for (_, b) in top)
    return float(s / total_supply)
