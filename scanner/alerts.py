"""Telegram alerting and digest formatting.

Direct Bot API call via httpx. Markdown body, ranked top-N gem digest.
"""

from __future__ import annotations

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
    # Lightweight: keep things plain to avoid Markdown parsing issues.
    return text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


def format_card(c: ScoredCandidate) -> str:
    """Compact per-candidate summary for a chart caption (≤1000 chars)."""
    t = c.token
    chain = t.chain.upper() if t.chain else "CG"
    venue = (
        f"{chain} DEX"
        if t.chain
        else (f"CG: {t.coingecko_id}" if t.coingecko_id else "Listed")
    )
    m = c.metrics
    rsi_v = m.get("rsi_14")
    rs_btc = (
        (m.get("perf_7d") or 0.0) - (m.get("btc_perf_7d") or 0.0)
        if m.get("perf_7d") is not None and m.get("btc_perf_7d") is not None
        else None
    )
    ud = m.get("up_days_7")
    accel = c.factors.get("acceleration", 0.0)
    flag_bits = []
    if t.suspected_honeypot:
        flag_bits.append("⚠ honeypot")
    if t.extra.get("wash_trade_warning"):
        flag_bits.append("⚠ wash?")
    flags = (" " + " ".join(flag_bits)) if flag_bits else ""
    lines = [
        f"💎 {t.symbol} ({venue}, age {_fmt_age(t.age_days)}) — score {c.score:.2f}{flags}",
        f"mcap {_fmt_money(t.mcap_usd)}  vol24h {_fmt_money(t.vol_24h_usd)}  vol/mcap {_fmt_pct(t.vol_mcap_ratio)}",
        f"3j {_fmt_pct(m.get('perf_3d'))}  7j {_fmt_pct(m.get('perf_7d'))}  14j {_fmt_pct(m.get('perf_14d'))}  accel {accel:.2f}",
    ]
    bits = []
    if rsi_v is not None:
        bits.append(f"RSI {rsi_v:.0f}")
    if rs_btc is not None:
        bits.append(f"vs BTC {_fmt_pct(rs_btc)}")
    if ud is not None:
        bits.append(f"up {ud}/7")
    if bits:
        lines.append("  ".join(bits))
    if t.chart_url:
        lines.append(t.chart_url)
    return "\n".join(lines)


def format_digest(
    candidates: list[ScoredCandidate],
    universe_size: int,
    candidates_total: int,
    top_n: int,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = (
        f"💎 Top {min(top_n, len(candidates))} gems momentum — {now}\n"
        f"Univers gem: {universe_size} tokens | candidats: {candidates_total} | "
        f"envoyés: {min(top_n, len(candidates))}"
    )
    if not candidates:
        return header + "\n\n_Aucun candidat ne passe le seuil aujourd'hui._"

    lines = [header, ""]
    for i, c in enumerate(candidates[:top_n], start=1):
        t = c.token
        chain = t.chain.upper() if t.chain else "CG"
        venue = (
            f"{chain} DEX"
            if t.chain
            else (f"CG: {t.coingecko_id}" if t.coingecko_id else "Listed")
        )
        symbol = _md_escape(t.symbol or "?")
        line1 = f"#{i}  {symbol}  ({venue}, age {_fmt_age(t.age_days)})  score {c.score:.2f}"
        line2 = (
            f"    mcap {_fmt_money(t.mcap_usd)}  vol24h {_fmt_money(t.vol_24h_usd)}"
            f"  vol/mcap {_fmt_pct(t.vol_mcap_ratio)}"
        )
        m = c.metrics
        accel = c.factors.get("acceleration", 0.0)
        line3 = (
            f"    3j {_fmt_pct(m.get('perf_3d'))}  7j {_fmt_pct(m.get('perf_7d'))}"
            f"  accel {accel:.2f}"
        )
        rsi_v = m.get("rsi_14")
        rs_btc = (
            (m.get("perf_7d") or 0.0) - (m.get("btc_perf_7d") or 0.0)
            if m.get("perf_7d") is not None and m.get("btc_perf_7d") is not None
            else None
        )
        ud = m.get("up_days_7")
        bits = []
        if rsi_v is not None:
            bits.append(f"RSI {rsi_v:.0f}")
        if rs_btc is not None:
            bits.append(f"vs BTC {_fmt_pct(rs_btc)}")
        if ud is not None:
            bits.append(f"up {ud}/7")
        line4 = "    " + "  ".join(bits) if bits else ""
        line5 = f"    {t.chart_url}" if t.chart_url else ""
        block = "\n".join(filter(None, [line1, line2, line3, line4, line5]))
        lines.append(block)
        lines.append("")
    return "\n".join(lines).rstrip()


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
    # Telegram caption hard limit is 1024 chars.
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
