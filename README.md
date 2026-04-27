# Crypto Long-Term Uptrend + Accumulation Scanner

Two-layer scanner that hunts **small-cap crypto gems** ($1M–$300M mcap) and pushes:

1. **🚀 Trending now** — tokens whose all-time chart shows a real, sustained uptrend with active buying pressure right now (lagging price signal).
2. **🕵️ Quietly accumulated** — tokens being silently accumulated *before* they pump, detected from on-chain holder growth, top-20 distribution shifts, Wyckoff range compression, and pool TVL inflows (leading on-chain signal).

Each layer ships a header digest + chart PNGs + a CSV attachment. Signal-only — no execution, no private keys.

## What it does

For every token in the gem mcap window across CoinGecko + GeckoTerminal (DEX, ~150 chains via Solana / Ethereum / Base / Arbitrum / BSC), the scanner:

1. Filters on hard gates: mcap ∈ [$1M, $300M], age ≥ 14 days, vol/mcap ≥ 5%, vol24h ≥ $100k, no wash-trade / honeypot flags.
2. Pulls **365 days of daily OHLCV** (or as much history as exists) and computes a long-term composite score:
   - **Trend gate** (close > MA20 > MA50) — hard gate.
   - **Slope** of log-price linear regression across the whole series, expressed as annualised growth.
   - **ATH proximity** — distance from the rolling all-time high (1.0 at the high, 0 at -25%).
   - **Perf consistency** — 7d/14d/30d all positive with magnitude blended in.
   - **Volume sustained** — median 14d vs median 60d (capital staying in).
   - **Relative strength vs BTC** over 30 days.
   - **RSI(14)** — plateau in [40, 75], decay to 0 at 88 (terminal overbought penalty).
3. **Sends every candidate** ≥ score threshold (no top-N cap):
   - **Header message** with the run summary and the top-10 highlights detailed.
   - **Chart PNGs** for the top 5 (configurable).
   - **CSV attachment** with all qualified candidates — open in a spreadsheet to filter however you want.
4. Dedupes via SQLite: 5-day cooldown unless a token's score jumps ≥ 0.10 since the last alert.

### On-chain accumulation layer (the "before the pump" signal)

After the trend digest, the scanner snapshots Solana SPL token state via Helius RPC and runs a parallel composite score:

- **Wyckoff compression** — recent ATR / baseline ATR (range tightening) + median 14d / median 90d volume (capital entering).
- **Holder growth (14d)** — % delta in unique holder count, computed from SQLite snapshot history. The most powerful leading indicator. Comes online once the snapshot history is at least 14 days deep — first weeks the bot runs are warm-up.
- **Distribution** — top-20 holder share absolute level + 14-day delta. Lower / falling = healthy distribution; high / rising = whale-controlled.
- **TVL growth** — pool TVL trend over 14 days.
- **Buy pressure** — buyers vs sellers excess ratio sustained from GeckoTerminal h24 stats.

