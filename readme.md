dc# Local IDX Finance AI Platform — Implementation Plan

> Status: planned, not yet implemented. Last updated 2026-08-19.

## Context

This repository is greenfield. We are building a 100% local (no paid APIs) Streamlit platform for the Indonesian market: an IDX stock screener (yfinance + technical indicators), a Reksa Dana screener (Playwright scraping into SQLite), and a local LLM "AI picks" engine via Ollama on `localhost:11434`.

Live environment probes changed several things versus the original spec, and the plan below reflects them:

| Finding | Consequence |
|---|---|
| Python **3.10.12**, numpy **2.2.6**, pandas **2.2.2** installed | Current PyPI `pandas_ta` (0.4.71b0) needs Python ≥3.12, so pip on 3.10 resolves to 0.3.14b0, which dies on numpy 2 (`from numpy import NaN`). → use **`pandas-ta-classic` 0.6.52** (Python ≥3.10, numpy ≥2, same `df.ta.*` API) |
| `sqlite3` is stdlib | It **must not** appear in `requirements.txt` — `pip install sqlite3` fails |
| yfinance verified working for IDX | `BBCA.JK` returned OHLCV plus `trailingPE` 13.46, `priceToBook`, `returnOnEquity`, `marketCap`, currency IDR — Module 2 is low-risk |
| `pasardana.id/robots.txt` disallows `/api/`; its fund-search page renders **no** `<table>` server-side and shows Login/Sign In. `reksadana.ojk.go.id` rejects non-browser requests (WAF) | Module 3 gets a **pluggable adapter + CSV fallback + bundled sample data**, never touches `/api/`, and ships an `--inspect` mode to re-derive selectors when a site changes |
| `ollama` binary not installed, `:11434` not listening | The graceful-degradation path in `ai_engine.py` is the *default* experience until Ollama is installed — it must be genuinely useful, not an afterthought |

Confirmed decisions: `pandas-ta-classic` for indicators · Plotly only for charts · adapter + CSV fallback for funds · code at repo root (no `local_finance_ai/` nesting).

## Target structure

```
ai-stockanalysis/
├── config.py                     # paths, Ollama endpoint, watchlist, indicator params, fund sources
├── app.py                        # Streamlit entry point + option_menu nav
├── requirements.txt
├── README.md
├── .gitignore                    # .venv/, data/*.db, data/raw/, __pycache__/
├── data/
│   ├── raw/                      # scraper HTML dumps (--inspect) + user CSV drops
│   └── sample_mutual_funds.csv   # bundled seed so Tab 2 always renders
├── modules/
│   ├── __init__.py
│   ├── database.py               # SQLite schema, upserts, staleness checks
│   ├── indicators.py             # isolates the pandas-ta-classic import (see rationale)
│   ├── stock_data.py             # yfinance fetch + screener table + filters
│   ├── mutual_funds.py           # Playwright adapters, CSV import, fund screening
│   └── ai_engine.py              # Ollama client, prompts, verdict parsing
├── ui/
│   ├── __init__.py
│   ├── tab_stocks.py
│   ├── tab_mutual_funds.py
│   └── tab_ai_picks.py
└── scripts/
    ├── refresh_prices.py         # CLI: warm the price/fundamentals cache
    └── scrape_funds.py           # CLI: refresh | --inspect | --import-csv
```

`indicators.py` is the one addition to the spec's module list. It exists so exactly one file imports the indicator library; if `pandas-ta-classic` ever breaks, we swap its internals and nothing else changes. `scripts/` exists so data refresh works headlessly (cron) without launching Streamlit.

## Build order

Checkpoint after Phase 1, per the original execution instruction, then proceed straight through.

### Phase 1 — Scaffold, `requirements.txt`, `config.py`

`requirements.txt` (no `sqlite3`; comment saying why):

```
streamlit>=1.39
yfinance>=0.2.65
pandas>=2.2
numpy>=2.0
pandas-ta-classic>=0.6.52   # pandas_ta itself needs Python >=3.12; this fork works on 3.10 + numpy 2
playwright>=1.48
ollama>=0.4
plotly>=5.24
streamlit-option-menu>=0.4.0
lxml>=5.0                    # pandas.read_html backend for the fund scraper
beautifulsoup4>=4.12
```

`config.py` — module-level constants, no side effects beyond `mkdir(parents=True, exist_ok=True)` for `data/` and `data/raw/`:

