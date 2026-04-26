"""Render compact OHLCV chart PNGs for Telegram alerts.

mplfinance is used because it understands OHLC dataframes natively and
ships sane candlestick + volume defaults. Output goes to bytes (PNG) so the
Telegram sendPhoto multipart upload can stream it without touching disk.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

logger = logging.getLogger(__name__)

# Disable mplfinance's mpl interactivity hooks at import.
os.environ.setdefault("MPLBACKEND", "Agg")


def render_ohlcv_png(
    df: pd.DataFrame,
    title: str,
    width_in: float = 7.0,
    height_in: float = 4.5,
) -> bytes | None:
    """Return a PNG image of the OHLCV series, or None on failure.

    The DataFrame must be indexed by UTC timestamps and contain at least
    open/high/low/close/volume columns (mplfinance contract).
    """
    if df is None or df.empty:
        return None
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(df.columns):
        return None
    plot_df = df[list(needed)].copy()
    plot_df.index = pd.DatetimeIndex(plot_df.index)
    if plot_df.index.tz is not None:
        plot_df.index = plot_df.index.tz_convert(None)

    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        rc={"font.size": 9},
    )

    buf = io.BytesIO()
    try:
        mpf.plot(
            plot_df,
            type="candle",
            volume=True,
            mav=(10, 30) if len(plot_df) >= 30 else (10,),
            style=style,
            title=title,
            ylabel="",
            ylabel_lower="vol",
            figsize=(width_in, height_in),
            tight_layout=True,
            savefig=dict(fname=buf, dpi=110, format="png", bbox_inches="tight"),
            warn_too_much_data=10000,
        )
    except Exception as e:
        logger.warning("chart render failed (%s): %s", title, e)
        plt.close("all")
        return None
    plt.close("all")
    out = buf.getvalue()
    return out if out else None
