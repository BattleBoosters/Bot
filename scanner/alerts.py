"""Telegram alerting + digest formatting.

Direct Bot API via httpx. The pivot to long-term uptrend signals means we
push EVERY candidate that clears the score threshold (sometimes hundreds)
rather than a hard top-N. To stay within Telegram's message limits we
send:

  1. A header message with the run summary + the top-N highlights formatted
     in detail (default top 10).
  2. Up to N chart PNGs (default top 5) attached individually.
  3. A CSV document attachment containing every qualified candidate so the
     operator can sort/filter in a spreadsheet.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

import httpx

from scanner.scoring import ScoredCandidate

logger = logging.getLogger(__name__)


def _fmt_pct(x: float | None) -> str:
    if x is None:
        return "—"
    sign = "+" if x >= 0 else ""
    return f"{sign}{x * 100:.1f}%"


def _fmt_money(x: float | None) -> str:
    if x is None or x <= 0:
        return "—"
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}k"
    return f"${x:.0f}"


def _fmt_age(days: float | None) -> str:
    if days is None:
        return "?"
    if days < 30:
        return f"{int(days)}j"
    if days < 365:
        return f"{int(days / 30)}mo"
    return f"{days / 365:.1f}y"


def _md_escape(text: str) -> str:
    return (
        text.replace("_", "\\_")
        .replace("*", "\\*")
        .replace("`", "\\`")
        .replace("[", "\\[")
    )


def _venue(c: ScoredCandidate) -> str:
    t = c.token
    if t.chain:
        return f"{t.chain.upper()} DEX"
    if t.coingecko_id:
        return f"CG: {t.coingecko_id}"
    return "Listed"


def _flag_bits(c: ScoredCandidate) -> list[str]:
    bits: list[str] = []
    if c.token.suspected_honeypot:
        bits.append("⚠ honeypot")
    if c.token.extra.get("wash_trade_warning"):
        bits.append("⚠ wash?")
    if c.token.extra.get("one_sided_warning"):
        bits.append("⚠ skew")
    return bits


def format_card(c: ScoredCandidate) -> str:
    """Compact per-candidate summary used as a chart caption (≤1000 chars)."""
    t = c.token
    m = c.metrics
    flags = _flag_bits(c)
    flags_str = (" " + " ".join(flags)) if flags else ""
    annual = m.get("annualised_growth")
    dd = m.get("drawdown_from_ath")
    weeks_up = m.get("weeks_up_12")
    rsi_v = m.get("rsi_14")
    hist = m.get("history_days")
    lines = [
        f"💎 {t.symbol} ({_venue(c)}, age {_fmt_age(t.age_days)}) — score {c.score:.2f}{flags_str}",
        f"mcap {_fmt_money(t.mcap_usd)}  vol24h {_fmt_money(t.vol_24h_usd)}  "
        f"vol/mcap {_fmt_pct(t.vol_mcap_ratio)}",
        f"30j {_fmt_pct(m.get('perf_30d'))}  60j {_fmt_pct(m.get('perf_60d'))}  "
        f"90j {_fmt_pct(m.get('perf_90d'))}",
    ]
    bits = []
    if annual is not None:
        bits.append(f"slope {_fmt_pct(annual)}/yr")
    if dd is not None:
        bits.append(f"ATH {_fmt_pct(dd)}")
    if weeks_up is not None:
        bits.append(f"weeks↑ {int(weeks_up * 12)}/12")
    if rsi_v is not None:
        bits.append(f"RSI {rsi_v:.0f}")
    if hist is not None:
        bits.append(f"hist {hist}d")
    if bits:
        lines.append("  ".join(bits))
    if t.chart_url:
        lines.append(t.chart_url)
    return "\n".join(lines)


def format_digest(
    candidates: list[ScoredCandidate],
    universe_size: int,
    candidates_total: int,
    highlight_top_n: int = 10,
    mcap_window_str: str = "",
) -> str:
    """Header message: summary line + detailed top-N highlights.

    Full list goes out as a CSV attachment, see `build_csv`. The mcap
    window string is rendered into the summary line so the digest reflects
    the actual run config rather than a hard-coded range.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    window_label = f" {mcap_window_str}" if mcap_window_str else ""
    header = (
        f"💎 Gems uptrend scan — {now}\n"
        f"Univers gem{window_label} : {universe_size} | candidats : {candidates_total} | "
        f"highlights : {min(highlight_top_n, len(candidates))}\n"
        f"📎 CSV joint = liste complète triée par score."
    )
    if not candidates:
        return header + "\n\n_Aucun candidat ne passe le seuil aujourd'hui._"

    lines = [header, ""]
    for i, c in enumerate(candidates[:highlight_top_n], start=1):
        t = c.token
        m = c.metrics
        flags = _flag_bits(c)
        flags_str = ("  " + " ".join(flags)) if flags else ""
        symbol = _md_escape(t.symbol or "?")
        lines.append(
            f"#{i}  {symbol}  ({_venue(c)}, age {_fmt_age(t.age_days)})  "
            f"score {c.score:.2f}{flags_str}"
        )
        lines.append(
            f"    mcap {_fmt_money(t.mcap_usd)}  vol24h {_fmt_money(t.vol_24h_usd)}"
            f"  vol/mcap {_fmt_pct(t.vol_mcap_ratio)}"
        )
        lines.append(
            f"    30j {_fmt_pct(m.get('perf_30d'))}  60j {_fmt_pct(m.get('perf_60d'))}"
            f"  90j {_fmt_pct(m.get('perf_90d'))}"
        )
        annual = m.get("annualised_growth")
        dd = m.get("drawdown_from_ath")
        weeks_up = m.get("weeks_up_12")
        rsi_v = m.get("rsi_14")
        hist = m.get("history_days")
        bits = []
        if annual is not None:
            bits.append(f"slope {_fmt_pct(annual)}/yr")
        if dd is not None:
            bits.append(f"ATH {_fmt_pct(dd)}")
        if weeks_up is not None:
            bits.append(f"weeks↑ {int(weeks_up * 12)}/12")
        if rsi_v is not None:
            bits.append(f"RSI {rsi_v:.0f}")
        if hist is not None:
            bits.append(f"hist {hist}d")
        if bits:
            lines.append("    " + "  ".join(bits))
        if t.chart_url:
            lines.append(f"    {t.chart_url}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_accumulation_digest(
    candidates: list,  # list[AccumulationCandidate]
    universe_size: int,
    highlight_top_n: int = 10,
) -> str:
    """Header for the on-chain accumulation digest (parallel to trend)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"🕵️ Quietly accumulated — {now}\n"
        f"Univers gem : {universe_size} | candidats accumulation : {len(candidates)} | "
        f"highlights : {min(highlight_top_n, len(candidates))}\n"
        f"📎 CSV joint = liste complète triée par score d'accumulation."
    )
    if not candidates:
        return header + "\n\n_Aucun signal d'accumulation aujourd'hui._"

    lines = [header, ""]
    for i, c in enumerate(candidates[:highlight_top_n], start=1):
        t = c.token
        m = c.metrics
        f = c.factors
        symbol = _md_escape(t.symbol or "?")
        chain_label = (t.chain or "?").upper()
        lines.append(
            f"#{i}  {symbol}  ({chain_label}, age {_fmt_age(t.age_days)})  "
            f"acc {c.score:.2f}"
        )
        lines.append(
            f"    mcap {_fmt_money(t.mcap_usd)}  vol24h {_fmt_money(t.vol_24h_usd)}"
        )
        bits = [
            f"wyckoff {f.get('wyckoff', 0):.2f}",
            f"holders {f.get('holder_growth', 0):.2f}",
            f"distrib {f.get('distribution', 0):.2f}",
            f"buyers {f.get('buy_pressure', 0):.2f}",
        ]
        lines.append("    " + "  ".join(bits))
        sub = []
        hg = m.get("holder_growth_14d")
        if hg is not None:
            sub.append(f"Δholders {_fmt_pct(hg)}")
        tg = m.get("tvl_growth_14d")
        if tg is not None:
            sub.append(f"ΔTVL {_fmt_pct(tg)}")
        top20 = m.get("top20_share")
        if top20 is not None:
            sub.append(f"top20 {top20 * 100:.0f}%")
        td = m.get("top20_delta")
        if td is not None:
            sub.append(f"Δtop20 {td * 100:+.1f}pp")
        hist = m.get("snapshot_history_days")
        if hist is not None:
            sub.append(f"hist {hist}j")
        if sub:
            lines.append("    " + "  ".join(sub))
        if t.chart_url:
            lines.append(f"    {t.chart_url}")
        lines.append("")
    return "\n".join(lines).rstrip()


ACC_CSV_COLUMNS = [
    "rank", "symbol", "name", "chain", "address", "score",
    "mcap_usd", "vol_24h_usd", "age_days",
    "factor_wyckoff", "factor_holder_growth", "factor_distribution",
    "factor_tvl_growth", "factor_buy_pressure",
    "holder_growth_14d", "tvl_growth_14d", "top20_share", "top20_delta",
    "snapshot_history_days", "chart_url",
]


def build_accumulation_csv(candidates: list) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=ACC_CSV_COLUMNS)
    w.writeheader()
    for i, c in enumerate(candidates, start=1):
        t = c.token
        m = c.metrics
        f = c.factors
        w.writerow({
            "rank": i,
            "symbol": t.symbol,
            "name": t.name,
            "chain": t.chain or "",
            "address": t.address or "",
            "score": round(c.score, 4),
            "mcap_usd": t.mcap_usd or "",
            "vol_24h_usd": t.vol_24h_usd or "",
            "age_days": int(t.age_days) if t.age_days is not None else "",
            "factor_wyckoff":       round(f.get("wyckoff", 0), 3),
            "factor_holder_growth": round(f.get("holder_growth", 0), 3),
            "factor_distribution":  round(f.get("distribution", 0), 3),
            "factor_tvl_growth":    round(f.get("tvl_growth", 0), 3),
            "factor_buy_pressure":  round(f.get("buy_pressure", 0), 3),
            "holder_growth_14d": _round(m.get("holder_growth_14d")),
            "tvl_growth_14d":    _round(m.get("tvl_growth_14d")),
            "top20_share":       _round(m.get("top20_share")),
            "top20_delta":       _round(m.get("top20_delta")),
            "snapshot_history_days": m.get("snapshot_history_days") or "",
            "chart_url": t.chart_url or "",
        })
    return buf.getvalue().encode("utf-8")


CSV_COLUMNS = [
    "rank", "symbol", "name", "score",
    "venue", "chain", "address", "coingecko_id",
    "mcap_usd", "vol_24h_usd", "vol_mcap_ratio", "age_days",
    "perf_7d", "perf_14d", "perf_30d", "perf_60d", "perf_90d",
    "annualised_growth", "drawdown_from_ath", "weeks_up_12",
    "rsi_14", "btc_perf_30d", "log_slope_per_day", "history_days",
    "factor_slope", "factor_ath_proximity", "factor_perf_consist",
    "factor_volume", "factor_rs_btc", "factor_rsi",
    "wash_warning", "one_sided_warning", "honeypot",
    "chart_url",
]


def build_csv(candidates: list[ScoredCandidate]) -> bytes:
    """Build the full-candidate CSV attached to the Telegram digest."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    w.writeheader()
    for i, c in enumerate(candidates, start=1):
        t = c.token
        m = c.metrics
        f = c.factors
        w.writerow({
            "rank": i,
            "symbol": t.symbol,
            "name": t.name,
            "score": round(c.score, 4),
            "venue": _venue(c),
            "chain": t.chain or "",
            "address": t.address or "",
            "coingecko_id": t.coingecko_id or "",
            "mcap_usd": t.mcap_usd or "",
            "vol_24h_usd": t.vol_24h_usd or "",
            "vol_mcap_ratio": round(t.vol_mcap_ratio, 4) if t.vol_mcap_ratio else "",
            "age_days": int(t.age_days) if t.age_days is not None else "",
            "perf_7d": _round(m.get("perf_7d")),
            "perf_14d": _round(m.get("perf_14d")),
            "perf_30d": _round(m.get("perf_30d")),
            "perf_60d": _round(m.get("perf_60d")),
            "perf_90d": _round(m.get("perf_90d")),
            "annualised_growth": _round(m.get("annualised_growth")),
            "drawdown_from_ath": _round(m.get("drawdown_from_ath")),
            "weeks_up_12": _round(m.get("weeks_up_12")),
            "rsi_14": _round(m.get("rsi_14"), 1),
            "btc_perf_30d": _round(m.get("btc_perf_30d")),
            "log_slope_per_day": _round(m.get("log_slope_per_day"), 6),
            "history_days": m.get("history_days") or "",
            "factor_slope": round(f.get("slope", 0.0), 3),
            "factor_ath_proximity": round(f.get("ath_proximity", 0.0), 3),
            "factor_perf_consist": round(f.get("perf_consist", 0.0), 3),
            "factor_volume": round(f.get("volume", 0.0), 3),
            "factor_rs_btc": round(f.get("rs_btc", 0.0), 3),
            "factor_rsi": round(f.get("rsi", 0.0), 3),
            "wash_warning": int(bool(t.extra.get("wash_trade_warning"))),
            "one_sided_warning": int(bool(t.extra.get("one_sided_warning"))),
            "honeypot": int(bool(t.suspected_honeypot)),
            "chart_url": t.chart_url or "",
        })
    return buf.getvalue().encode("utf-8")