- Paths: `BASE_DIR`, `DATA_DIR`, `RAW_DIR`, `DB_PATH = DATA_DIR / "finance.db"`, `SAMPLE_FUND_CSV`
- Ollama: `OLLAMA_HOST = "http://localhost:11434"`, `OLLAMA_MODEL = "llama3.2"`, `OLLAMA_TIMEOUT_S = 120`, `OLLAMA_TEMPERATURE = 0.2` — each overridable by env var so no code edit is needed to try `qwen2.5`
- IDX: `IDX_SUFFIX = ".JK"`, `DEFAULT_WATCHLIST` of ~20 liquid names (BBCA, BBRI, BMRI, BBNI, TLKM, ASII, ICBP, INDF, UNVR, KLBF, ACES, AMRT, GOTO, ANTM, MDKA, ADRO, PGAS, EXCL, SMGR, TPIA) stored **without** suffix; `to_yf_symbol()` adds it
- Indicators: `RSI_LENGTH = 14`, `MACD_FAST/SLOW/SIGNAL = 12/26/9`, `SMA_PERIODS = (20, 50, 200)`, `BB_LENGTH/BB_STD = 20/2.0`, `ATR_LENGTH = 14`
- Cache TTLs: `PRICE_CACHE_TTL_HOURS = 12`, `FUNDAMENTALS_TTL_HOURS = 24`, `FUND_CACHE_TTL_HOURS = 24`
- Screener defaults: `DEFAULT_RSI_MAX = 30`, `DEFAULT_PE_MAX = 15`, `DEFAULT_MIN_VOLUME`
- Playwright: `PW_HEADLESS = True`, `PW_USER_AGENT`, `PW_LOCALE = "id-ID"`, `PW_TIMEZONE = "Asia/Jakarta"`, `PW_NAV_TIMEOUT_MS = 45_000`, `PW_REQUEST_DELAY_S = 2.0`
- `FUND_SOURCES`: list of dicts — `{name, url, wait_selector, table_selector, column_map}` — so a site redesign is a config edit, not a code change. Ship with one best-effort public source enabled plus a comment recording that `/api/` is robots-disallowed and must not be requested.

### Phase 2 — `modules/database.py`

`sqlite3` only, WAL mode, `check_same_thread=False` (Streamlit reruns on other threads), a `@contextmanager get_connection()` that commits/rolls back.

Tables created idempotently by `init_db()`:

- `prices(ticker, date, open, high, low, close, adj_close, volume, PRIMARY KEY(ticker, date))`
- `fundamentals(ticker PRIMARY KEY, long_name, sector, trailing_pe, price_to_book, market_cap, dividend_yield, roe, updated_at)`
- `mutual_funds(id PK, fund_name, manager, category, nav, return_1y, aum, nav_date, source, scraped_at, UNIQUE(fund_name, nav_date))`
- `ai_notes(id PK, ticker, model, rating, summary, payload, created_at)`
- `meta(key PRIMARY KEY, value)` — refresh timestamps backing staleness checks

API: `init_db()`, `upsert_prices(ticker, df)` / `load_prices(ticker, start=None)`, `upsert_fundamentals(ticker, dict)` / `load_fundamentals(tickers)`, `upsert_mutual_funds(rows)` / `load_mutual_funds(latest_only=True)`, `save_ai_note()` / `load_ai_notes(ticker)`, `set_meta()` / `get_meta()` / `is_stale(key, ttl_hours)`. All writes use `INSERT ... ON CONFLICT ... DO UPDATE` so re-running is safe.

### Phase 3 — `modules/indicators.py` + `modules/stock_data.py`

`indicators.py`:
- One guarded import: try `pandas_ta_classic as ta`, then `pandas_ta as ta`, else raise a message naming the exact fix.
- `add_all(df) -> DataFrame` computes RSI, MACD, SMA 20/50/200, Bollinger, ATR and **renames to stable snake_case** — `rsi_14`, `macd`, `macd_signal`, `macd_hist`, `sma_20/50/200`, `bb_lower/bb_mid/bb_upper`, `bb_pct`, `atr_14` — so UI and prompts never depend on library-specific column names.
- `macd_state(df) -> "bullish_cross" | "bearish_cross" | "bullish" | "bearish"` from the last two bars.

