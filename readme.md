# Local IDX Swing-Trading AI Platform — Implementation Plan

> Status: **Phase 1 complete** (scaffold, `requirements.txt`, `config.py`).
> Phases 2-7 planned. Last updated 2026-08-20.
>
> Runs on two machines: Ubuntu/WSL and Windows. See
> [Cross-platform notes](#cross-platform-notes-wsl--windows).

## Context

This repo is greenfield. The spec was revised from a general screener into a **daily swing-trading
assistant**: the primary deliverable is now an actionable trade card per IDX ticker — ACTION, Entry,
Stop-Loss at 1.5×ATR, Take-Profit at 3.0×ATR (1:2 R/R) — gated by hard quantitative rules, with a
local Ollama model supplying the rationale. The screener and Reksa Dana tabs remain, demoted to
Tabs 2 and 3.

This plan supersedes the earlier screener-first plan. It keeps the spec's exact 4-module / 3-tab
layout (`tab_swing_picks.py` replaces the old `tab_ai_picks.py`) and carries forward two settled
constraints from the previous round.

### Verified findings driving the design

| Finding | Consequence |
|---|---|
| Python **3.10.12**, numpy **2.2.6** | PyPI `pandas_ta` (0.4.71b0) needs Python ≥3.12; pip on 3.10 falls back to 0.3.14b0, which dies on numpy 2 (`from numpy import NaN`). → **`pandas-ta-classic` 0.6.52**, imported as `pandas_ta_classic as ta`, same `df.ta.*` API. **Verified installed and imported on this Python 3.10.12 / numpy 2.2.6 / pandas 2.2.2 box**, so the dependency choice is no longer an assumption |
| `sqlite3` is stdlib | Must **not** go in `requirements.txt` — `pip install sqlite3` fails |
| All 10 watchlist tickers verified live | BBCA 6350, BBRI 3130, BMRI 4150, TLKM 2610, ASII 4750, GOTO 50, ACES 360, BRMS 675, AMMN 4470, UNVR 1795 — 244 rows each, no gaps. `trailingPE`/`priceToBook`/`returnOnEquity` also available |
| **`ta.atr(length=14)` emits `ATRr_14`, not `ATR_14`** | The name embeds `mamode` ("rma"). Hardcoding `ATR_14` silently KeyErrors — the most likely implementation bug here, and it sits directly on the stop-loss path. **Confirmed against live BBCA data**, along with `SUPERTd_7_3.0` (values `{-1.0, 1.0}`) and `SQZ_20_2.0_20_1.5_LB` |
| `ta.supertrend(7, 3.0)` → `SUPERT_7_3.0`, **`SUPERTd_7_3.0`** (1=bull, −1=bear), `SUPERTl/s_7_3.0` | `SUPERTd` is the signal column the BUY gate reads |
| `ta.squeeze(lazybear=True)` → `SQZ_20_2.0_20_1.5_LB` + `SQZ_ON`/`SQZ_OFF`/`SQZ_NO` | `lazybear=True` matches the TradingView "Squeeze Momentum [LazyBear]" the spec asks for; default (Carter TTM) drops the `_LB` suffix |
| `supertrend` is `@njit`-decorated with a no-op fallback if numba is absent | `numba` is optional — correctness unaffected, speed is not. Listed as a commented optional dep |
| `pasardana.id/robots.txt` disallows `/api/`; fund search renders no `<table>` server-side and shows Login. `reksadana.ojk.go.id` WAF-blocks non-browser requests | Mutual funds keep the adapter + CSV-fallback + sample-seed design; never request `/api/` |
| `ollama` not installed, `:11434` not listening | Default state until installed — see the offline-resilience note below |
| **`python3.10-venv` absent on this WSL box** | `python3 -m venv` imports but cannot bootstrap pip (`No module named ensurepip`). Ubuntu/WSL needs `sudo apt install python3.10-venv` first; Windows ships venv working out of the box |

### Confirmed design decisions

1. **Python computes every number; the LLM only narrates.** All arithmetic and gating happen in
   `stock_data.py`. The model receives finished figures and returns only ACTION + RATIONALE; a
   validator overrides any price it restates incorrectly, so a displayed price is always the Python
   one. A 3B model cannot do this arithmetic reliably, and these are numbers someone might trade on.
2. **Entry = today's close, explicitly labelled a reference price**, with ATR-implied gap risk shown
   and a UI helper to recompute SL/TP from an actual fill.
3. **Round SL/TP to valid IDX ticks** (fraksi harga). Without it the engine emits unplaceable prices
   like a Rp6,127 stop on BBCA. Position sizing in lots and a liquidity guard were considered and
   **deliberately excluded** from this scope.
4. **Batch scan + on-demand narration.** Deterministically score all 10 tickers instantly (no LLM),
   show a ranked gate table, narrate only what the user clicks.

A consequence worth stating plainly: because Python owns the math, **Tab 1 is fully functional with
Ollama offline** — Entry/SL/TP/gates all render; only the prose paragraph is missing. Graceful
degradation stops being a consolation path and becomes a genuinely usable mode.

## Target structure

Exactly the spec's layout, at repo root (no `local_finance_ai/` nesting):

```
ai-stockanalysis/
├── config.py                 # Ollama settings, watchlist, indicator + risk params, IDX tick bands
├── app.py                    # Streamlit entry, 3-tab nav, sidebar health panel
├── requirements.txt
├── readme.md                 # this document
├── .gitignore                # .venv/, data/*.db, data/raw/, __pycache__/
├── .gitattributes            # eol=lf everywhere, so WSL and Windows share one commit
├── data/
│   ├── raw/                  # scraper HTML dumps (--inspect) + user CSV drops
│   └── sample_mutual_funds.csv
├── modules/
│   ├── __init__.py
│   ├── stock_data.py         # yfinance + indicator engine + swing math + tick rounding
│   ├── mutual_funds.py       # Playwright adapters, CSV import, fund screening
│   ├── database.py           # SQLite cache + fast screener queries
│   └── ai_engine.py          # Ollama narration only (temperature 0.1)
├── ui/
│   ├── __init__.py
│   ├── tab_swing_picks.py    # Tab 1 — AI Swing Trader
│   ├── tab_stocks.py         # Tab 2 — Stock Screener
│   └── tab_mutual_funds.py   # Tab 3 — Reksa Dana Screener
└── scripts/
    ├── refresh_prices.py     # CLI: warm price/fundamentals cache (cron-able)
    └── scrape_funds.py       # CLI: refresh | --inspect | --import-csv
```

No extra module beyond the spec's four: the indicator import is isolated inside a marked section of
`stock_data.py` behind one `_COLUMN_MAP`, rather than in a separate `indicators.py`. `scripts/`
exists only so data refresh runs headlessly without Streamlit.

## Cross-platform notes (WSL + Windows)

The same commit runs on both machines; only the environment differs. What this costs in the code:

| Concern | Handling |
|---|---|
| **Paths** | Every path is a `pathlib.Path` anchored to `BASE_DIR = Path(__file__).resolve().parent`, never to the process CWD. `streamlit run app.py` and `python scripts/refresh_prices.py` resolve identically from any directory. |
| **Line endings** | `.gitattributes` pins `eol=lf` for all text. Without it, a file saved by a Windows editor arrives as CRLF and the other machine sees the entire file as modified. `.bat`/`.ps1` are exempted to CRLF. |
| **SQLite WAL** | WAL needs shared-memory mmap, which **DrvFs does not support** — a database under `/mnt/c/...` accessed from WSL fails with "disk I/O error". `config._default_journal_mode()` detects WSL + a `/mnt/` database path and falls back to `DELETE`; `IDXAI_SQLITE_JOURNAL_MODE` overrides either way. |
| **The database itself** | Git-ignored and per-machine, deliberately. Copying a SQLite file between the two invites lock and journal corruption, and prices are one refresh away. |
| **Virtualenvs** | Never shared or committed — a venv is full of absolute paths and native binaries. Build one per OS. On Ubuntu/WSL, `sudo apt install python3.10-venv` is required first (this box does not have it); Windows ships it working. Activation differs: `.venv/bin/activate` vs `.venv\Scripts\Activate.ps1`. |
| **Text encoding** | Windows defaults to cp1252, which mangles Indonesian fund names. `config.TEXT_ENCODING` is `"utf-8"` and every file open passes it explicitly. |
| **Playwright browsers** | Installed per-machine into an OS-specific cache, not into the repo. Run `python -m playwright install chromium` once on each. |
| **Ollama across the boundary** | WSL2's default NAT networking means `localhost` inside the distro is *not* the Windows host. To share one Ollama on Windows, set `IDXAI_OLLAMA_HOST` to the host IP (`ip route show default`) and `OLLAMA_HOST=0.0.0.0` on the Windows side. With WSL2 mirrored networking (Win 11 22H2+), plain localhost works. Simplest option remains one Ollama per machine. |
| **Dependencies** | Every package in `requirements.txt` ships wheels for both platforms; `numba` is commented out as optional so no machine needs a compiler. |

## Build order

Checkpoint after Phase 2 (`requirements.txt` + `config.py` + `stock_data.py`), per the spec's
initial execution step.

### Phase 1 — `requirements.txt` + `config.py` &nbsp;✅ done

```
streamlit>=1.39
yfinance>=0.2.65
pandas>=2.2
numpy>=2.0
pandas-ta-classic>=0.6.52   # pandas_ta needs Python >=3.12; this fork works on 3.10 + numpy 2
playwright>=1.48
ollama>=0.4
plotly>=5.24
lxml>=5.0                   # pandas.read_html backend for the fund scraper
beautifulsoup4>=4.12
# numba                     # optional: JIT for SuperTrend; graceful no-op fallback without it
# sqlite3 is Python stdlib — do not add it here, pip install sqlite3 fails
```

`config.py` — constants only, no side effects beyond `mkdir(parents=True, exist_ok=True)` on
`data/` and `data/raw/`. Every value is overridable via an `IDXAI_`-prefixed environment
variable, so machine-specific differences live in each shell profile and both laptops share one
commit. Unparseable values fall back to the default rather than crashing at import.
`python config.py` prints the resolved settings — run it on both machines and diff when they
disagree.

- `OLLAMA_HOST = "http://localhost:11434"`, `DEFAULT_MODEL = "llama3.2"`,
  `OLLAMA_TEMPERATURE = 0.1`, `OLLAMA_TIMEOUT_S = 180`
- `DATABASE_PATH = "data/finance.db"` (as a `BASE_DIR`-relative `Path`), `RAW_DIR`,
  `SAMPLE_FUND_CSV`
- `DEFAULT_WATCHLIST = ["BBCA","BBRI","BMRI","TLKM","ASII","GOTO","ACES","BRMS","AMMN","UNVR"]`
  stored **without** suffix; `IDX_SUFFIX = ".JK"`
- Indicators: `EMA_PERIODS = (20, 50, 200)`, `RSI_LENGTH = 14`, `MACD = (12, 26, 9)`,
  `SUPERTREND_LENGTH = 7`, `SUPERTREND_MULT = 3.0`, `ATR_LENGTH = 14`, `SQUEEZE_LAZYBEAR = True`
- Risk rules: `ATR_MULT_SL = 1.5`, `ATR_MULT_TP = 3.0`, `RSI_OVERBOUGHT = 70`, `TREND_EMA = 50`
- `IDX_TICK_BANDS = [(200,1),(500,2),(2000,5),(5000,10),(None,25)]` — upper-bound-exclusive; in
  config because IDX revises fraksi harga periodically
- Cache TTLs: `PRICE_CACHE_TTL_HOURS = 12`, `FUNDAMENTALS_TTL_HOURS = 24`,
  `FUND_CACHE_TTL_HOURS = 24`
- Playwright: `PW_HEADLESS`, `PW_USER_AGENT`, `PW_LOCALE = "id-ID"`,
  `PW_TIMEZONE = "Asia/Jakarta"`, `PW_NAV_TIMEOUT_MS = 45_000`, `PW_REQUEST_DELAY_S = 2.0`
- `FUND_SOURCES`: `[{name, url, wait_selector, table_selector, column_map}]` so a site redesign is
  a config edit; comment records that `/api/` is robots-disallowed

### Phase 2 — `modules/stock_data.py` (the quantitative core)

**Data**

- `to_yf_symbol(code)` / `strip_suffix(symbol)`.
- `fetch_ohlcv(ticker, period="2y", use_cache=True)` — serve from SQLite unless stale; else
  `yf.Ticker(sym).history(auto_adjust=False)` **per ticker** (avoids the MultiIndex that
  `yf.download(group_by=...)` returns — confirmed in probing), lowercase columns, upsert, return.
  Any network/parse failure logs, falls back to cache, and sets a `stale` flag rather than raising.
  2y of history is needed for a valid `ema_200`.
- `fetch_fundamentals(ticker)` — `Ticker.info` in try/except with per-key `.get()`; `.info` is
  yfinance's flakiest surface, so failure yields `None` fields, never a crash.

**Indicator engine** — one guarded import (`pandas_ta_classic as ta`, falling back to `pandas_ta`,
else a message naming the fix), then `add_indicators(df)` computing EMA 20/50/200, RSI 14,
MACD(12,26,9), SuperTrend(7,3), Squeeze(LazyBear), ATR 14, OBV. Library columns are renamed through
a single explicit `_COLUMN_MAP` to stable snake_case — `ema_20/50/200`, `rsi_14`, `macd`/
`macd_signal`/`macd_hist`, `supertrend`, `supertrend_dir`, `squeeze_mom`, `squeeze_on`, `atr_14`,
`obv` — so nothing downstream ever sees `ATRr_14` or `SUPERTd_7_3.0`. The map is asserted against
the actual DataFrame after computation, so a library rename fails loudly at the boundary instead of
producing a silent NaN stop-loss. Adds `obv_slope` (sign of OBV's 5-day linear fit) as the
institutional-flow proxy the spec's "Bandarmology" note asks for.

**Swing math**

- `round_to_tick(price, mode)` — applies `IDX_TICK_BANDS`. SL rounds **down** and TP rounds **down**
  (both conservative: a wider stop and a nearer target are the safe direction to err); entry rounds
  to nearest.
- `evaluate_swing_setup(snapshot) -> SwingPlan` — a frozen dataclass, pure function, no I/O:
  - Gates, each recorded individually in `gate_results: dict[str, bool]` so the UI shows exactly
    which rule failed: `close > ema_50`, `supertrend_dir == 1`,
    `rsi_14 <= RSI_OVERBOUGHT` (the spec's rule is "do not buy if RSI > 70", so RSI exactly 70
    passes).
  - `action` = `BUY_AT_NEXT_OPEN` when all gates pass; `SELL` when
    `supertrend_dir == -1 and close < ema_50`; otherwise `HOLD`.
  - `entry` = last close, carrying an `entry_is_reference = True` flag;
    `sl = round_to_tick(entry - 1.5*atr, "down")`; `tp = round_to_tick(entry + 3.0*atr, "down")`.
  - `rr_ratio` computed **from the rounded prices**, not asserted to be 2.0 — after tick rounding
    the true ratio drifts (e.g. GOTO at Rp50 with a Rp1 tick), and reporting a nominal 2.0 would be
    false. Also carries `gap_risk_pct = atr/close` so the reference-price caveat is quantified.
  - Guards: `None` when `atr_14` is NaN (insufficient history) or when `entry - sl <= 0` after
    rounding, rather than emitting a degenerate plan.
- `scan_watchlist(tickers) -> DataFrame` — one row per ticker with action, gate booleans,
  entry/SL/TP, RR, RSI, SuperTrend state, squeeze state, OBV slope; ranked passers first. Sequential
  fetch, spaced requests, one bounded retry with backoff on rate limits; per-ticker failures
  collected and surfaced, never fatal.
- `screen(table, criteria)` — pure pandas masks for Tab 2 (RSI range, PE max, SuperTrend bullish,
  volume-spike ratio vs 20-day average, sector). No network, so filtering is instant.

### Phase 3 — `modules/database.py`

`sqlite3` only, WAL mode, `check_same_thread=False` (Streamlit reruns off-thread),
`@contextmanager get_connection()` that commits/rolls back. `init_db()` is idempotent; all writes
are `INSERT ... ON CONFLICT ... DO UPDATE` so re-runs are safe.

- `prices(ticker, date, open, high, low, close, adj_close, volume, PK(ticker,date))`
- `fundamentals(ticker PK, long_name, sector, trailing_pe, price_to_book, market_cap,
  dividend_yield, roe, updated_at)`
- `swing_plans(ticker, plan_date, action, entry, sl, tp, rr_ratio, atr, gates_json,
  PK(ticker,plan_date))` — persists the deterministic plan so today's scan survives a rerun and
  yesterday's decisions stay auditable
- `ai_notes(id PK, ticker, plan_date, model, action, rationale, raw, created_at)` — narration kept
  separate from the numbers, matching the architecture
- `mutual_funds(id PK, fund_name, manager, category, nav, return_1y, aum, nav_date, source,
  scraped_at, UNIQUE(fund_name, nav_date))`
- `meta(key PK, value)` — refresh timestamps backing `is_stale(key, ttl_hours)`

### Phase 4 — `modules/ai_engine.py` (narration only)

- `check_ollama() -> (bool, str)` — `ollama.Client(host=OLLAMA_HOST).list()` in try/except over
  connection/httpx/timeout errors; the failure string is actionable (install from ollama.com →
  `ollama serve` → `ollama pull llama3.2`). `list_models()` feeds a model dropdown.
- `SYSTEM_PROMPT` — the spec's disciplined-IDX-quant instruction plus three hard constraints:
  prices are IDR; **the supplied Entry/SL/TP are final and must be quoted verbatim, never
  recomputed**; say so plainly when an input is missing. Includes a not-investment-advice line.
- `generate_swing_trade_plan(plan: SwingPlan, model=None) -> AINarration` (the spec's function
  name) — renders the computed plan plus indicator context into the prompt, calls Ollama with
  `options={"temperature": 0.1}`, requests `{"action", "rationale"}` JSON.
  - **Validator**: any number in the response is checked against `plan`; mismatches beyond one tick
    are stripped and replaced with the Python figure, and the substitution is flagged in the UI. A
    returned `action` that contradicts the deterministic gates is overridden, with the disagreement
    shown — the model narrates, it does not decide.
  - JSON parse failure degrades to narrative-only text with `action=None`; never raises.
  - Persists to `ai_notes`.
- `stream_narration(...)` generator for `st.write_stream`, since a local model takes 30–60s on CPU.
- Offline path returns `AINarration(available=False, message=...)` so callers render the full
  numeric plan regardless.

### Phase 5 — `modules/mutual_funds.py` + `scripts/scrape_funds.py`

- `@dataclass FundRow(fund_name, manager, category, nav, return_1y, aum, nav_date, source)`.
- `parse_id_number(text)` — Indonesian formats: `1.234,56`, `Rp`, `%`, `Miliar`/`Triliun`
  multipliers → float.
- `TableAdapter` driven entirely by a `FUND_SOURCES` entry: navigate → `wait_for_selector` →
  `page.content()` → `pandas.read_html` → `column_map` → `parse_id_number` → `FundRow`s. Adding a
  source is a config edit.
- `scrape(sources=None, headless=None, inspect=False)` — sync Playwright, one Chromium context with
  configured UA/locale/timezone, `PW_REQUEST_DELAY_S` between navigations, per-source try/except so
  one dead site doesn't kill the run. `inspect=True` dumps `data/raw/<source>-<YYYYMMDD>.html` for
  re-deriving selectors. Never requests a robots-disallowed path.
- `import_csv(path)` — same schema; the reliable path given the login gates.
- `load_or_seed()` — cached rows, or the bundled sample tagged `source="sample"`. **Sample data is
  never presented as live**: `source` and `scraped_at` are columns in the table and drive a banner.
- `screen_funds(df, categories, min_return_1y, min_aum)` — pure pandas.
- CLI: `--refresh` (default) · `--inspect` · `--import-csv PATH` · `--source NAME`.

### Phase 6 — `app.py` + `ui/`

`app.py`: `st.set_page_config(layout="wide")`, `init_db()` once via `@st.cache_resource`, three
tabs. Sidebar: Ollama status badge from `check_ollama()`, model selector, last price-refresh / last
fund-scrape timestamps from `meta`, refresh buttons.

- **Tab 1 `tab_swing_picks.py`** — "Run daily scan" builds `scan_watchlist()` instantly (no LLM)
  into a ranked table with a gate-pass column and pass/fail chips per rule. Selecting a row shows
  the daily candle summary plus the trade card: ACTION badge, Entry (labelled *reference —
  recompute at your fill*), SL, TP, R/R after rounding, ATR and gap-risk %, and a number input that
  recomputes SL/TP live from a real fill price. "Explain this setup" streams the rationale; with
  Ollama down the card renders complete minus the prose, with install steps inline. Prompt visible
  in an expander.
- **Tab 2 `tab_stocks.py`** — filters (RSI range, max PE, SuperTrend bullish, volume-spike multiple,
  sector) over a `@st.cache_data(ttl=...)`-wrapped scan; `st.dataframe(selection_mode="single-row")`
  → Plotly figure: candlesticks with EMA 20/50/200 overlays and the SuperTrend line coloured by
  direction, volume subplot, RSI subplot with 30/70 guides, MACD histogram subplot, squeeze-on
  markers on the price axis. Failed tickers in an expander.
- **Tab 3 `tab_mutual_funds.py`** — category multiselect, 1Y-return slider, min-AUM input, sortable
  table, "Refresh (Playwright)" with a spinner and a ~30s warning, plus the sample/stale banner.

### Phase 7 — setup documentation

Once code exists, this document is replaced by a genuine README (and the plan moves to `PLAN.md`)
covering: venv → `pip install -r requirements.txt` → `python -m playwright install chromium` →
install Ollama + `ollama pull llama3.2` → `streamlit run app.py`. Plus the numpy-2/
`pandas-ta-classic` rationale, the `ATRr_14` naming trap, the Python-computes-math architecture and
why, the reference-entry caveat, tick-rounding behaviour, how to add a fund source via `--inspect`,
CSV import, and a prominent not-investment-advice disclaimer.

## Verification

1. **Deps** — on Ubuntu/WSL, `sudo apt install python3.10-venv` first (this box lacks it, and
   without it `python3 -m venv` fails with `No module named ensurepip`). Then:

   ```bash
   python3 -m venv .venv && source .venv/bin/activate     # WSL / Linux
   pip install -r requirements.txt
   python -c "import pandas_ta_classic, streamlit, yfinance, plotly, ollama, playwright"
   ```
   ```powershell
   py -m venv .venv; .\.venv\Scripts\Activate.ps1        # Windows
   pip install -r requirements.txt
   ```
   `pandas-ta-classic` 0.6.52 is **already verified** importing on Python 3.10.12 + numpy 2.2.6 +
   pandas 2.2.2, so the fallback below is contingency, not an expected step. Should it ever break,
   replace only the marked indicator section of `stock_data.py` with pandas-native EMA/RSI/MACD/
   ATR/OBV plus a hand-rolled SuperTrend and Squeeze — `add_indicators()`'s output contract is
   unchanged, so nothing else moves.

   Also run `python config.py` on each machine and confirm `platform`, `journal mode`, and
   `ollama host` read as expected for that box.
2. **Column-name contract** — assert the post-rename frame has `atr_14`, `supertrend_dir`,
   `squeeze_mom`, `ema_200`, and that `_COLUMN_MAP`'s sources exist pre-rename. This is the
   `ATRr_14` guard; run it before anything else touches the swing math. Names confirmed against
   live BBCA data (479 rows / 2y): `EMA_20`, `EMA_50`, `EMA_200`, `RSI_14`,
   `MACD_12_26_9` + `MACDh_` + `MACDs_`, `SUPERT_7_3.0` + `SUPERTd_7_3.0`, `SQZ_20_2.0_20_1.5_LB`,
   **`ATRr_14`**, `OBV`. Note `.history()` also returns `dividends` and `stock_splits` — drop them.
3. **Indicator sanity** — `rsi_14` within [0,100]; `supertrend_dir` ∈ {1,−1}; `atr_14 > 0`; NaN
   prefixes only as long as each indicator's warm-up; `ema_200` non-NaN given 2y of history.
4. **Tick rounding** — table-driven: 50→1, 360→2, 675→5, 4470→10, 6350→25 tick sizes; assert every
   rounded SL/TP is an exact multiple of its band's tick; check both sides of each boundary
   (199/200, 499/500, 1999/2000, 4999/5000).
5. **Swing math golden numbers** — for a fixed synthetic bar (entry 6350, ATR 150): pre-rounding SL
   6125.0 and TP 6800.0, both already valid 25-ticks; then a case that *does* move under rounding
   (entry 675, ATR 23 → SL 640.5→640, TP 744→740) and assert `rr_ratio` equals the recomputed
   post-rounding ratio, not 2.0.
6. **Gate logic** — RSI exactly 70 passes, 70.1 blocks; `close == ema_50` blocks (rule is strict
   `>`); `supertrend_dir == -1` with `close < ema_50` yields SELL; a NaN ATR yields `None`, not a
   plan.
7. **DB** — `init_db()` then `sqlite3 data/finance.db ".tables"` shows all six tables; re-running
   `init_db()` and a repeated upsert changes no row counts (idempotence).
8. **Prices** — `python scripts/refresh_prices.py --tickers BBCA,GOTO,AMMN`, then
   `select ticker, count(*), max(date) from prices group by ticker` returns ~500 rows each with a
   recent max date.
9. **Ollama offline (current state)** — with nothing on `:11434`, `check_ollama()` returns
   `(False, <install steps>)` **and Tab 1 still renders complete Entry/SL/TP cards**. This is the
   headline check for the chosen architecture.
10. **Ollama online** — after `ollama pull llama3.2`, `generate_swing_trade_plan()` returns a
    rationale, a row lands in `ai_notes`, and the validator is exercised by feeding a deliberately
    wrong-number response through it to confirm the Python figure wins and the substitution is
    flagged.
11. **Funds** — `scripts/scrape_funds.py --inspect` writes `data/raw/*.html`; expect selector tuning
    given the login gates. Guaranteed path: `--import-csv data/sample_mutual_funds.csv`, then
    confirm `mutual_funds` rows.
12. **End to end** — `streamlit run app.py`: run the daily scan, open a trade card, override the
    fill price and watch SL/TP move, filter Tab 2 and chart a ticker, filter Tab 3.

## Risks

- **`pandas-ta-classic` install on Python 3.10** — the only install-time unknown; the fallback in
  step 1 is contained to one marked section.
- **Indicator column renames** — the concrete `ATRr_14` trap, mitigated by the step-2 assertion that
  fails loudly rather than yielding a NaN stop.
- **Reference-price entry** — a gap-up at the open invalidates the SL/TP pair. Mitigated by
  labelling, the `gap_risk_pct` figure, and the fill-price recompute input; it remains an inherent
  limitation of planning before the open.
- **Fund scraping** — login gates and WAF mean a scrape may return nothing; the adapter/`--inspect`/
  CSV design and honest banner absorb that, and CSV import is the reliable path.
- **yfinance `.info`** — breaks upstream periodically and rate-limits; every access guarded, so a
  fundamentals outage degrades Tab 2 to technical-only columns while Tab 1 (which needs no
  fundamentals) is unaffected.
- **Local LLM latency** — 30–60s per narration on CPU; batch scan is LLM-free and `ai_notes` caches
  prose, so latency never blocks the daily overview.
- **Out of scope by decision** — position sizing in lots and the liquidity guard. Worth revisiting:
  without a liquidity check, a thin small cap can produce a plan that is not fillable at the stated
  price.

---

*Nothing in this repository is investment advice. Signals are mechanical output from public data and
a local language model; verify every number before risking capital.*
