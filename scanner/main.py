"""CLI entrypoint for the gem momentum scanner.

Commands:
    config-check    Verify .env and ping Telegram.
    scan-once       Run the pipeline once. Use --dry-run to skip Telegram send.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx
import typer

from scanner.alerts import format_digest, ping_telegram, send_message
from scanner.config import Settings, get_settings
from scanner.ohlcv import fetch_many, get_ohlcv_cached
from scanner.scoring import ScoredCandidate, rank_candidates, score_token
from scanner.sources.base import Token
from scanner.sources.coingecko import CoinGeckoSource
from scanner.sources.dexscreener import DexscreenerSource
from scanner.sources.geckoterminal import GeckoTerminalSource
from scanner.state import AlertState
from scanner.universe import apply_filters

app = typer.Typer(add_completion=False, help="Crypto small-cap gem momentum scanner.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("scanner")


def _make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_seconds),
        headers={"user-agent": "crypto-momentum-scanner/0.1"},
        follow_redirects=True,
        http2=True,
    )


async def _gather_universe(
    gt: GeckoTerminalSource, cg: CoinGeckoSource
) -> list[Token]:
    gt_tokens, cg_tokens = await asyncio.gather(
        gt.list_universe(),
        cg.list_universe(),
        return_exceptions=False,
    )
    return list(gt_tokens) + list(cg_tokens)


async def _enrich_dexscreener(
    ds: DexscreenerSource, tokens: list[Token]
) -> None:
    needs = [
        t for t in tokens
        if t.address and (
            t.mcap_usd is None or t.created_at is None or t.vol_24h_usd is None
        )
    ]
    if not needs:
        return
    sem = asyncio.Semaphore(8)

    async def one(tok: Token) -> None:
        async with sem:
            await ds.enrich(tok)

    await asyncio.gather(*(one(t) for t in needs))


async def _scan_once(settings: Settings, dry_run: bool) -> int:
    async with _make_client(settings) as client:
        gt = GeckoTerminalSource(networks=settings.networks, client=client)
        ds = DexscreenerSource(client=client)
        cg = CoinGeckoSource(
            client=client,
            mcap_min=settings.mcap_min_usd,
            mcap_max=settings.mcap_max_usd,
            api_key=settings.coingecko_api_key,
        )

        logger.info("fetching universes (geckoterminal + coingecko)...")
        raw = await _gather_universe(gt, cg)
        logger.info("raw universe: %d entries", len(raw))

        logger.info("enriching DEX-side tokens via Dexscreener...")
        await _enrich_dexscreener(ds, raw)

        cg_needing_age = [
            t for t in raw if t.source == "coingecko" and t.created_at is None
        ][:50]
        if cg_needing_age:
            logger.info(
                "fetching genesis_date for %d coingecko tokens", len(cg_needing_age)
            )
            await cg.fetch_genesis_dates(cg_needing_age)

        candidates, stats = apply_filters(raw, settings)
        if not candidates:
            logger.info("no tokens passed gem filters")

        logger.info("fetching OHLCV for %d candidates...", len(candidates))
        chain_toks = [t for t in candidates if t.chain and t.pool_address]
        listed_toks = [t for t in candidates if t.coingecko_id and not (t.chain and t.pool_address)]

        chain_ohlcv = await fetch_many(
            gt, chain_toks, days=30, cache_dir=settings.cache_dir, concurrency=3
        )
        listed_ohlcv = await fetch_many(
            cg, listed_toks, days=30, cache_dir=settings.cache_dir, concurrency=2
        )
        ohlcv = {**chain_ohlcv, **listed_ohlcv}

        btc_token = Token(
            symbol="BTC", coingecko_id="bitcoin", source="coingecko"
        )
        btc_df = await get_ohlcv_cached(cg, btc_token, days=30, cache_dir=settings.cache_dir)

        scored: list[ScoredCandidate] = []
        for tok in candidates:
            df = ohlcv.get(tok.key)
            if df is None or df.empty:
                continue
            sc = score_token(tok, df, btc_df)
            scored.append(sc)

        ranked = rank_candidates(scored, settings.score_threshold, settings.top_n)
        logger.info(
            "scored=%d  qualified=%d  top=%d",
            len(scored),
            sum(1 for s in scored if s.rejection is None and s.score >= settings.score_threshold),
            len(ranked),
        )

        state = AlertState(settings.db_path)
        await state.init()
        fresh: list[ScoredCandidate] = []
        for c in ranked:
            ok = await state.should_alert(
                c.token.key,
                c.score,
                cooldown_days=settings.realert_cooldown_days,
            )
            if ok:
                fresh.append(c)

        digest = format_digest(
            fresh,
            universe_size=stats.deduped,
            candidates_total=len([s for s in scored if s.rejection is None and s.score >= settings.score_threshold]),
            top_n=settings.top_n,
        )

        if dry_run:
            print(digest)
            return 0

        if not settings.telegram_configured():
            print(digest)
            logger.warning("telegram not configured; printed digest to stdout instead")
            return 0

        sent = await send_message(
            client,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            digest,
        )
        if sent and fresh:
            for c in fresh:
                await state.mark_alerted(c.token.key, c.token.symbol, c.score)
        return 0 if sent else 2


async def _config_check(settings: Settings) -> int:
    print("== config check ==")
    print(f"mcap window:   ${settings.mcap_min_usd:,.0f} – ${settings.mcap_max_usd:,.0f}")
    print(f"min age:       {settings.min_age_days} days")
    print(f"vol/mcap min:  {settings.min_vol_mcap_ratio:.2%}")
    print(f"vol24h min:    ${settings.min_vol_24h_usd:,.0f}")
    print(f"score thresh:  {settings.score_threshold}")
    print(f"top N:         {settings.top_n}")
    print(f"networks:      {settings.networks}")
    print(f"cache dir:     {settings.cache_dir}")
    print(f"db path:       {settings.db_path}")
    print(f"telegram cfg:  {settings.telegram_configured()}")
    if not settings.telegram_configured():
        print("  -> set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return 1
    async with _make_client(settings) as client:
        ok, info = await ping_telegram(
            client, settings.telegram_bot_token, settings.telegram_chat_id
        )
        if not ok:
            print(f"  -> telegram ping FAILED: {info}")
            return 2
        print(f"  -> telegram bot OK: @{info}")
    return 0


@app.command("config-check")
def config_check_cmd() -> None:
    """Validate .env and ping the Telegram bot."""
    settings = get_settings()
    code = asyncio.run(_config_check(settings))
    raise typer.Exit(code)


@app.command("scan-once")
def scan_once_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print digest to stdout instead of Telegram."
    ),
) -> None:
    """Run the full scan pipeline once."""
    settings = get_settings()
    try:
        code = asyncio.run(_scan_once(settings, dry_run))
    except KeyboardInterrupt:
        sys.exit(130)
    raise typer.Exit(code)


if __name__ == "__main__":
    app()
