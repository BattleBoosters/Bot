# Crypto Long-Term Uptrend Scanner

Scanner that hunts **small-cap crypto gems** ($1M–$100M mcap) whose **all-time chart shows a real, sustained uptrend** with active buying pressure right now — not 3-day spikes. Pushes a ranked digest + a CSV of every qualified candidate to Telegram. Signal-only — no execution, no private keys.

## What it does

For every token in the gem mcap window across CoinGecko + GeckoTerminal (DEX, ~150 chains via Solana / Ethereum / Base / Arbitrum / BSC), the scanner:

1. Filters on hard gates: mcap ∈ [$1M, $100M], age ≥ 14 days, vol/mcap ≥ 5%, vol24h ≥ $100k, no wash-trade / honeypot flags.
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
| `SCANNER_MCAP_MAX_USD` | 100_000_000 | Upper mcap bound |
| `SCANNER_MIN_AGE_DAYS` | 14 | Reject anything younger |
| `SCANNER_MIN_VOL_MCAP_RATIO` | 0.05 | vol24h/mcap floor |
| `SCANNER_MIN_VOL_24H_USD` | 100_000 | Absolute vol24h floor |
| `SCANNER_SCORE_THRESHOLD` | 0.55 | Composite score cut |
| `SCANNER_WATCHLIST_THRESHOLD` | 0.40 | Min score to keep on the watchlist |
| `SCANNER_HIGHLIGHT_TOP_N` | 10 | Detailed entries in the header message |
| `SCANNER_CHART_TOP_N` | 5 | How many top candidates get a chart PNG attached |
| `SCANNER_OHLCV_DAYS` | 365 | OHLCV history window pulled per token |
| `SCANNER_NETWORKS` | solana,eth,base,arbitrum,bsc | DEX chains scanned |
| `SCANNER_REALERT_COOLDOWN_DAYS` | 5 | Days before re-alerting |
| `SCANNER_REJECT_WASH_TRADE` | true | Drop tokens whose tx pattern looks washed |
| `SCANNER_FULL_SCAN_HOURS` | 1 | UTC hours for daemon full scans (CSV) |
| `SCANNER_FULL_SCAN_MINUTE` | 30 | Minute-of-hour for full scans |
| `SCANNER_WATCHLIST_SCAN_MINUTES` | 240 | Watchlist delta scan period |

## Layout

```
scanner/
  config.py         pydantic-settings
  sources/
    base.py           Source Protocol + Token model
    geckoterminal.py  primary DEX universe + OHLCV + wash-trade signals
    dexscreener.py    enrichment (mcap, age, liquidity)
    coingecko.py      listed cross-source + BTC OHLCV
  universe.py       merge + dedupe + gem filters
  ohlcv.py          parquet cache layer
  indicators.py     MA, RSI, perf, up_days, log-slope, ATH drawdown, weekly regularity
  scoring.py        long-term composite score + ranking
  chart.py          mplfinance PNG renderer
  alerts.py         Telegram bot API (sendMessage + sendPhoto + sendDocument) + CSV
  state.py          SQLite dedupe + watchlist
  metrics.py        per-scan stats (counts, errors, durations)
  main.py           Typer CLI (config-check / scan-once / watchlist-scan / run)
tests/              fixture-based unit tests
```

## CSV columns

The attachment includes (in order): `rank, symbol, name, score, venue, chain, address, coingecko_id, mcap_usd, vol_24h_usd, vol_mcap_ratio, age_days, perf_7d, perf_14d, perf_30d, perf_60d, perf_90d, annualised_growth, drawdown_from_ath, weeks_up_12, rsi_14, btc_perf_30d, log_slope_per_day, history_days, factor_*, wash_warning, one_sided_warning, honeypot, chart_url`.

## Phase 3 ideas (not yet built)

- Optional Sonnet 4.6 LLM filter (toggle via `SCANNER_LLM_FILTER_ENABLED=true`)
- Birdeye Solana paid tier for deeper holder/wash data
- CoinGecko Pro tier to scan top 5000 listed
- Backtest harness on archived OHLCV to tune weights
