"""Universe assembly: merge multi-source token lists, dedupe, apply gem filters.

The output is the small set of tokens that pass the hard gates and deserve
an OHLCV fetch + scoring round.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from scanner.config import Settings
from scanner.sources.base import Token

logger = logging.getLogger(__name__)


@dataclass
class FilterStats:
    raw_total: int = 0
    deduped: int = 0
    passed: int = 0
    rejected_mcap: int = 0
    rejected_age: int = 0
    rejected_liquidity: int = 0
    rejected_honeypot: int = 0
    rejected_missing_data: int = 0


def _merge(primary: Token, other: Token) -> Token:
    """Fill empty fields on `primary` from `other` without overwriting."""
    if primary.mcap_usd is None and other.mcap_usd is not None:
        primary.mcap_usd = other.mcap_usd
    if primary.vol_24h_usd is None and other.vol_24h_usd is not None:
        primary.vol_24h_usd = other.vol_24h_usd
    if primary.price_usd is None and other.price_usd is not None:
        primary.price_usd = other.price_usd
    if other.created_at is not None:
        if primary.created_at is None or other.created_at < primary.created_at:
            primary.created_at = other.created_at
    if not primary.coingecko_id and other.coingecko_id:
        primary.coingecko_id = other.coingecko_id
    if not primary.address and other.address:
        primary.address = other.address
        primary.chain = other.chain
    if not primary.pool_address and other.pool_address:
        primary.pool_address = other.pool_address
    if not primary.chart_url and other.chart_url:
        primary.chart_url = other.chart_url
    primary.suspected_honeypot = primary.suspected_honeypot or other.suspected_honeypot
    return primary


def dedupe(tokens: list[Token]) -> list[Token]:
    by_key: dict[str, Token] = {}
    by_symbol: dict[str, Token] = {}
    for tok in tokens:
        if tok.chain and tok.address:
            existing = by_key.get(tok.key)
            if existing:
                _merge(existing, tok)
            else:
                by_key[tok.key] = tok
        else:
            sym = tok.symbol.upper()
            existing_sym = by_symbol.get(sym)
            if existing_sym:
                _merge(existing_sym, tok)
            else:
                by_symbol[sym] = tok

    # Symbol-only entries (typically CoinGecko) merged into chain-keyed entries
    # when the symbol matches a unique chain entry — best-effort cross-link.
    for sym, ctok in list(by_symbol.items()):
        matches = [t for t in by_key.values() if t.symbol.upper() == sym]
        if len(matches) == 1:
            _merge(matches[0], ctok)
            by_symbol.pop(sym, None)

    return list(by_key.values()) + list(by_symbol.values())


def is_gem_candidate(token: Token, settings: Settings) -> tuple[bool, str]:
    if token.mcap_usd is None:
        return False, "missing_mcap"
    if token.mcap_usd < settings.mcap_min_usd or token.mcap_usd > settings.mcap_max_usd:
        return False, "mcap"
    if token.age_days is None:
        return False, "missing_age"
    if token.age_days < settings.min_age_days:
        return False, "age"
    if token.vol_24h_usd is None:
        return False, "missing_vol"
    if token.vol_24h_usd < settings.min_vol_24h_usd:
        return False, "liquidity"
    ratio = token.vol_mcap_ratio
    if ratio is None or ratio < settings.min_vol_mcap_ratio:
        return False, "liquidity"
    if token.suspected_honeypot:
        return False, "honeypot"
    return True, "ok"


def apply_filters(
    tokens: list[Token], settings: Settings
) -> tuple[list[Token], FilterStats]:
    stats = FilterStats(raw_total=len(tokens))
    deduped = dedupe(tokens)
    stats.deduped = len(deduped)
    passed: list[Token] = []
    for tok in deduped:
        ok, reason = is_gem_candidate(tok, settings)
        if ok:
            passed.append(tok)
            continue
        if reason == "mcap":
            stats.rejected_mcap += 1
        elif reason == "age" or reason == "missing_age":
            stats.rejected_age += 1
        elif reason == "liquidity" or reason == "missing_vol":
            stats.rejected_liquidity += 1
        elif reason == "honeypot":
            stats.rejected_honeypot += 1
        else:
            stats.rejected_missing_data += 1
    stats.passed = len(passed)
    logger.info(
        "universe filter: raw=%d deduped=%d passed=%d "
        "rej_mcap=%d rej_age=%d rej_liq=%d rej_honey=%d rej_missing=%d",
        stats.raw_total, stats.deduped, stats.passed,
        stats.rejected_mcap, stats.rejected_age, stats.rejected_liquidity,
        stats.rejected_honeypot, stats.rejected_missing_data,
    )
    return passed, stats
