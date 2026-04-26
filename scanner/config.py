from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    coingecko_api_key: str = Field(default="", alias="COINGECKO_API_KEY")
    cmc_api_key: str = Field(default="", alias="CMC_API_KEY")
    birdeye_api_key: str = Field(default="", alias="BIRDEYE_API_KEY")
    helius_api_key: str = Field(default="", alias="HELIUS_API_KEY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    mcap_min_usd: float = Field(default=1_000_000, alias="SCANNER_MCAP_MIN_USD")
    mcap_max_usd: float = Field(default=300_000_000, alias="SCANNER_MCAP_MAX_USD")
    min_age_days: int = Field(default=14, alias="SCANNER_MIN_AGE_DAYS")
    min_vol_mcap_ratio: float = Field(default=0.05, alias="SCANNER_MIN_VOL_MCAP_RATIO")
    min_vol_24h_usd: float = Field(default=100_000, alias="SCANNER_MIN_VOL_24H_USD")

    score_threshold: float = Field(default=0.55, alias="SCANNER_SCORE_THRESHOLD")
    watchlist_threshold: float = Field(default=0.40, alias="SCANNER_WATCHLIST_THRESHOLD")
    chart_top_n: int = Field(default=5, alias="SCANNER_CHART_TOP_N")
    highlight_top_n: int = Field(default=10, alias="SCANNER_HIGHLIGHT_TOP_N")
    realert_cooldown_days: int = Field(default=5, alias="SCANNER_REALERT_COOLDOWN_DAYS")
    reject_wash_trade: bool = Field(default=True, alias="SCANNER_REJECT_WASH_TRADE")
    full_scan_hours_csv: str = Field(default="1", alias="SCANNER_FULL_SCAN_HOURS")
    full_scan_minute: int = Field(default=30, alias="SCANNER_FULL_SCAN_MINUTE")
    watchlist_scan_minutes: int = Field(default=240, alias="SCANNER_WATCHLIST_SCAN_MINUTES")
    ohlcv_days: int = Field(default=365, alias="SCANNER_OHLCV_DAYS")

    networks_csv: str = Field(default="solana,eth,base,arbitrum,bsc", alias="SCANNER_NETWORKS")

    cache_dir: Path = Field(default=Path("./data/cache"), alias="SCANNER_CACHE_DIR")
    db_path: Path = Field(default=Path("./data/scanner.db"), alias="SCANNER_DB_PATH")

    llm_filter_enabled: bool = Field(default=False, alias="SCANNER_LLM_FILTER_ENABLED")

    accumulation_enabled: bool = Field(default=True, alias="SCANNER_ACCUMULATION_ENABLED")
    accumulation_threshold: float = Field(default=0.50, alias="SCANNER_ACCUMULATION_THRESHOLD")
    onchain_concurrency: int = Field(default=4, alias="SCANNER_ONCHAIN_CONCURRENCY")
    onchain_max_tokens: int = Field(default=200, alias="SCANNER_ONCHAIN_MAX_TOKENS")

    request_timeout_seconds: float = Field(default=30.0, alias="SCANNER_HTTP_TIMEOUT")

    @property
    def networks(self) -> list[str]:
        return [n.strip().lower() for n in self.networks_csv.split(",") if n.strip()]

    @property
    def full_scan_hours(self) -> list[int]:
        out: list[int] = []
        for s in self.full_scan_hours_csv.split(","):
            s = s.strip()
            if not s:
                continue
            try:
                h = int(s)
            except ValueError:
                continue
            if 0 <= h <= 23:
                out.append(h)
        return sorted(set(out)) or [0]

    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token) and bool(self.telegram_chat_id)

    @property
    def mcap_window_str(self) -> str:
        return f"({_fmt_mcap(self.mcap_min_usd)}–{_fmt_mcap(self.mcap_max_usd)})"


def _fmt_mcap(x: float) -> str:
    if x >= 1e9:
        v = x / 1e9
        return f"${v:.1f}B" if v % 1 else f"${int(v)}B"
    if x >= 1e6:
        v = x / 1e6
        return f"${v:.1f}M" if v % 1 else f"${int(v)}M"
    return f"${x:.0f}"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
