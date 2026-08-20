"""Configuration for the local IDX swing-trading platform.

Design rules for this file:

* **Constants only.** The single side effect is creating the data directories, which is
  idempotent and cheap. Nothing here touches the network or the database.
* **Cross-platform.** The same checkout runs unchanged on Ubuntu/WSL and on Windows.
  Every path is a :class:`pathlib.Path` anchored to this file, never to the process
  working directory, so ``streamlit run app.py`` and ``python scripts/refresh_prices.py``
  resolve identically regardless of where they are launched from.
* **Env-overridable.** Every setting reads an ``IDXAI_``-prefixed environment variable
  first, so switching models or tuning risk multiples never requires editing code. Put
  machine-specific overrides in your shell profile, not in this file, to keep the two
  laptops on one shared commit.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Final

# ======================================================================================
# Environment helpers
# ======================================================================================

_PREFIX: Final[str] = "IDXAI_"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "y", "on"})
_FALSEY: Final[frozenset[str]] = frozenset({"0", "false", "no", "n", "off"})


def _raw(name: str) -> str | None:
    """Return the stripped value of ``IDXAI_<name>``, or None when unset/blank."""
    value = os.environ.get(f"{_PREFIX}{name}")
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_str(name: str, default: str) -> str:
    """Read a string setting from the environment."""
    return _raw(name) or default


def _env_int(name: str, default: int) -> int:
    """Read an int setting, falling back to ``default`` when unset or unparseable."""
    value = _raw(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float setting, falling back to ``default`` when unset or unparseable."""
    value = _raw(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean setting accepting 1/true/yes/on and 0/false/no/off."""
    value = _raw(name)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSEY:
        return False
    return default


def _env_path(name: str, default: Path) -> Path:
    """Read a filesystem path; relative values resolve against :data:`BASE_DIR`."""
    value = _raw(name)
    if value is None:
        return default
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (BASE_DIR / candidate).resolve()


# ======================================================================================
# Platform detection
# ======================================================================================

IS_WINDOWS: Final[bool] = os.name == "nt"
"""True on native Windows (including Windows-native Python launched from a WSL shell)."""

IS_WSL: Final[bool] = not IS_WINDOWS and "microsoft" in platform.uname().release.lower()
"""True inside a WSL distribution."""

PLATFORM_LABEL: Final[str] = (
    "windows" if IS_WINDOWS else "wsl" if IS_WSL else platform.system().lower()
)

# ======================================================================================
# Paths
# ======================================================================================

BASE_DIR: Final[Path] = Path(__file__).resolve().parent
DATA_DIR: Final[Path] = _env_path("DATA_DIR", BASE_DIR / "data")
RAW_DIR: Final[Path] = _env_path("RAW_DIR", DATA_DIR / "raw")
DATABASE_PATH: Final[Path] = _env_path("DATABASE_PATH", DATA_DIR / "finance.db")
SAMPLE_FUND_CSV: Final[Path] = _env_path(
    "SAMPLE_FUND_CSV", DATA_DIR / "sample_mutual_funds.csv"
)

# The database is per-machine and git-ignored on purpose: a SQLite file copied between
# WSL and Windows invites lock and journal corruption, and prices are cheap to refetch.
for _directory in (DATA_DIR, RAW_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

# Always pass this when opening text files. Windows defaults to cp1252, which mangles
# Indonesian fund names ("Reksa Dana Syariah", em dashes, currency glyphs).
TEXT_ENCODING: Final[str] = "utf-8"


def _default_journal_mode() -> str:
    """Pick a SQLite journal mode that actually works on this filesystem.

    WAL needs shared-memory mmap, which DrvFs (``/mnt/c/...`` inside WSL) does not
    support -- SQLite then fails with "disk I/O error" or silently degrades. Detect a
    Windows drive mounted into WSL and fall back to the slower but portable DELETE mode.
    """
    if IS_WSL and str(DATABASE_PATH).startswith("/mnt/"):
        return "DELETE"
    return "WAL"


SQLITE_JOURNAL_MODE: Final[str] = _env_str("SQLITE_JOURNAL_MODE", _default_journal_mode())
SQLITE_TIMEOUT_S: Final[float] = _env_float("SQLITE_TIMEOUT_S", 30.0)

# ======================================================================================
# Local AI engine (Ollama)
# ======================================================================================

# Both machines normally run Ollama locally, so localhost is correct by default.
#
# If you would rather run a single Ollama on the Windows host and reach it from WSL,
# export the host's address in your WSL shell profile -- WSL2's default NAT networking
# means "localhost" inside the distro is NOT the Windows host:
#
#     export IDXAI_OLLAMA_HOST="http://$(ip route show default | awk '{print $3}'):11434"
#
# and on the Windows side set OLLAMA_HOST=0.0.0.0 so the server accepts non-local
# connections. With WSL2 mirrored networking (Windows 11 22H2+) plain localhost works.
OLLAMA_HOST: Final[str] = _env_str("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL: Final[str] = _env_str("DEFAULT_MODEL", "llama3.2")

# Temperature is deliberately near-zero. The model narrates numbers that Python has
# already computed; creative reinterpretation of a stop-loss is the failure mode.
OLLAMA_TEMPERATURE: Final[float] = _env_float("OLLAMA_TEMPERATURE", 0.1)
OLLAMA_TIMEOUT_S: Final[float] = _env_float("OLLAMA_TIMEOUT_S", 180.0)

# ======================================================================================
# IDX universe
# ======================================================================================

IDX_SUFFIX: Final[str] = ".JK"

_BUILTIN_WATCHLIST: Final[tuple[str, ...]] = (
    "BBCA",
    "BBRI",
    "BMRI",
    "TLKM",
    "ASII",
    "GOTO",
    "ACES",
    "BRMS",
    "AMMN",
    "UNVR",
)


def _resolve_watchlist() -> tuple[str, ...]:
    """Parse IDXAI_WATCHLIST ("BBCA,TLKM" or "BBCA;TLKM") or use the built-in list."""
    override = _raw("WATCHLIST")
    if not override:
        return _BUILTIN_WATCHLIST
    codes = tuple(
        code.strip().upper()
        for code in override.replace(";", ",").split(",")
        if code.strip()
    )
    return codes or _BUILTIN_WATCHLIST


# Stored WITHOUT the exchange suffix; stock_data.to_yf_symbol() appends it.
DEFAULT_WATCHLIST: Final[tuple[str, ...]] = _resolve_watchlist()

# ======================================================================================
# Indicator parameters
# ======================================================================================

EMA_PERIODS: Final[tuple[int, ...]] = (20, 50, 200)
RSI_LENGTH: Final[int] = _env_int("RSI_LENGTH", 14)
MACD_FAST: Final[int] = _env_int("MACD_FAST", 12)
MACD_SLOW: Final[int] = _env_int("MACD_SLOW", 26)
MACD_SIGNAL: Final[int] = _env_int("MACD_SIGNAL", 9)
SUPERTREND_LENGTH: Final[int] = _env_int("SUPERTREND_LENGTH", 7)
SUPERTREND_MULT: Final[float] = _env_float("SUPERTREND_MULT", 3.0)
ATR_LENGTH: Final[int] = _env_int("ATR_LENGTH", 14)

# True selects LazyBear's Squeeze Momentum (the TradingView script); False selects
# Carter's TTM Squeeze. This changes the emitted column name -- see stock_data._COLUMN_MAP.
SQUEEZE_LAZYBEAR: Final[bool] = _env_bool("SQUEEZE_LAZYBEAR", True)

OBV_SLOPE_WINDOW: Final[int] = _env_int("OBV_SLOPE_WINDOW", 5)

# 2 years of daily bars keeps EMA-200 valid with room to spare (~500 trading days).
HISTORY_PERIOD: Final[str] = _env_str("HISTORY_PERIOD", "2y")
HISTORY_INTERVAL: Final[str] = _env_str("HISTORY_INTERVAL", "1d")

# ======================================================================================
# Swing-trade risk rules
# ======================================================================================

ATR_MULT_SL: Final[float] = _env_float("ATR_MULT_SL", 1.5)
ATR_MULT_TP: Final[float] = _env_float("ATR_MULT_TP", 3.0)
RSI_OVERBOUGHT: Final[float] = _env_float("RSI_OVERBOUGHT", 70.0)
TREND_EMA: Final[int] = _env_int("TREND_EMA", 50)

# ======================================================================================
# IDX tick sizes (fraksi harga)
# ======================================================================================

# (upper_bound_exclusive, tick). None marks the open-ended top band. IDX revises these
# periodically, which is exactly why they live in config rather than in the maths.
IDX_TICK_BANDS: Final[tuple[tuple[int | None, int], ...]] = (
    (200, 1),
    (500, 2),
    (2000, 5),
    (5000, 10),
    (None, 25),
)

# ======================================================================================
# Cache freshness
# ======================================================================================

PRICE_CACHE_TTL_HOURS: Final[float] = _env_float("PRICE_CACHE_TTL_HOURS", 12.0)
FUNDAMENTALS_TTL_HOURS: Final[float] = _env_float("FUNDAMENTALS_TTL_HOURS", 24.0)
FUND_CACHE_TTL_HOURS: Final[float] = _env_float("FUND_CACHE_TTL_HOURS", 24.0)

# Spacing and retries for yfinance, which rate-limits aggressively on bursts.
FETCH_DELAY_S: Final[float] = _env_float("FETCH_DELAY_S", 0.6)
FETCH_MAX_RETRIES: Final[int] = _env_int("FETCH_MAX_RETRIES", 2)
FETCH_BACKOFF_S: Final[float] = _env_float("FETCH_BACKOFF_S", 2.0)

# ======================================================================================
# Playwright (Reksa Dana scraper)
# ======================================================================================

# Browsers are installed per-machine into an OS-specific cache, so run
# `python -m playwright install chromium` once on WSL and once on Windows.
PW_HEADLESS: Final[bool] = _env_bool("PW_HEADLESS", True)
PW_USER_AGENT: Final[str] = _env_str(
    "PW_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
PW_LOCALE: Final[str] = _env_str("PW_LOCALE", "id-ID")
PW_TIMEZONE: Final[str] = _env_str("PW_TIMEZONE", "Asia/Jakarta")
PW_NAV_TIMEOUT_MS: Final[int] = _env_int("PW_NAV_TIMEOUT_MS", 45_000)
PW_REQUEST_DELAY_S: Final[float] = _env_float("PW_REQUEST_DELAY_S", 2.0)

# ======================================================================================
# Mutual fund sources
# ======================================================================================

# Each entry fully describes one scrape target, so adapting to a site redesign is a
# config edit rather than a code change. Selectors are re-derived from the HTML that
# `python scripts/scrape_funds.py --inspect` dumps into data/raw/.
#
# Two constraints found while probing, both load-bearing:
#   * pasardana.id/robots.txt disallows /api/ -- never request those endpoints.
#   * The fund-search page renders no server-side <table> and shows a login wall, so a
#     scrape may legitimately return zero rows. mutual_funds.load_or_seed() falls back to
#     the bundled sample CSV, clearly labelled, and CSV import is the reliable path.
FUND_SOURCES: Final[tuple[dict[str, object], ...]] = (
    {
        "name": "pasardana",
        "url": "https://pasardana.id/mutual-fund/search",
        "wait_selector": "table",
        "table_selector": "table",
        "column_map": {
            "Nama Reksa Dana": "fund_name",
            "Manajer Investasi": "manager",
            "Jenis": "category",
            "NAB/Unit": "nav",
            "Return 1 Thn": "return_1y",
            "AUM": "aum",
        },
    },
)

# ======================================================================================
# Introspection
# ======================================================================================


def describe() -> str:
    """Return a human-readable dump of the resolved configuration.

    Handy when the two machines disagree: run ``python config.py`` on each and diff.
    """
    lines = [
        "IDX Swing-Trading AI -- resolved configuration",
        f"  platform            : {PLATFORM_LABEL} (windows={IS_WINDOWS}, wsl={IS_WSL})",
        f"  python              : {platform.python_version()}",
        f"  base dir            : {BASE_DIR}",
        f"  database            : {DATABASE_PATH}",
        f"  journal mode        : {SQLITE_JOURNAL_MODE}",
        f"  raw dir             : {RAW_DIR}",
        f"  ollama host         : {OLLAMA_HOST}",
        f"  model / temperature : {DEFAULT_MODEL} / {OLLAMA_TEMPERATURE}",
        f"  watchlist           : {len(DEFAULT_WATCHLIST)} -- {', '.join(DEFAULT_WATCHLIST)}",
        f"  ema / rsi / atr     : {EMA_PERIODS} / {RSI_LENGTH} / {ATR_LENGTH}",
        f"  supertrend          : ({SUPERTREND_LENGTH}, {SUPERTREND_MULT})",
        f"  squeeze             : {'lazybear' if SQUEEZE_LAZYBEAR else 'ttm (carter)'}",
        f"  sl / tp multiples   : {ATR_MULT_SL}x ATR / {ATR_MULT_TP}x ATR",
        f"  rsi overbought      : {RSI_OVERBOUGHT}",
        f"  trend filter        : close > EMA{TREND_EMA}",
        f"  tick bands          : {IDX_TICK_BANDS}",
        f"  fund sources        : {[s['name'] for s in FUND_SOURCES]}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