`stock_data.py`:
- `to_yf_symbol(code)` / `strip_suffix(symbol)`.
- `fetch_ohlcv(ticker, period="2y", interval="1d", use_cache=True)` — serve from SQLite when `not is_stale(...)`; otherwise `yf.Ticker(sym).history(auto_adjust=False)` per ticker (avoids the MultiIndex that `yf.download(group_by=...)` returns — confirmed in probing), normalize to lowercase columns, upsert, return. On any network/parse exception: log, fall back to cached rows, and surface a `stale=True` flag rather than raising.
- `fetch_fundamentals(ticker)` — `Ticker.info` wrapped in try/except with per-key `.get()`; `.info` is the flakiest yfinance surface, so a failure yields `None` fields, never a crash.
- `build_screener_table(tickers)` — one row per ticker: last close, % change 1d/1m/1y, `rsi_14`, MACD state, `above_sma200`, `bb_pct`, PE, PB, ROE, market cap, avg volume. Sequential fetch with request spacing and one bounded retry with backoff on rate-limit errors; per-ticker failures are collected and reported, not fatal.
- `screen(table, criteria)` — pure pandas boolean masks over the already-built table (`rsi_max`, `rsi_min`, `above_sma200`, `pe_max`, `min_volume`, `sectors`). No network, so UI filtering is instant.

### Phase 4 — `modules/mutual_funds.py` + `scripts/scrape_funds.py`

- `@dataclass FundRow(fund_name, manager, category, nav, return_1y, aum, nav_date, source)`.
- `parse_id_number(text)` — Indonesian formatting: `1.234,56`, `Rp`, `%`, and `Miliar`/`Triliun` multipliers → float.
- `TableAdapter`, driven entirely by a `FUND_SOURCES` entry: navigate → `wait_for_selector` → `page.content()` → `pandas.read_html(table_selector)` → apply `column_map` → `parse_id_number` → `FundRow`s. Adding a source is a config entry.
- `scrape(sources=None, headless=None, inspect=False)` — sync Playwright API, one Chromium context with configured UA/locale/timezone, `PW_REQUEST_DELAY_S` between navigations, per-source try/except so one dead site doesn't kill the run. `inspect=True` writes `data/raw/<source>-<YYYYMMDD>.html` so selectors can be re-derived when a layout changes. Never requests a robots-disallowed path.
- `import_csv(path)` — same `FundRow` schema; the guaranteed-working path given the login gates.
- `load_or_seed()` — what the UI calls: return cached DB rows; if empty, import `data/sample_mutual_funds.csv` tagged `source="sample"` so the UI can show an honest "sample data — run the scraper or import a CSV" banner. **No fabricated data is ever presented as live**: the `source` and `scraped_at` columns are shown in the table.
- `screen_funds(df, categories, min_return_1y, min_aum)` — pure pandas.
- `scripts/scrape_funds.py`: `--refresh` (default) · `--inspect` · `--import-csv PATH` · `--source NAME`.

### Phase 5 — `modules/ai_engine.py`

- `check_ollama() -> (bool, str)`: `ollama.Client(host=OLLAMA_HOST).list()` inside try/except for connection/httpx/timeout errors. Failure message is actionable and specific: install from ollama.com, `ollama serve`, `ollama pull llama3.2`. `list_models()` powers a model dropdown so the user isn't locked to `llama3.2`.
- `SYSTEM_PROMPT`: the spec's disciplined-IDX-quant instruction, plus "all prices are IDR", "state uncertainty when data is missing", and an explicit not-investment-advice line.
- `build_prompt(snapshot)`: ticker, name, sector, last close, 5d/20d/1y change, `rsi_14`, MACD state, price-vs-SMA20/50/200, `bb_pct`, ATR, PE/PB/ROE/market cap — with missing fields rendered as `n/a` rather than dropped, so the model can't silently assume.
- `analyze_ticker(ticker, snapshot, model=None, stream=False) -> AIVerdict(rating, summary, entry, exit, risks, raw, model)`. Ask for a JSON object and parse it; on parse failure keep the narrative text and set `rating=None` — a malformed response degrades to "narrative only", never an exception. Persist to `ai_notes`.
- `stream_analysis(...)` generator for `st.write_stream`, since local LLMs are slow enough that streaming matters.

### Phase 6 — `app.py` + `ui/`

