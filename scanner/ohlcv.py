"""OHLCV fetch + parquet cache.

Cache key: (source, chain, address|cg_id) → parquet file under cache_dir.
We only cache the daily series; per-day reuse is cheap because we always
fetch a 30-day window and the file is small.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scanner.sources.base import Source, Token

logger = logging.getLogger(__name__)


def _cache_path(cache_dir: Path, source: str, token: Token) -> Path:
    if token.chain and token.address:
        key = f"{source}_{token.chain}_{token.address.lower()}"
    elif token.coingecko_id:
        key = f"{source}_cg_{token.coingecko_id}"
    else:
        key = f"{source}_sym_{token.symbol.upper()}"
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    safe_sym = "".join(c for c in token.symbol.upper() if c.isalnum())[:16] or "X"
    return cache_dir / source / f"{safe_sym}_{digest}.parquet"


def _is_fresh(path: Path, max_age_hours: float = 6.0) -> bool:
    if not path.exists():
        return False
    age = datetime.now(tz=timezone.utc).timestamp() - path.stat().st_mtime
    return age < max_age_hours * 3600


async def get_ohlcv_cached(
    source: Source,
    token: Token,
    days: int,
    cache_dir: Path,
    cache_max_age_hours: float = 6.0,
) -> pd.DataFrame:
    path = _cache_path(cache_dir, source.name, token)
    if _is_fresh(path, cache_max_age_hours):
        try:
            df = pd.read_parquet(path)
            if not df.empty:
                return df.tail(days)
        except Exception as e:
            logger.debug("cache read failed for %s: %s", token.symbol, e)
    df = await source.get_ohlcv(token, days)
    if df is None or df.empty:
        return pd.DataFrame()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path)
    except Exception as e:
        logger.debug("cache write failed for %s: %s", token.symbol, e)
    return df


async def fetch_many(
    source: Source,
    tokens: list[Token],
    days: int,
    cache_dir: Path,
    concurrency: int = 4,
    cache_max_age_hours: float = 6.0,
) -> dict[str, pd.DataFrame]:
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, pd.DataFrame] = {}

    async def one(tok: Token) -> None:
        async with sem:
            df = await get_ohlcv_cached(
                source, tok, days, cache_dir, cache_max_age_hours
            )
            if not df.empty:
                out[tok.key] = df

    await asyncio.gather(*(one(t) for t in tokens), return_exceptions=False)
    return out
