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
```

To run daily, add a cron entry (after the daily UTC close stabilises):

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

## Layout

```
scanner/
  config.py         pydantic-settings
  sources/
    base.py         Source Protocol + Token model
    geckoterminal.py  primary DEX universe + OHLCV
    dexscreener.py    enrichment (mcap, age, liquidity)
    coingecko.py    listed cross-source + BTC OHLCV
  universe.py       merge + dedupe + gem filters
  ohlcv.py          parquet cache layer
  indicators.py     MA, RSI, perf, up_days
  scoring.py        composite score + ranking
  alerts.py         Telegram bot API + digest format
  state.py          SQLite dedupe
  main.py           Typer CLI
tests/              fixture-based unit tests
```

## Phase 2 ideas

- Long-running asyncio daemon with 4×/day scans + watchlist hourly delta
- Chart PNG attached via mplfinance
- Anti wash-trade ratio (unique traders / total trades)
- Optional Sonnet 4.6 LLM filter (toggle via `SCANNER_LLM_FILTER_ENABLED=true`)
