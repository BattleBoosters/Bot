"""CLI entrypoint for the gem long-term uptrend scanner.

Commands:
    config-check    Verify .env and ping Telegram.
    scan-once       Run one full scan (universe → score → alert).
    watchlist-scan  Run one watchlist delta scan (forced fresh OHLCV, alert
                    only on tokens crossing the score threshold).
    run             Long-running daemon: scheduled full scans (default 1×/day
                    at 01:30 UTC) plus periodic watchlist delta scans
                    (default every 240 min).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import typer

from scanner.accumulation import (
    AccumulationCandidate,
    rank_accumulation,
    score_accumulation,
)
from scanner.alerts import (
    build_accumulation_digest,
    build_csv,
    format_card,
    format_digest,
    ping_telegram,
    send_document,
    send_message,
    send_photo,
)
from scanner.onchain.helius import HeliusClient, concentration_share
from scanner.chart import render_ohlcv_png
from scanner.config import Settings, get_settings
from scanner.metrics import ScanStats
from scanner.ohlcv import fetch_many, get_ohlcv_cached
from scanner.scoring import ScoredCandidate, rank_candidates, score_token
from scanner.sources.base import Source, Token
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


@dataclass
class Sources:
    gt: GeckoTerminalSource
    ds: DexscreenerSource
    cg: CoinGeckoSource
    helius: HeliusClient


def _make_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.request_timeout_seconds),
        headers={"user-agent": "crypto-momentum-scanner/0.1"},
        follow_redirects=True,
        http2=True,
    )


def _build_sources(settings: Settings, client: httpx.AsyncClient) -> Sources:
    return Sources(
        gt=GeckoTerminalSource(
            networks=settings.networks,
            client=client,
            top_pools_pages=settings.gt_top_pools_pages,
        ),
        ds=DexscreenerSource(client=client),
        cg=CoinGeckoSource(
            client=client,
            mcap_min=settings.mcap_min_usd,
            mcap_max=settings.mcap_max_usd,
            api_key=settings.coingecko_api_key,
        ),
        helius=HeliusClient(client=client, api_key=settings.helius_api_key),
    )


async def _snapshot_onchain(
    tokens: list[Token],
    helius: HeliusClient,
    state: AlertState,
    settings: Settings,
    stats: ScanStats,
) -> dict[str, dict]:
    """Snapshot on-chain accumulation signals for Solana SPL tokens.

    Skips tokens with no Solana address and tokens beyond the
    `onchain_max_tokens` budget (we cap to keep one scan within Helius's
    free-tier rate limit). Returns a {token_key: latest_snapshot} dict
    for the scoring stage.
    """
    out: dict[str, dict] = {}
    if not settings.accumulation_enabled:
        return out

    sol = [
        t for t in tokens
        if t.chain == "solana" and t.address and not t.address.startswith("0x")
    ][: settings.onchain_max_tokens]
    if not sol:
        return out

    sem = asyncio.Semaphore(settings.onchain_concurrency)

    async def one(tok: Token) -> None:
        async with sem:
            try:
                top = await helius.get_top_holders(tok.address)
                supply = await helius.get_token_supply(tok.address)
            except Exception as e:
                logger.debug("onchain snapshot failed for %s: %s", tok.symbol, e)
                stats.bump_error("helius")
                return
            top10 = concentration_share(top, supply, 10)
            top20 = concentration_share(top, supply, 20)

            holder_count: int | None = None
            if helius.configured:
                try:
                    holder_count = await helius.get_holder_count(tok.address)
                except Exception as e:
                    logger.debug("get_holder_count failed for %s: %s", tok.symbol, e)
                    stats.bump_error("helius")

            tx = (tok.extra or {}).get("tx_h24") or {}
            await state.record_holder_snapshot(
                tok.key,
                holder_count=holder_count,
                top10_share=top10,
                top20_share=top20,
                tvl_usd=tok.vol_24h_usd,  # GT exposes liquidity_usd via extra; fallback
                buyers_h24=tx.get("buyers"),
                sellers_h24=tx.get("sellers"),
            )
            out[tok.key] = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "holder_count": holder_count,
                "top10_share": top10,
                "top20_share": top20,
                "tvl_usd": tok.vol_24h_usd,
                "buyers_h24": tx.get("buyers"),
                "sellers_h24": tx.get("sellers"),
            }

    await asyncio.gather(*(one(t) for t in sol))
    return out


async def _score_accumulation_layer(
    candidates: list[Token],
    ohlcv: dict,
    state: AlertState,
    settings: Settings,
) -> list[AccumulationCandidate]:
    """Run the accumulation scorer over every universe candidate using the
    OHLCV we already fetched plus any historical snapshot rows."""
    if not settings.accumulation_enabled:
        return []
    out: list[AccumulationCandidate] = []
    for tok in candidates:
        df = ohlcv.get(tok.key)
        snaps = await state.load_holder_snapshots(tok.key, max_age_days=90)
        sc = score_accumulation(tok, df, snaps)
        out.append(sc)
    return out


async def _gather_universe(srcs: Sources, stats: ScanStats) -> list[Token]:
    async def safe(src: Source) -> list[Token]:
        try:
            return await src.list_universe()
        except Exception as e:
            logger.warning("%s.list_universe failed: %s", src.name, e)
            stats.bump_error(src.name)
            return []

    gt_tokens, cg_tokens = await asyncio.gather(safe(srcs.gt), safe(srcs.cg))
    return list(gt_tokens) + list(cg_tokens)


async def _enrich_dexscreener(
    ds: DexscreenerSource, tokens: list[Token], stats: ScanStats
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
            try:
                await ds.enrich(tok)
            except Exception as e:
                logger.debug("dexscreener enrich error for %s: %s", tok.symbol, e)
                stats.bump_error(ds.name)

    await asyncio.gather(*(one(t) for t in needs))


async def _score_candidates(
    candidates: list[Token],
    srcs: Sources,
    settings: Settings,
    stats: ScanStats,
    force_fresh: bool = False,
) -> tuple[list[ScoredCandidate], dict, "object"]:
    chain_toks = [t for t in candidates if t.chain and t.pool_address]
    listed_toks = [t for t in candidates if t.coingecko_id and not (t.chain and t.pool_address)]

    cache_age = 0.0 if force_fresh else 6.0
    days = settings.ohlcv_days
    chain_ohlcv = await fetch_many(
        srcs.gt, chain_toks, days=days, cache_dir=settings.cache_dir, concurrency=3,
        cache_max_age_hours=cache_age,
    )
    listed_ohlcv = await fetch_many(
        srcs.cg, listed_toks, days=days, cache_dir=settings.cache_dir, concurrency=2,
        cache_max_age_hours=cache_age,
    )
    ohlcv = {**chain_ohlcv, **listed_ohlcv}

    btc_token = Token(symbol="BTC", coingecko_id="bitcoin", source="coingecko")
    btc_df = await get_ohlcv_cached(
        srcs.cg, btc_token, days=days, cache_dir=settings.cache_dir,
        cache_max_age_hours=cache_age,
    )

    scored: list[ScoredCandidate] = []
    for tok in candidates:
        df = ohlcv.get(tok.key)
        if df is None or df.empty:
            stats.ohlcv_misses += 1
            continue
        sc = score_token(tok, df, btc_df, include_post_peak=settings.include_post_peak)
        scored.append(sc)
    stats.candidates_scored = len(scored)
    return scored, ohlcv, btc_df


async def _send_alerts(
    client: httpx.AsyncClient,
    settings: Settings,
    fresh: list[ScoredCandidate],
    ohlcv: dict,
    digest: str,
    state: AlertState,
    dry_run: bool,
    stats: ScanStats,
    csv_filename: str = "gems.csv",
) -> bool:
    if dry_run:
        print(digest)
        if fresh:
            print(f"\n[csv would be attached: {len(fresh)} rows]")
        return True

    if not settings.telegram_configured():
        print(digest)
        logger.warning("telegram not configured; printed digest to stdout instead")
        return True

    sent = await send_message(
        client,
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        digest,
    )
    if not sent:
        return False

    chart_count = min(settings.chart_top_n, len(fresh))
    for c in fresh[:chart_count]:
        df = ohlcv.get(c.token.key)
        if df is None or df.empty:
            continue
        title = f"{c.token.symbol} — score {c.score:.2f}"
        png = render_ohlcv_png(df, title)
        if not png:
            continue
        await send_photo(
            client,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            png,
            caption=format_card(c),
            filename=f"{c.token.symbol}.png",
        )

    if fresh:
        csv_bytes = build_csv(fresh)
        await send_document(
            client,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            csv_bytes,
            filename=csv_filename,
            caption=f"{len(fresh)} candidats triés par score (CSV).",
        )
        for c in fresh:
            await state.mark_alerted(c.token.key, c.token.symbol, c.score)
        stats.alerts_sent = len(fresh)
    return True


async def _send_accumulation(
    client: httpx.AsyncClient,
    settings: Settings,
    candidates: list[AccumulationCandidate],
    ohlcv: dict,
    digest: str,
    dry_run: bool,
    stats: ScanStats,
    csv_filename: str = "accumulation.csv",
) -> bool:
    if dry_run:
        print("\n--- ACCUMULATION ---")
        print(digest)
        if candidates:
            print(f"\n[acc csv would be attached: {len(candidates)} rows]")
        return True

    if not settings.telegram_configured():
        print("\n--- ACCUMULATION ---")
        print(digest)
        return True

    from scanner.alerts import build_accumulation_csv

    sent = await send_message(
        client,
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        digest,
    )
    if not sent:
        return False

    chart_count = min(settings.chart_top_n, len(candidates))
    for c in candidates[:chart_count]:
        df = ohlcv.get(c.token.key)
        if df is None or df.empty:
            continue
        title = f"{c.token.symbol} — accumulation {c.score:.2f}"
        png = render_ohlcv_png(df, title)
        if not png:
            continue
        caption = (
            f"🕵️ {c.token.symbol} acc {c.score:.2f}  "
            f"wyckoff {c.factors.get('wyckoff', 0):.2f}  "
            f"holders {c.factors.get('holder_growth', 0):.2f}  "
            f"distrib {c.factors.get('distribution', 0):.2f}"
        )
        await send_photo(
            client,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            png,
            caption=caption,
            filename=f"{c.token.symbol}_acc.png",
        )

    if candidates:
        await send_document(
            client,
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            build_accumulation_csv(candidates),
            filename=csv_filename,
            caption=f"{len(candidates)} candidats accumulation triés par score.",
        )
        stats.accumulation_alerts = len(candidates)
    return True


async def run_full_scan(settings: Settings, dry_run: bool = False) -> int:
    stats = ScanStats()
    async with _make_client(settings) as client:
        srcs = _build_sources(settings, client)

        logger.info("[full] fetching universes...")
        raw = await _gather_universe(srcs, stats)
        stats.universe_raw = len(raw)
        logger.info("[full] raw universe: %d entries", len(raw))

        logger.info("[full] enriching DEX-side tokens via Dexscreener...")
        await _enrich_dexscreener(srcs.ds, raw, stats)

        cg_needing_age = [
            t for t in raw if t.source == "coingecko" and t.created_at is None
        ][:50]
        if cg_needing_age:
            logger.info(
                "[full] fetching genesis_date for %d coingecko tokens",
                len(cg_needing_age),
            )
            try:
                await srcs.cg.fetch_genesis_dates(cg_needing_age)
            except Exception as e:
                logger.warning("genesis_date enrichment failed: %s", e)
                stats.bump_error("coingecko")

        passed, ustats = apply_filters(raw, settings)
        stats.universe_deduped = ustats.deduped
        stats.universe_passed = ustats.passed
        stats.rejected_mcap = ustats.rejected_mcap
        stats.rejected_age = ustats.rejected_age
        stats.rejected_liquidity = ustats.rejected_liquidity
        stats.rejected_honeypot = ustats.rejected_honeypot
        stats.rejected_wash = ustats.rejected_wash
        stats.rejected_missing = ustats.rejected_missing_data

        logger.info("[full] fetching OHLCV for %d candidates...", len(passed))
        scored, ohlcv, _btc_df = await _score_candidates(
            passed, srcs, settings, stats, force_fresh=False
        )

        qualified = [
            s for s in scored if s.rejection is None and s.score >= settings.score_threshold
        ]
        stats.candidates_qualified = len(qualified)
        # Send everything that clears the score threshold — no top_n cap.
        ranked = rank_candidates(scored, settings.score_threshold, top_n=None)

        state = AlertState(settings.db_path)
        await state.init()

        # Refresh watchlist with tokens scoring above the watchlist threshold,
        # whether or not they made the alert cut.
        for s in scored:
            if s.rejection is None and s.score >= settings.watchlist_threshold:
                await state.upsert_watchlist(s.token, s.score)
        await state.prune_watchlist(max_age_hours=48)
        wl = await state.load_watchlist()
        stats.watchlist_size = len(wl)

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
            universe_size=ustats.deduped,
            candidates_total=len(qualified),
            highlight_top_n=settings.highlight_top_n,
            mcap_window_str=settings.mcap_window_str,
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        ok = await _send_alerts(
            client, settings, fresh, ohlcv, digest, state, dry_run, stats,
            csv_filename=f"gems_{ts}.csv",
        )

        # ----- Accumulation layer (pro-elite) -----
        if settings.accumulation_enabled:
            logger.info("[full] taking on-chain accumulation snapshots...")
            await _snapshot_onchain(passed, srcs.helius, state, settings, stats)
            await state.prune_holder_snapshots(max_age_days=180)

            logger.info("[full] scoring accumulation layer...")
            acc_scored = await _score_accumulation_layer(
                passed, ohlcv, state, settings
            )
            acc_ranked = rank_accumulation(
                acc_scored, settings.accumulation_threshold, top_n=None
            )
            stats.accumulation_scored = len(acc_scored)
            stats.accumulation_qualified = len(acc_ranked)

            if acc_ranked:
                acc_digest = build_accumulation_digest(
                    acc_ranked,
                    universe_size=ustats.deduped,
                    highlight_top_n=settings.highlight_top_n,
                )
                await _send_accumulation(
                    client, settings, acc_ranked, ohlcv, acc_digest, dry_run, stats,
                    csv_filename=f"accumulation_{ts}.csv",
                )

    logger.info("[full] %s", stats.finish().summary_line())
    return 0 if ok else 2


async def run_watchlist_scan(settings: Settings, dry_run: bool = False) -> int:
    stats = ScanStats()
    async with _make_client(settings) as client:
        srcs = _build_sources(settings, client)
        state = AlertState(settings.db_path)
        await state.init()

        watchlist = await state.load_watchlist()
        stats.watchlist_size = len(watchlist)
        if not watchlist:
            logger.info("[wl] watchlist empty, nothing to do")
            return 0

        logger.info("[wl] re-scoring %d watchlist tokens (forced fresh OHLCV)", len(watchlist))
        scored, ohlcv, _ = await _score_candidates(
            watchlist, srcs, settings, stats, force_fresh=True
        )
        stats.universe_raw = len(watchlist)
        stats.universe_deduped = len(watchlist)
        stats.universe_passed = len(watchlist)

        qualified = [
            s for s in scored if s.rejection is None and s.score >= settings.score_threshold
        ]
        stats.candidates_qualified = len(qualified)
        ranked = rank_candidates(scored, settings.score_threshold, top_n=None)

        fresh: list[ScoredCandidate] = []
        for c in ranked:
            ok = await state.should_alert(
                c.token.key,
                c.score,
                cooldown_days=settings.realert_cooldown_days,
            )
            if ok:
                fresh.append(c)

        if not fresh:
            logger.info("[wl] no watchlist tokens cleared the alert threshold")
            logger.info("[wl] %s", stats.finish().summary_line())
            return 0

        digest = "🔔 Watchlist delta — " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        digest += f"\n{len(fresh)} token(s) viennent de passer le seuil:\n\n"
        digest += format_digest(
            fresh,
            universe_size=len(watchlist),
            candidates_total=len(qualified),
            highlight_top_n=settings.highlight_top_n,
        )

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        ok = await _send_alerts(
            client, settings, fresh, ohlcv, digest, state, dry_run, stats,
            csv_filename=f"watchlist_{ts}.csv",
        )

    logger.info("[wl] %s", stats.finish().summary_line())
    return 0 if ok else 2


def _next_full_scan(now: datetime, hours: list[int], minute: int) -> datetime:
    today = now.replace(minute=minute, second=0, microsecond=0)
    candidates = [today.replace(hour=h) for h in hours]
    future = [c for c in candidates if c > now]
    if future:
        return min(future)
    tomorrow = today + timedelta(days=1)
    return tomorrow.replace(hour=hours[0])


def _next_watchlist_scan(now: datetime, every_minutes: int) -> datetime:
    base = now.replace(second=0, microsecond=0)
    delta = timedelta(minutes=every_minutes)
    return base + delta


async def run_daemon(settings: Settings) -> int:
    logger.info(
        "starting daemon: full scans at %s:%02d UTC, watchlist every %d min",
        settings.full_scan_hours, settings.full_scan_minute,
        settings.watchlist_scan_minutes,
    )
    next_wl = _next_watchlist_scan(datetime.now(timezone.utc), settings.watchlist_scan_minutes)
    while True:
        now = datetime.now(timezone.utc)
        next_full = _next_full_scan(now, settings.full_scan_hours, settings.full_scan_minute)
        if next_wl <= now:
            next_wl = _next_watchlist_scan(now, settings.watchlist_scan_minutes)
        next_run = min(next_full, next_wl)
        wait = max(0.0, (next_run - now).total_seconds())
        logger.info("daemon sleeping %.0fs until %s", wait, next_run.isoformat())
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            logger.info("daemon cancelled")
            return 0

        try:
            if next_run == next_full:
                await run_full_scan(settings, dry_run=False)
            else:
                await run_watchlist_scan(settings, dry_run=False)
                next_wl = _next_watchlist_scan(
                    datetime.now(timezone.utc), settings.watchlist_scan_minutes
                )
        except Exception as e:
            logger.exception("scan failed: %s", e)
            await asyncio.sleep(30)


async def _config_check(settings: Settings) -> int:
    print("== config check ==")
    print(f"mcap window:   ${settings.mcap_min_usd:,.0f} – ${settings.mcap_max_usd:,.0f}")
    print(f"min age:       {settings.min_age_days} days")
    print(f"vol/mcap min:  {settings.min_vol_mcap_ratio:.2%}")
    print(f"vol24h min:    ${settings.min_vol_24h_usd:,.0f}")
    print(f"score thresh:  {settings.score_threshold}  (watchlist {settings.watchlist_threshold})")
    print(f"highlights:    top {settings.highlight_top_n}  (charts: top {settings.chart_top_n})")
    print(f"OHLCV window:  {settings.ohlcv_days} days")
    print(f"accumulation:  {'on' if settings.accumulation_enabled else 'off'}  "
          f"thresh {settings.accumulation_threshold}  "
          f"(helius={'configured' if settings.helius_api_key else 'public RPC'})")
    print(f"reject wash:   {settings.reject_wash_trade}")
    print(f"networks:      {settings.networks}")
    print(f"full scans:    {settings.full_scan_hours} at :{settings.full_scan_minute:02d} UTC")
    print(f"watchlist:     every {settings.watchlist_scan_minutes} min")
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
    """Run one full scan."""
    settings = get_settings()
    try:
        code = asyncio.run(run_full_scan(settings, dry_run))
    except KeyboardInterrupt:
        sys.exit(130)
    raise typer.Exit(code)


@app.command("watchlist-scan")
def watchlist_scan_cmd(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print digest to stdout instead of Telegram."
    ),
) -> None:
    """Run one watchlist delta scan (forced fresh OHLCV)."""
    settings = get_settings()
    try:
        code = asyncio.run(run_watchlist_scan(settings, dry_run))
    except KeyboardInterrupt:
        sys.exit(130)
    raise typer.Exit(code)


@app.command("run")
def run_cmd() -> None:
    """Run as a long-lived daemon: scheduled full scans + watchlist deltas."""
    settings = get_settings()
    try:
        code = asyncio.run(run_daemon(settings))
    except KeyboardInterrupt:
        sys.exit(130)
    raise typer.Exit(code)


if __name__ == "__main__":
    app()