Helius free tier (100k req/mo) is enough for ~200 tokens/scan. Without an API key the public Solana RPC is used as a fallback (heavily throttled — top-20 concentration still works, holder count likely won't).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]' --pre
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
scanner config-check
```

Create the bot via [@BotFather](https://t.me/BotFather), then send any message to it and find your chat ID at `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Run

```bash
# print the digest to stdout, no Telegram send
scanner scan-once --dry-run

# real run — sends header + charts + CSV to Telegram
scanner scan-once

# delta scan over the watchlist (forced fresh OHLCV)
scanner watchlist-scan

# long-running daemon: scheduled full scans + watchlist deltas
scanner run
```

For the daemon, prefer running under systemd:

```ini
# /etc/systemd/system/scanner.service
[Unit]
Description=Crypto Long-Term Uptrend Scanner
After=network.target

[Service]
WorkingDirectory=/path/to/Bot
ExecStart=/path/to/Bot/.venv/bin/scanner run
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
```

Or as a cron alternative:

```cron
30 1 * * *  cd /path/to/Bot && /path/to/Bot/.venv/bin/scanner scan-once >> /var/log/scanner.log 2>&1
```

## Tests

```bash
pytest -q
```

## Tunables

All thresholds live in `.env` and override the defaults in `scanner/config.py`:

| Var | Default | Meaning |
|---|---|---|
| `SCANNER_MCAP_MIN_USD` | 1_000_000 | Lower mcap bound |
| `SCANNER_MCAP_MAX_USD` | 300_000_000 | Upper mcap bound. Push to 500M to catch "second leg" plays. |
| `SCANNER_MIN_AGE_DAYS` | 14 | Reject anything younger |
| `SCANNER_MIN_VOL_MCAP_RATIO` | 0.05 | vol24h/mcap floor |
| `SCANNER_MIN_VOL_24H_USD` | 100_000 | Absolute vol24h floor |
| `SCANNER_SCORE_THRESHOLD` | 0.55 | Composite score cut |
| `SCANNER_WATCHLIST_THRESHOLD` | 0.40 | Min score to keep on the watchlist |
| `SCANNER_HIGHLIGHT_TOP_N` | 10 | Detailed entries in the header message |
| `SCANNER_CHART_TOP_N` | 5 | How many top candidates get a chart PNG attached |
| `SCANNER_OHLCV_DAYS` | 365 | OHLCV history window pulled per token |
| `SCANNER_NETWORKS` | solana,eth,base,arbitrum,bsc | DEX chains scanned |
| `SCANNER_GT_TOP_POOLS_PAGES` | 5 | GT pages of top-by-volume pools per chain (~100 pools/page) |
| `SCANNER_INCLUDE_POST_PEAK` | false | When true, tokens up to -50% from ATH still pass proximity (catches second-leg setups) |
| `SCANNER_REALERT_COOLDOWN_DAYS` | 5 | Days before re-alerting |
| `SCANNER_REJECT_WASH_TRADE` | true | Drop tokens whose tx pattern looks washed |
| `SCANNER_FULL_SCAN_HOURS` | 1 | UTC hours for daemon full scans (CSV) |
| `SCANNER_FULL_SCAN_MINUTE` | 30 | Minute-of-hour for full scans |
| `SCANNER_WATCHLIST_SCAN_MINUTES` | 240 | Watchlist delta scan period |
| `SCANNER_ACCUMULATION_ENABLED` | true | Toggle the on-chain accumulation layer |
| `SCANNER_ACCUMULATION_THRESHOLD` | 0.50 | Composite accumulation score cut |
| `SCANNER_ONCHAIN_MAX_TOKENS` | 200 | Per-scan budget for Helius RPC calls |
| `SCANNER_ONCHAIN_CONCURRENCY` | 4 | Parallel snapshot workers |
| `HELIUS_API_KEY` | _empty_ | Solana RPC API key (free 100k req/mo at helius.dev) |

## Layout

```
scanner/
  config.py         pydantic-settings
  sources/
    base.py           Source Protocol + Token model
    geckoterminal.py  primary DEX universe + OHLCV + wash-trade signals
    dexscreener.py    enrichment (mcap, age, liquidity)
    coingecko.py      listed cross-source + BTC OHLCV
  onchain/
    helius.py         Solana RPC client (top holders, supply, holder count)
  universe.py       merge + dedupe + gem filters
  ohlcv.py          parquet cache layer
  indicators.py     MA, RSI, perf, log-slope, ATH drawdown, weekly regularity, Wyckoff
  scoring.py        long-term TREND composite score + ranking
  accumulation.py   on-chain ACCUMULATION composite score + ranking
  chart.py          mplfinance PNG renderer
  alerts.py         Telegram bot API + dual digest builders + CSV builders
  state.py          SQLite dedupe + watchlist + holder snapshot history
  metrics.py        per-scan stats (counts, errors, durations)
  main.py           Typer CLI (config-check / scan-once / watchlist-scan / run)
tests/              fixture-based unit tests, 87 tests
```

## CSV columns

The attachment includes (in order): `rank, symbol, name, score, venue, chain, address, coingecko_id, mcap_usd, vol_24h_usd, vol_mcap_ratio, age_days, perf_7d, perf_14d, perf_30d, perf_60d, perf_90d, annualised_growth, drawdown_from_ath, weeks_up_12, rsi_14, btc_perf_30d, log_slope_per_day, history_days, factor_*, wash_warning, one_sided_warning, honeypot, chart_url`.

## Phase 3 ideas (not yet built)

- Optional Sonnet 4.6 LLM filter (toggle via `SCANNER_LLM_FILTER_ENABLED=true`)
- Birdeye Solana paid tier for deeper holder/wash data
- CoinGecko Pro tier to scan top 5000 listed
- Backtest harness on archived OHLCV to tune weights
