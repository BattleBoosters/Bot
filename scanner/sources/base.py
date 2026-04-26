from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import pandas as pd


@dataclass
class Token:
    """Identity + last-known snapshot metadata for a candidate asset.

    Address+chain is the canonical key for DEX tokens; coingecko_id is the
    fallback when we only know the listed token. Symbol is informational.
    """

    symbol: str
    name: str = ""
    chain: str | None = None
    address: str | None = None
    coingecko_id: str | None = None

    mcap_usd: float | None = None
    vol_24h_usd: float | None = None
    price_usd: float | None = None
    created_at: datetime | None = None
    source: str = ""
    pool_address: str | None = None
    chart_url: str | None = None

    suspected_honeypot: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def age_days(self) -> float | None:
        if self.created_at is None:
            return None
        now = datetime.now(timezone.utc)
        delta = now - self.created_at
        return max(delta.total_seconds() / 86_400.0, 0.0)

    @property
    def vol_mcap_ratio(self) -> float | None:
        if self.mcap_usd and self.vol_24h_usd is not None and self.mcap_usd > 0:
            return self.vol_24h_usd / self.mcap_usd
        return None

    @property
    def key(self) -> str:
        if self.chain and self.address:
            return f"{self.chain}:{self.address.lower()}"
        if self.coingecko_id:
            return f"cg:{self.coingecko_id}"
        return f"sym:{self.symbol.upper()}"


class Source(Protocol):
    name: str

    async def list_universe(self) -> list[Token]: ...

    async def get_ohlcv(self, token: Token, days: int) -> pd.DataFrame:
        """Return a DataFrame indexed by daily UTC timestamp with columns
        open, high, low, close, volume. Empty DataFrame when unavailable."""
        ...