def _round(v, digits: int = 4):
    if v is None:
        return ""
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return ""


async def send_message(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str | None = None,
) -> bool:
    if not bot_token or not chat_id:
        logger.warning("telegram not configured; skipping send")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    chunks = _split_for_telegram(text, limit=4000)
    for chunk in chunks:
        payload: dict = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = await client.post(url, json=payload, timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError as e:
            logger.error("telegram send failed: %s", e)
            return False
    return True


async def send_photo(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: str,
    image: bytes,
    caption: str = "",
    filename: str = "chart.png",
) -> bool:
    if not bot_token or not chat_id or not image:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    if len(caption) > 1000:
        caption = caption[:1000] + "…"
    files = {"photo": (filename, image, "image/png")}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        r = await client.post(url, data=data, files=files, timeout=60.0)
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error("telegram sendPhoto failed: %s", e)
        return False


async def send_document(
    client: httpx.AsyncClient,
    bot_token: str,
    chat_id: str,
    content: bytes,
    filename: str,
    caption: str = "",
    mime: str = "text/csv",
) -> bool:
    """Upload a file (e.g. CSV digest) via Telegram sendDocument."""
    if not bot_token or not chat_id or not content:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    if len(caption) > 1000:
        caption = caption[:1000] + "…"
    files = {"document": (filename, content, mime)}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        r = await client.post(url, data=data, files=files, timeout=60.0)
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        logger.error("telegram sendDocument failed: %s", e)
        return False


async def ping_telegram(
    client: httpx.AsyncClient, bot_token: str, chat_id: str
) -> tuple[bool, str]:
    if not bot_token or not chat_id:
        return False, "missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
    try:
        r = await client.get(
            f"https://api.telegram.org/bot{bot_token}/getMe", timeout=15.0
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return False, f"getMe not ok: {data}"
        return True, data["result"]["username"]
    except httpx.HTTPError as e:
        return False, f"HTTP error: {e}"


def _split_for_telegram(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        parts.append(remaining)
    return parts
