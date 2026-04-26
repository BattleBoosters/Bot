from __future__ import annotations

from scanner.chart import render_ohlcv_png


def test_render_ohlcv_png(clean_uptrend):
    png = render_ohlcv_png(clean_uptrend, title="GEM — score 0.84")
    assert png is not None
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1000


def test_render_ohlcv_png_empty():
    import pandas as pd
    assert render_ohlcv_png(pd.DataFrame(), title="X") is None


def test_render_ohlcv_png_missing_columns():
    import pandas as pd
    bad = pd.DataFrame({"close": [1, 2, 3]})
    assert render_ohlcv_png(bad, title="X") is None
