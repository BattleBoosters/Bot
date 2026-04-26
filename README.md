# Crypto Momentum Scanner

Daily scanner that hunts **small-cap crypto gems** ($1M–$100M mcap) with sustained 3–10 day uptrends and pushes a ranked digest to Telegram. Signal-only — no execution, no private keys.

## What it does

For every token in the gem mcap window across CoinGecko + GeckoTerminal (DEX, ~150 chains via Solana / Ethereum / Base / Arbitrum / BSC), the scanner:

1. Filters on hard gates: mcap ∈ [$1M, $100M], age ≥ 5 days, vol/mcap ≥ 5%, vol24h ≥ $100k.
2. Computes a multi-factor composite score:
   - Trend gate (close > MA10 > MA30) — hard gate.
   - 7-day and 3-day performance, acceleration ratio.
   - 7-day regularity (up-days, closes vs MA10).
   - 3-vs-14-day median volume confirmation.
   - RSI(14) with plateau in [50, 75] and decay above.
   - 7-day relative strength vs BTC.
   - Liquidity floor (log-scaled vol24h).
3. Ranks by score, takes the top N (default 15), dedupes against recent alerts (5-day cooldown unless score jumps ≥0.10), and sends a Markdown digest to a Telegram chat.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
# fill TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
scanner config-check
```

Create the bot via [@BotFather](https://t.me/BotFather), then send any message to it and find your chat ID at `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Run

```bash
# print the digest to stdout, no Telegram send
scanner scan-once --dry-run

# real run — sends to Telegram
scanner scan-once

# delta scan over the watchlist (forced fresh OHLCV)
scanner watchlist-scan

# long-running daemon: 4 full scans/day + hourly watchlist delta
scanner run
```

For the daemon, prefer running under systemd:

```ini
# /etc/systemd/system/scanner.service
[Unit]
Description=Crypto Momentum Scanner
After=network.target

[Service]
WorkingDirectory=/path/to/Bot
ExecStart=/path/to/Bot/.venv/bin/scanner run
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
```

Or as a quick cron alternative for the simple daily mode:

```cron
30 0 * * *  cd /path/to/Bot && /path/to/Bot/.venv/bin/scanner scan-once >> /var/log/scanner.log 2>&1
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
| `SCANNER_MIN_AGE_DAYS` | 5 | Reject anything younger |
| `SCANNER_MIN_VOL_MCAP_RATIO` | 0.05 | vol24h/mcap floor |
| `SCANNER_MIN_VOL_24H_USD` | 100_000 | Absolute vol24h floor |
| `SCANNER_SCORE_THRESHOLD` | 0.60 | Composite score cut |
| `SCANNER_TOP_N` | 15 | Max tokens per digest |
| `SCANNER_NETWORKS` | solana,eth,base,arbitrum,bsc | DEX chains scanned |
| `SCANNER_REALERT_COOLDOWN_DAYS` | 5 | Days before re-alerting |
| `SCANNER_WATCHLIST_THRESHOLD` | 0.45 | Min score to keep a token on the daemon's hourly watchlist |
| `SCANNER_CHART_TOP_N` | 5 | How many top candidates get a chart PNG attached |
| `SCANNER_REJECT_WASH_TRADE` | true | Drop tokens whose tx pattern looks washed |
| `SCANNER_FULL_SCAN_HOURS` | 0,6,12,18 | UTC hours for daemon full scans |
| `SCANNER_FULL_SCAN_MINUTE` | 30 | Minute-of-hour for full scans |
| `SCANNER_WATCHLIST_SCAN_MINUTES` | 60 | Watchlist delta scan period |

## Layout

```
scanner/
  config.py         pydantic-settings
  sources/
    base.py         Source Protocol + Token model
    geckoterminal.py  primary DEX universe + OHLCV + wash-trade signals
    dexscreener.py    enrichment (mcap, age, liquidity)
    coingecko.py    listed cross-source + BTC OHLCV
  universe.py       merge + dedupe + gem filters
  ohlcv.py          parquet cache layer
  indicators.py     MA, RSI, perf, up_days
  scoring.py        composite score + ranking
  chart.py          mplfinance PNG renderer
  alerts.py         Telegram bot API (sendMessage + sendPhoto) + digest format
  state.py          SQLite dedupe + watchlist
  metrics.py        per-scan stats (counts, errors, durations)
  main.py           Typer CLI (config-check / scan-once / watchlist-scan / run)
tests/              fixture-based unit tests
```

## Phase 3 ideas (not yet built)

- Optional Sonnet 4.6 LLM filter (toggle via `SCANNER_LLM_FILTER_ENABLED=true`)
- Birdeye Solana paid tier for deeper holder/wash data
- CoinGecko Pro tier to scan top 5000 listed
- Backtest harness on archived OHLCV to tune weights