- `app.py`: `st.set_page_config(page_title=..., layout="wide")`, `init_db()` once via `@st.cache_resource`, horizontal `option_menu` for the three tabs. Sidebar: Ollama status badge (green/red from `check_ollama()`), last price-refresh and last fund-scrape timestamps from `meta`, model selector, and refresh buttons.
- `tab_stocks.py`: filter widgets (RSI range, `Price > SMA200`, max PE, min volume, sector multiselect) → `screen()` over a `@st.cache_data(ttl=...)`-wrapped `build_screener_table()`. Result in `st.dataframe(selection_mode="single-row")`; selecting a row renders a Plotly figure — candlestick + SMA overlays + Bollinger band, volume subplot, RSI subplot with 30/70 guides, MACD subplot with histogram. Failed tickers listed in an expander.
- `tab_mutual_funds.py`: category multiselect, 1Y-return slider, min-AUM input, sortable table, "Refresh (Playwright)" button with a spinner and a clear warning that scraping takes ~30s, plus the sample/stale-data banner.
- `tab_ai_picks.py`: watchlist ticker dropdown → snapshot built from `stock_data` + `indicators` → "Generate thesis" streams the response into a card with a colour-coded Bullish/Neutral/Bearish badge, entry/exit levels, risk bullets, the exact prompt in an expander, and previous verdicts for that ticker from `ai_notes`. When Ollama is down the button is disabled and the install steps are shown inline.

### Phase 7 — `README.md`

Setup: create venv → `pip install -r requirements.txt` → `python -m playwright install chromium` → install Ollama and `ollama pull llama3.2` → `streamlit run app.py`. Plus: how to add a fund source to `FUND_SOURCES` using `--inspect`, how to import a CSV instead of scraping, the numpy-2/`pandas-ta-classic` rationale, and a not-investment-advice disclaimer.

## Verification

Run in order; each step is independently checkable.

1. **Deps** — `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`, then `python -c "import pandas_ta_classic, streamlit, yfinance, plotly, ollama, playwright; print('ok')"`. This is the one genuinely unverified assumption (no installs were run during planning). If `pandas-ta-classic` fails on Python 3.10, replace only `indicators.py`'s internals with pandas-native RSI/MACD/SMA/Bollinger/ATR — the public API (`add_all`, `macd_state`) and every other module stay unchanged.
2. **Browser** — `python -m playwright install chromium`.
3. **DB** — `python -c "from modules.database import init_db; init_db()"`, then `sqlite3 data/finance.db ".tables"` shows all five tables.
4. **Prices** — `python scripts/refresh_prices.py --tickers BBCA,TLKM`, then `sqlite3 data/finance.db "select ticker, count(*), max(date) from prices group by ticker"` returns non-zero counts with a recent max date.
5. **Indicators** — one-liner asserting `add_all()` output contains `rsi_14`/`macd`/`sma_200`/`bb_upper`, that `rsi_14` is within [0, 100], and that only the expected leading NaNs exist.
6. **Screener logic** — `screen()` with `rsi_max=100, pe_max=None` returns every row; with `rsi_max=0` returns none (boundary check on the filters).
7. **Funds** — `python scripts/scrape_funds.py --inspect`, then confirm `data/raw/*.html` exists and check whether the configured selector matched. Expect selector tuning here given the login gates. Guaranteed path: `python scripts/scrape_funds.py --import-csv data/sample_mutual_funds.csv`, then confirm rows in `mutual_funds`.
8. **Ollama down (current state)** — with nothing on `:11434`, `python -c "from modules.ai_engine import check_ollama; print(check_ollama())"` returns `(False, <install instructions>)` and the app still runs with Tabs 1–2 fully functional.
9. **Ollama up** — after `ollama pull llama3.2`, `analyze_ticker("BBCA.JK", ...)` returns a verdict with a rating and a row lands in `ai_notes`.
10. **End to end** — `streamlit run app.py`, then walk all three tabs: filter and chart a stock, filter funds, generate an AI thesis.

## Risks

- **`pandas-ta-classic` on Python 3.10** — the only install-time unknown; documented fallback in step 1 is contained to one file.
- **Fund scraping** — the login gates and WAF mean the scrape may yield nothing without a logged-in session. Mitigated by the adapter/`--inspect`/CSV design and an honest sample-data banner; expect to iterate on selectors, and the CSV path is the reliable one.
- **yfinance `.info`** — periodically breaks upstream and rate-limits. Every field access is guarded; a fundamentals outage degrades the screener to technical-only columns instead of failing.
- **Local LLM latency** — a 3B model on CPU can take 30–60s per thesis; streaming plus cached `ai_notes` keeps the UI responsive.
