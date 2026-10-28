"""
Portfolio Tracker - "My Portfolio" vs "Recommended Portfolio"
------------------------------------------------------------------
Four tabs (My Options added 2026-10-02 for LEAPs / long-dated options):
  - "My Portfolio"          : what you actually hold. You type in Ticker,
                               Shares Held, and Average Cost (blue cells);
                               everything else (current price, market value,
                               gain/loss, weight %) is calculated for you
                               from the latest screener data.
  - "Recommended Portfolio" : this month's Top 20 + your manually-tracked
                               tickers, each with a Suggested Entry Price
                               for the month - what the screener/calculator
                               is currently pointing at.
  - "Portfolio vs Recommended" : gap analysis - which of your holdings are
                               (or aren't) currently recommended, and which
                               recommended picks you don't own yet.

Like Trade_Tracker.xlsx, this is append-safe for "My Portfolio": re-running
this script NEVER erases or overwrites tickers you've already entered there
- it only adds rows for brand new tickers you haven't listed yet, and
refreshes the calculated (not manually-typed) columns.
"""

import json
import os
import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import trade_journal

JSON_FILE = "top20_calculator_data.json"
REAL_DATA_FILE = "Complete_US_Market_Report.xlsx"
PORTFOLIO_FILE = "Portfolio_Tracker.xlsx"

# Seed "My Portfolio" with these tickers on first creation (0 shares, blue
# and fully editable) - convenient starting point since these are the ones
# you've been tracking anyway. Delete rows you don't actually hold, or add
# more any time - this list only matters the very first time the file is
# created; after that, your own rows in the sheet are what's used.
STARTER_TICKERS = ["CLS", "DRAM", "HOOD", "INTC", "IREN", "IWM", "MRVL",
                   "NVDA", "SKHY", "SOFI", "SOXL", "VOO", "WDC"]

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name="Arial", size=10)
INPUT_FONT = Font(name="Arial", size=10, color="0000FF")
INPUT_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
FORMULA_FONT = Font(name="Arial", size=10, color="000000")
AUTO_FONT = Font(name="Arial", size=10, color="1F4E79")  # synced from Trade_Tracker.xlsx My Trades
GREEN_FONT = Font(name="Arial", size=10, color="1B7A1B", bold=True)
RED_FONT = Font(name="Arial", size=10, color="B00020", bold=True)
THIN_BORDER = Border(*(Side(style="thin", color="D0D0D0"),) * 4)
NOTE_FONT = Font(name="Arial", italic=True, size=9, color="808080")
CURRENCY_FMT = '$#,##0.00;($#,##0.00)'
PCT_FMT = '0.0%'
INT_FMT = '#,##0'


def _safe_float(v):
    try:
        if v in (None, "N/A", "", "nan"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def load_recommended_rows():
    """This month's Top 20 (real sizing data, written by
    generate_vlookup_calculator.py)."""
    if not os.path.exists(JSON_FILE):
        return [], False
    with open(JSON_FILE) as f:
        data = json.load(f)
    return data["rows"], data.get("is_real", False)


def load_universe_data():
    """Ticker -> full row dict from the real screener output - used to
    price up My Portfolio holdings and any manually-tracked tickers that
    aren't in this month's Top 20."""
    if not os.path.exists(REAL_DATA_FILE):
        return {}
    try:
        import pandas as pd
        df = pd.read_excel(REAL_DATA_FILE)
        return {str(r["Ticker"]).upper(): r.to_dict() for _, r in df.iterrows()}
    except Exception as e:
        print(f"[WARNING] Could not read '{REAL_DATA_FILE}': {e}")
        return {}


def compute_suggested_entry(current_price, lower_bb):
    cp, lb = _safe_float(current_price), _safe_float(lower_bb)
    if cp is None:
        return None
    if lb is None:
        return round(cp * 0.995, 2)
    dist = cp - lb
    return round(min(cp * 0.995, lb + dist * 0.35), 2)


def style_header(ws, headers, row_num=1):
    for c, name in enumerate(headers, start=1):
        cell = ws.cell(row=row_num, column=c, value=name)
        cell.fill, cell.font, cell.border = HEADER_FILL, HEADER_FONT, THIN_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


# ---------------------------------------------------------------------------
# SHEET 1: My Portfolio
# ---------------------------------------------------------------------------
MY_PORTFOLIO_HEADERS = [
    "Ticker", "Shares Held", "Average Cost", "Company", "Current Price",
    "Market Value", "Cost Basis", "Unrealized Gain/Loss ($)",
    "Unrealized Gain/Loss (%)", "Portfolio Weight (%)",
    "This Month Suggested Entry", "vs. Suggested Entry", "Analyst Target Price",
    "MACD Histogram Trend",
]
MY_PORTFOLIO_INPUT_COLS = {"Ticker", "Shares Held", "Average Cost"}


def sync_from_journal(wb, positions):
    """2026-10-09: when Trade_Tracker.xlsx 'My Trades' has rows, it is the
    source of truth for what you hold. Rewrite My Portfolio's Ticker /
    Shares Held / Average Cost from it (sold-out tickers drop off, new buys
    appear). Warns about any hand edit made here that disagrees, so a change
    typed in the wrong file is never silently lost without a trace."""
    ws = wb["My Portfolio"] if "My Portfolio" in wb.sheetnames else None
    if ws is None:
        ws = wb.create_sheet("My Portfolio", 0)
        style_header(ws, MY_PORTFOLIO_HEADERS)
        ws.freeze_panes = "A2"
    h = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
    before = {}
    for r in range(2, ws.max_row + 1):
        t = ws.cell(row=r, column=h["Ticker"]).value
        if t and isinstance(t, str) and len(t) <= 10 and " " not in t:
            before[t.upper()] = (_safe_float(ws.cell(row=r, column=h["Shares Held"]).value) or 0,
                                 _safe_float(ws.cell(row=r, column=h["Average Cost"]).value) or 0)
    held = {t: p for t, p in trade_journal.stock_positions(positions).items() if p["shares"] > 0}
    for t in sorted(set(before) | set(held)):
        old_sh, old_ac = before.get(t, (0, 0))
        new_sh = held[t]["shares"] if t in held else 0
        new_ac = held[t]["avg_cost"] if t in held else 0
        if abs(old_sh - new_sh) > 1e-4 or (new_sh and abs(old_ac - new_ac) > 0.005):
            print(f"[MY TRADES] {t}: My Portfolio said {old_sh:g} @ {old_ac:.3f} -> now {new_sh:g} @ {new_ac:.3f} (from My Trades)")
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)
    for i, t in enumerate(sorted(held), start=2):
        write_portfolio_row(ws, h, i, {"Ticker": t, "Shares Held": held[t]["shares"],
                                       "Average Cost": held[t]["avg_cost"]})
        for name in MY_PORTFOLIO_INPUT_COLS:  # no longer typed here - show them as auto
            cell = ws.cell(row=i, column=h[name])
            cell.font, cell.fill = AUTO_FONT, PatternFill(fill_type=None)
    return len(held)


def sync_options_from_journal(wb, positions):
    """2026-10-09: option lines (Call/Put + Strike + Expiry rows in My Trades)
    drive 'My Options' the same way stocks drive 'My Portfolio'. Rewrites the
    yellow input columns - one row per open contract line, Premium Paid =
    average premium per share - and build_my_options() then prices them."""
    if "My Options" in wb.sheetnames:
        ws = wb["My Options"]
        col = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        for name in OPT_HEADERS:
            if name not in col:
                c = ws.max_column + 1
                ws.cell(row=1, column=c, value=name)
                col[name] = c
    else:
        ws = wb.create_sheet("My Options")
        style_header(ws, OPT_HEADERS)
        ws.freeze_panes = "B2"
        col = {name: i + 1 for i, name in enumerate(OPT_HEADERS)}

    def key(und, cp, strike, exp):
        return (str(und).strip().upper(), cp, round(strike or 0, 4), exp)

    before = {}
    for r in range(2, ws.max_row + 1):
        und = ws.cell(row=r, column=col["Underlying"]).value
        if not _is_underlying(und):
            continue
        k = key(und, _norm_cp(ws.cell(row=r, column=col["Call/Put"]).value),
                _safe_float(ws.cell(row=r, column=col["Strike"]).value),
                _parse_expiry(ws.cell(row=r, column=col["Expiry"]).value))
        n = _safe_float(ws.cell(row=r, column=col["Contracts"]).value) or 0
        before[k] = before.get(k, 0) + n
    opts = trade_journal.option_positions(positions)
    after = {key(p["ticker"], p["cp"], p["strike"], p["expiry"]): p["shares"] for p in opts}
    for k in sorted(set(before) | set(after), key=str):
        if abs(before.get(k, 0) - after.get(k, 0)) > 1e-6:
            print(f"[MY TRADES] Option {k[0]} {k[1]}{k[2]:g} {k[3]}: My Options said {before.get(k, 0):g} "
                  f"contract(s) -> now {after.get(k, 0):g} (from My Trades)")

    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)
    for i, p in enumerate(opts, start=2):
        vals = {"Underlying": p["ticker"], "Call/Put": p["cp"], "Strike": p["strike"],
                "Expiry": datetime.datetime.combine(p["expiry"], datetime.time()),
                "Contracts": p["shares"], "Premium Paid (per share)": p["avg_cost"],
                "Notes": p.get("notes")}
        for name, v in vals.items():
            ws.cell(row=i, column=col[name], value=v)
    return len(opts)


def build_my_portfolio(wb, universe_data, recommended_by_ticker, from_journal=False):
    if "My Portfolio" in wb.sheetnames:
        ws = wb["My Portfolio"]
        existing_tickers = set()
        header_row_idx = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        last_ticker_row = 1
        for r in range(2, ws.max_row + 1):
            t = ws.cell(row=r, column=header_row_idx["Ticker"]).value
            # A real ticker is a short, space-free string (e.g. "NVDA",
            # "BRK-B"). The note text also lives in column 1, so without
            # this check its prose would get mistaken for "the last ticker
            # row" and push every future run's rows further down forever.
            if t and isinstance(t, str) and len(t) <= 10 and " " not in t:
                existing_tickers.add(t.upper())
                last_ticker_row = r
        # Clear anything below the last real ticker row (old note text, blank
        # rows) so it doesn't accumulate run after run
        if ws.max_row > last_ticker_row:
            ws.delete_rows(last_ticker_row + 1, ws.max_row - last_ticker_row)
        next_row = last_ticker_row + 1
        is_new = False
    else:
        ws = wb.create_sheet("My Portfolio")
        style_header(ws, MY_PORTFOLIO_HEADERS)
        ws.freeze_panes = "A2"
        header_row_idx = {name: i + 1 for i, name in enumerate(MY_PORTFOLIO_HEADERS)}
        existing_tickers = set()
        next_row = 2
        is_new = True

    added = 0
    if is_new and not from_journal:
        for ticker in STARTER_TICKERS:
            row_vals = {"Ticker": ticker, "Shares Held": 0, "Average Cost": 0}
            write_portfolio_row(ws, header_row_idx, next_row, row_vals)
            existing_tickers.add(ticker)
            next_row += 1
            added += 1

    # Refresh calculated columns (Company/Current Price/Market Value/etc.)
    # for EVERY row already there - never touches Ticker/Shares Held/
    # Average Cost, since those are your typed-in facts.
    refreshed = 0
    ticker_col = header_row_idx["Ticker"]
    for r in range(2, next_row):
        ticker = ws.cell(row=r, column=ticker_col).value
        if not ticker:
            continue
        ticker = str(ticker).upper()
        shares = _safe_float(ws.cell(row=r, column=header_row_idx["Shares Held"]).value) or 0
        avg_cost = _safe_float(ws.cell(row=r, column=header_row_idx["Average Cost"]).value) or 0

        u = universe_data.get(ticker, {})
        company = u.get("Name", ticker)
        current_price = _safe_float(u.get("Current Price"))

        rec = recommended_by_ticker.get(ticker)
        suggested_entry = rec["entry"] if rec else compute_suggested_entry(
            u.get("Current Price"), u.get("Support (Lower BB)"))
        analyst_target = (rec.get("analyst_target") if rec else None) or _safe_float(u.get("Analyst Target Price"))
        macd_trend = (rec.get("macd_trend") if rec else None) or u.get("MACD Histogram Trend", "N/A")

        market_value = round(shares * current_price, 2) if current_price is not None else None
        cost_basis = round(shares * avg_cost, 2) if shares and avg_cost else 0
        gain_dollar = round(market_value - cost_basis, 2) if market_value is not None else None
        gain_pct = round(gain_dollar / cost_basis, 4) if gain_dollar is not None and cost_basis else None
        vs_entry = (round(current_price - suggested_entry, 2)
                    if current_price is not None and suggested_entry is not None else None)

        set_cell(ws, r, header_row_idx["Company"], company, BODY_FONT)
        set_cell(ws, r, header_row_idx["Current Price"], current_price, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, header_row_idx["Market Value"], market_value, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, header_row_idx["Cost Basis"], cost_basis, FORMULA_FONT, CURRENCY_FMT)
        gl_font = GREEN_FONT if (gain_dollar or 0) >= 0 else RED_FONT
        set_cell(ws, r, header_row_idx["Unrealized Gain/Loss ($)"], gain_dollar, gl_font, CURRENCY_FMT)
        set_cell(ws, r, header_row_idx["Unrealized Gain/Loss (%)"], gain_pct, gl_font, PCT_FMT)
        set_cell(ws, r, header_row_idx["This Month Suggested Entry"], suggested_entry, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, header_row_idx["vs. Suggested Entry"], vs_entry, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, header_row_idx["Analyst Target Price"], analyst_target, FORMULA_FONT, CURRENCY_FMT)
        trend_font = RED_FONT if macd_trend == "FALLING" else (GREEN_FONT if macd_trend == "RISING" else BODY_FONT)
        set_cell(ws, r, header_row_idx["MACD Histogram Trend"], macd_trend, trend_font)
        refreshed += 1

    # Portfolio Weight (%) needs the total across all rows, so compute after
    total_value = 0.0
    for r in range(2, next_row):
        mv = ws.cell(row=r, column=header_row_idx["Market Value"]).value
        if isinstance(mv, (int, float)):
            total_value += mv
    for r in range(2, next_row):
        mv = ws.cell(row=r, column=header_row_idx["Market Value"]).value
        weight = round(mv / total_value, 4) if isinstance(mv, (int, float)) and total_value else None
        set_cell(ws, r, header_row_idx["Portfolio Weight (%)"], weight, FORMULA_FONT, PCT_FMT)

    for c, w in enumerate([10, 12, 13, 20, 13, 13, 13, 16, 16, 14, 18, 14, 16, 16], start=1):
        ws.column_dimensions[get_column_letter(c)].width = w

    note_row = next_row + 2
    if from_journal:
        note = ("Ticker / Shares Held / Average Cost come from Trade_Tracker.xlsx > My Trades - record buys "
                "and sells THERE (edits made here are overwritten on the next run). Everything else "
                f"recalculates from {REAL_DATA_FILE}. Total Portfolio Value: ${total_value:,.2f}")
    else:
        note = ("Blue cells (Ticker, Shares Held, Average Cost) are yours to edit - "
                "add/remove rows for what you actually hold. Everything else recalculates "
                "each time you run generate_portfolio_tracker.py, using the latest data from "
                f"{REAL_DATA_FILE}. Total Portfolio Value: ${total_value:,.2f}")
    ws.cell(row=note_row, column=1, value=note).font = NOTE_FONT
    return ws, total_value


def write_portfolio_row(ws, header_row_idx, row_idx, values):
    for name in MY_PORTFOLIO_HEADERS:
        cell = ws.cell(row=row_idx, column=header_row_idx[name])
        if name in MY_PORTFOLIO_INPUT_COLS:
            cell.value = values.get(name)
            cell.font, cell.fill = INPUT_FONT, INPUT_FILL
        cell.border = THIN_BORDER


def set_cell(ws, row, col, value, font, number_format=None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = font
    cell.border = THIN_BORDER
    if number_format:
        cell.number_format = number_format


# ---------------------------------------------------------------------------
# SHEET 2: Recommended Portfolio
# ---------------------------------------------------------------------------
REC_HEADERS = ["Ticker", "Company", "Category", "Signal", "Matrix Score",
               "Current Price", "Suggested Entry Price (This Month)", "Analyst Target Price",
               "MACD Histogram Trend", "Shares for $1K/Mo Target", "Capital Required"]


def build_recommended_portfolio(wb, recommended_rows, is_real, month_tag):
    if "Recommended Portfolio" in wb.sheetnames:
        del wb["Recommended Portfolio"]
    ws = wb.create_sheet("Recommended Portfolio")
    style_header(ws, REC_HEADERS)
    ws.freeze_panes = "A2"

    for i, r in enumerate(recommended_rows, start=2):
        set_cell(ws, i, 1, r["Ticker"], BODY_FONT)
        set_cell(ws, i, 2, r.get("Name", r["Ticker"]), BODY_FONT)
        set_cell(ws, i, 3, r.get("Category"), BODY_FONT)
        sig = r.get("Signal")
        sig_font = GREEN_FONT if sig == "BUY" else (RED_FONT if sig == "SELL" else BODY_FONT)
        set_cell(ws, i, 4, sig, sig_font)
        set_cell(ws, i, 5, r.get("Matrix Score"), BODY_FONT)
        set_cell(ws, i, 6, _safe_float(r.get("Current Price")), BODY_FONT, CURRENCY_FMT)
        set_cell(ws, i, 7, r.get("This Month Entry Price"), BODY_FONT, CURRENCY_FMT)
        set_cell(ws, i, 8, r.get("Analyst Target Price"), BODY_FONT, CURRENCY_FMT)
        macd_trend = r.get("MACD Histogram Trend", "N/A")
        trend_font = RED_FONT if macd_trend == "FALLING" else (GREEN_FONT if macd_trend == "RISING" else BODY_FONT)
        set_cell(ws, i, 9, macd_trend, trend_font)
        set_cell(ws, i, 10, r.get("Shares Needed (This Month)"), BODY_FONT, INT_FMT)
        set_cell(ws, i, 11, r.get("Capital Required (This Month)"), BODY_FONT, CURRENCY_FMT)

    for c, w in enumerate([10, 22, 16, 10, 12, 13, 22, 16, 14, 18, 16], start=1):
        ws.column_dimensions[get_column_letter(c)].width = w

    note_row = len(recommended_rows) + 3
    mode = "REAL" if is_real else "SIMULATED - run stock_screener_us.py for real prices"
    ws.cell(row=note_row, column=1,
            value=(f"Month: {month_tag} | Data: {mode}. 'Suggested Entry Price' is calculated "
                   "from spot price vs. Bollinger support - a planning figure, not a live order.")
            ).font = NOTE_FONT
    return ws


# ---------------------------------------------------------------------------
# SHEET 3: Portfolio vs Recommended (gap analysis)
# ---------------------------------------------------------------------------
GAP_HEADERS = ["Ticker", "Status", "You Hold (Shares)", "Your Avg Cost",
               "This Month Suggested Entry", "Notes"]


def build_gap_analysis(wb, my_portfolio_tickers_shares, recommended_by_ticker, universe_data):
    if "Portfolio vs Recommended" in wb.sheetnames:
        del wb["Portfolio vs Recommended"]
    ws = wb.create_sheet("Portfolio vs Recommended")
    style_header(ws, GAP_HEADERS)
    ws.freeze_panes = "A2"

    all_tickers = sorted(set(my_portfolio_tickers_shares) | set(recommended_by_ticker))
    row = 2
    for ticker in all_tickers:
        held = my_portfolio_tickers_shares.get(ticker)
        rec = recommended_by_ticker.get(ticker)
        shares, avg_cost = (held if held else (None, None))

        if held and held[0] and rec:
            status, note = "HELD + RECOMMENDED", "Currently recommended and you already own it"
        elif held and held[0] and not rec:
            status, note = "HELD, NOT RECOMMENDED", "You own it, but it's not in this month's picks"
        elif (not held or not held[0]) and rec:
            status, note = "RECOMMENDED, NOT HELD", "This month's pick - you don't own it yet"
        else:
            continue  # in your list with 0 shares and not recommended - skip, nothing to show

        status_font = GREEN_FONT if status == "HELD + RECOMMENDED" else (
            RED_FONT if status == "RECOMMENDED, NOT HELD" else BODY_FONT)
        set_cell(ws, row, 1, ticker, BODY_FONT)
        set_cell(ws, row, 2, status, status_font)
        set_cell(ws, row, 3, shares, BODY_FONT, INT_FMT)
        set_cell(ws, row, 4, avg_cost, BODY_FONT, CURRENCY_FMT)
        suggested = rec["entry"] if rec else compute_suggested_entry(
            universe_data.get(ticker, {}).get("Current Price"),
            universe_data.get(ticker, {}).get("Support (Lower BB)"))
        set_cell(ws, row, 5, suggested, BODY_FONT, CURRENCY_FMT)
        set_cell(ws, row, 6, note, NOTE_FONT)
        row += 1

    for c, w in enumerate([10, 22, 16, 14, 22, 40], start=1):
        ws.column_dimensions[get_column_letter(c)].width = w


# ---------------------------------------------------------------------------
# SHEET: My Options  (LEAPs / long-dated calls & puts)  - added 2026-10-02
# ---------------------------------------------------------------------------
# Stocks and options can't share the "My Portfolio" tab: a stock row is
# shares x share price, an option is contracts x 100 x option premium, with
# a strike and an expiry on top. So options get their own tab. Same rule as
# My Portfolio: the yellow input cells are YOURS and are never overwritten -
# every run only refreshes the calculated columns to the right of them.
OPT_HEADERS = [
    # --- your inputs (yellow) ---
    "Underlying", "Call/Put", "Strike", "Expiry", "Contracts", "Premium Paid (per share)", "Notes",
    # --- calculated each run ---
    "Company", "Stock Price", "Option Price (now)", "Price Source", "Cost Basis", "Market Value",
    "Unrealized P/L ($)", "Unrealized P/L (%)", "Days to Expiry", "Breakeven Price",
    "Stock vs Breakeven (%)", "Intrinsic Value (per share)", "Time Value (per share)",
    "Implied Volatility", "Stock Signal", "KDJ Action", "Contract Symbol",
]
OPT_INPUT_COLS = ["Underlying", "Call/Put", "Strike", "Expiry", "Contracts",
                  "Premium Paid (per share)", "Notes"]
OPT_MULTIPLIER = 100  # 1 US equity option contract = 100 shares
ORANGE_FONT = Font(name="Arial", size=10, color="C26A00", bold=True)


def _is_underlying(v):
    """Real tickers are short and space-free (NVDA, BRK-B). Anything else in
    column A is the note text or a blank, and is not a position."""
    return bool(v) and isinstance(v, str) and len(v.strip()) <= 10 and " " not in v.strip()


def _parse_expiry(v):
    """Accepts a real Excel date, or text like 2027-01-15 / 15/1/2027 /
    15-Jan-2027 / Jan 15 2027. Returns a datetime.date or None."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%b %d %Y", "%b %d, %Y",
                "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _norm_cp(v):
    s = str(v or "").strip().upper()
    if s in ("C", "CALL", "CALLS"):
        return "C"
    if s in ("P", "PUT", "PUTS"):
        return "P"
    return None


def fetch_option_quotes(positions):
    """positions: list of (underlying, cp, strike, expiry_date). Looks each
    contract up in Yahoo's option chain - one download per underlying+expiry,
    not per row. Returns {key: {...}} where key = (und, cp, strike, expiry).
    Never raises: a missing contract / no network just leaves that row's
    'Option Price (now)' blank and says why in 'Price Source'."""
    out = {}
    if not positions:
        return out
    try:
        import yfinance as yf
        import warnings; warnings.filterwarnings("ignore", message=".*inplace keyword.*")  # yfinance re-enables pandas inplace notices on import
    except ImportError:
        for k in positions:
            out[k] = {"source": "yfinance not installed"}
        return out

    by_chain = {}
    for k in positions:
        by_chain.setdefault((k[0], k[3]), []).append(k)

    import time
    for (und, exp), keys in by_chain.items():
        exp_str = exp.strftime("%Y-%m-%d")
        try:
            tk = yf.Ticker(und)
            listed = list(tk.options or [])
            if exp_str not in listed:
                for k in keys:
                    out[k] = {"source": f"expiry {exp_str} not listed on Yahoo"}
                continue
            chain = tk.option_chain(exp_str)
            for k in keys:
                df = chain.calls if k[1] == "C" else chain.puts
                hit = df[(df["strike"] - k[2]).abs() < 0.001]
                if hit.empty:
                    out[k] = {"source": f"strike {k[2]:g} not found"}
                    continue
                row = hit.iloc[0]
                bid, ask = _safe_float(row.get("bid")), _safe_float(row.get("ask"))
                last = _safe_float(row.get("lastPrice"))
                if bid and ask and bid > 0 and ask > 0:
                    price, src = round((bid + ask) / 2, 2), "Yahoo mid (bid/ask)"
                elif last:
                    price, src = round(last, 2), "Yahoo last trade"
                else:
                    price, src = None, "no quote"
                iv = _safe_float(row.get("impliedVolatility"))
                out[k] = {"price": price, "source": src, "iv": iv,
                          "symbol": row.get("contractSymbol")}
        except Exception as e:
            for k in keys:
                out[k] = {"source": f"lookup failed: {str(e)[:60]}"}
        time.sleep(0.5)  # same polite pacing as the screener
    return out


def _stock_price_fallback(und):
    """Underlying not in the screener universe -> ask Yahoo directly."""
    try:
        import yfinance as yf
        import warnings; warnings.filterwarnings("ignore", message=".*inplace keyword.*")  # yfinance re-enables pandas inplace notices on import
        return _safe_float(yf.Ticker(und).fast_info.get("last_price"))
    except Exception:
        return None


def build_my_options(wb, universe_data, from_journal=False):
    if "My Options" in wb.sheetnames:
        ws = wb["My Options"]
        col = {ws.cell(row=1, column=c).value: c for c in range(1, ws.max_column + 1)}
        # Add any header that's missing (e.g. a column added in a later version)
        for name in OPT_HEADERS:
            if name not in col:
                c = ws.max_column + 1
                ws.cell(row=1, column=c, value=name)
                col[name] = c
        style_header(ws, [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)])
    else:
        ws = wb.create_sheet("My Options")
        style_header(ws, OPT_HEADERS)
        ws.freeze_panes = "B2"
        col = {name: i + 1 for i, name in enumerate(OPT_HEADERS)}

    und_c = col["Underlying"]
    last_pos_row = 1
    for r in range(2, ws.max_row + 1):
        if _is_underlying(ws.cell(row=r, column=und_c).value):
            last_pos_row = r
    # Remove only what's BELOW the last real position (the old note text) -
    # never a position row itself.
    if ws.max_row > last_pos_row:
        ws.delete_rows(last_pos_row + 1, ws.max_row - last_pos_row)

    # Pass 1: read your inputs
    rows = []
    for r in range(2, last_pos_row + 1):
        und = ws.cell(row=r, column=und_c).value
        if not _is_underlying(und):
            continue
        und = und.strip().upper()
        cp = _norm_cp(ws.cell(row=r, column=col["Call/Put"]).value)
        strike = _safe_float(ws.cell(row=r, column=col["Strike"]).value)
        expiry = _parse_expiry(ws.cell(row=r, column=col["Expiry"]).value)
        contracts = _safe_float(ws.cell(row=r, column=col["Contracts"]).value) or 0
        premium = _safe_float(ws.cell(row=r, column=col["Premium Paid (per share)"]).value)
        rows.append(dict(r=r, und=und, cp=cp, strike=strike, expiry=expiry,
                         contracts=contracts, premium=premium))

    keys = list({(x["und"], x["cp"], x["strike"], x["expiry"]) for x in rows
                 if x["cp"] and x["strike"] and x["expiry"] and x["contracts"]})
    quotes = fetch_option_quotes(keys)
    today = datetime.date.today()

    total_cost = total_value = 0.0
    for x in rows:
        r = x["r"]
        # keep your input cells styled as inputs (never change their values);
        # when they come from My Trades, style them as auto-filled instead
        for name in OPT_INPUT_COLS:
            cell = ws.cell(row=r, column=col[name])
            if from_journal:
                cell.font, cell.fill, cell.border = AUTO_FONT, PatternFill(fill_type=None), THIN_BORDER
            else:
                cell.font, cell.fill, cell.border = INPUT_FONT, INPUT_FILL, THIN_BORDER
        ws.cell(row=r, column=col["Strike"]).number_format = CURRENCY_FMT
        ws.cell(row=r, column=col["Premium Paid (per share)"]).number_format = CURRENCY_FMT
        ws.cell(row=r, column=col["Expiry"]).number_format = "dd-mmm-yyyy"

        u = universe_data.get(x["und"], {})
        stock = _safe_float(u.get("Current Price"))
        if stock is None and x["contracts"]:
            stock = _stock_price_fallback(x["und"])
        problems = []
        if not x["cp"]: problems.append("Call/Put must be C or P")
        if not x["strike"]: problems.append("Strike missing")
        if not x["expiry"]: problems.append("Expiry not a date")
        if x["premium"] is None: problems.append("Premium missing")

        q = quotes.get((x["und"], x["cp"], x["strike"], x["expiry"]), {})
        opt_px = q.get("price")
        mult = x["contracts"] * OPT_MULTIPLIER
        cost = round(mult * x["premium"], 2) if x["premium"] is not None else None
        value = round(mult * opt_px, 2) if opt_px is not None else None
        pl = round(value - cost, 2) if (value is not None and cost is not None) else None
        pl_pct = round(pl / cost, 4) if (pl is not None and cost) else None
        dte = (x["expiry"] - today).days if x["expiry"] else None
        breakeven = None
        if x["strike"] and x["premium"] is not None and x["cp"]:
            breakeven = round(x["strike"] + x["premium"], 2) if x["cp"] == "C" else round(x["strike"] - x["premium"], 2)
        vs_be = round((stock - breakeven) / breakeven, 4) if (stock and breakeven) else None
        if vs_be is not None and x["cp"] == "P":
            vs_be = -vs_be  # for puts, being BELOW breakeven is the good side
        intrinsic = None
        if stock is not None and x["strike"] and x["cp"]:
            intrinsic = round(max(0.0, stock - x["strike"]) if x["cp"] == "C" else max(0.0, x["strike"] - stock), 2)
        time_val = round(opt_px - intrinsic, 2) if (opt_px is not None and intrinsic is not None) else None

        if x["contracts"]:
            total_cost += cost or 0
            total_value += value if value is not None else (cost or 0)

        source = "; ".join(problems) if problems else (q.get("source") or ("closed (0 contracts)" if not x["contracts"] else ""))
        set_cell(ws, r, col["Company"], u.get("Name", x["und"]), BODY_FONT)
        set_cell(ws, r, col["Stock Price"], stock, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, col["Option Price (now)"], opt_px, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, col["Price Source"], source, NOTE_FONT if not problems else RED_FONT)
        set_cell(ws, r, col["Cost Basis"], cost, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, col["Market Value"], value, FORMULA_FONT, CURRENCY_FMT)
        plf = GREEN_FONT if (pl or 0) >= 0 else RED_FONT
        set_cell(ws, r, col["Unrealized P/L ($)"], pl, plf, CURRENCY_FMT)
        set_cell(ws, r, col["Unrealized P/L (%)"], pl_pct, plf, PCT_FMT)
        dte_font = RED_FONT if (dte is not None and dte <= 90) else (ORANGE_FONT if (dte is not None and dte <= 180) else BODY_FONT)
        set_cell(ws, r, col["Days to Expiry"], dte, dte_font, INT_FMT)
        set_cell(ws, r, col["Breakeven Price"], breakeven, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, col["Stock vs Breakeven (%)"], vs_be, GREEN_FONT if (vs_be or 0) >= 0 else RED_FONT, PCT_FMT)
        set_cell(ws, r, col["Intrinsic Value (per share)"], intrinsic, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, col["Time Value (per share)"], time_val, FORMULA_FONT, CURRENCY_FMT)
        set_cell(ws, r, col["Implied Volatility"], q.get("iv"), FORMULA_FONT, PCT_FMT)
        set_cell(ws, r, col["Stock Signal"], u.get("Signal", "—"), BODY_FONT)
        kdj = u.get("KDJ Action")
        set_cell(ws, r, col["KDJ Action"], kdj if isinstance(kdj, str) else "—", BODY_FONT)
        set_cell(ws, r, col["Contract Symbol"], q.get("symbol"), NOTE_FONT)

    # Pre-style 10 empty input rows below your positions, ready to type into
    first_blank = last_pos_row + 1
    for r in range(first_blank, first_blank + (0 if from_journal else 10)):
        for name in OPT_INPUT_COLS:
            cell = ws.cell(row=r, column=col[name])
            cell.font, cell.fill, cell.border = INPUT_FONT, INPUT_FILL, THIN_BORDER
        ws.cell(row=r, column=col["Expiry"]).number_format = "dd-mmm-yyyy"
        ws.cell(row=r, column=col["Strike"]).number_format = CURRENCY_FMT
        ws.cell(row=r, column=col["Premium Paid (per share)"]).number_format = CURRENCY_FMT

    widths = {"Underlying": 11, "Call/Put": 8, "Strike": 10, "Expiry": 13, "Contracts": 10,
              "Premium Paid (per share)": 14, "Notes": 22, "Company": 20, "Price Source": 22,
              "Contract Symbol": 22}
    for name, c in col.items():
        ws.column_dimensions[get_column_letter(c)].width = widths.get(name, 13)

    note_row = first_blank + 11
    notes = [
        "Underlying / Call/Put / Strike / Expiry / Contracts / Premium Paid / Notes come from "
        "Trade_Tracker.xlsx > My Trades (rows with Call/Put + Strike + Expiry filled) - record option "
        "buys and sells THERE; edits made here are overwritten on the next run.",
        "Everything right of 'Notes' recalculates every run from Yahoo's option chain (after-hours runs use "
        "the last trade if there is no live bid/ask). Days to Expiry turns orange under 180 days and red "
        "under 90 - the usual window to roll a LEAP.",
        f"Options total: cost ${total_cost:,.2f} · value ${total_value:,.2f}",
    ] if from_journal else [
        "HOW TO ENTER A LEAP: one row per contract line. Underlying = stock ticker (e.g. NVDA). "
        "Call/Put = C or P. Strike = 200. Expiry = the date, e.g. 15-Jan-2027. "
        "Contracts = how many you bought (e.g. 2). Premium Paid = price PER SHARE you paid, as quoted by "
        "your broker (e.g. 45.00 means $4,500 per contract).",
        "Everything right of 'Notes' recalculates every run from Yahoo's option chain (after-hours runs use "
        "the last trade if there is no live bid/ask). Closed a position? Set Contracts to 0 (keeps the row "
        "as history) or delete the row. Days to Expiry turns orange under 180 days and red under 90 "
        "- the usual window to roll a LEAP.",
        f"Options total: cost ${total_cost:,.2f} · value ${total_value:,.2f}",
    ]
    for i, t in enumerate(notes):
        ws.cell(row=note_row + i, column=1, value=t).font = NOTE_FONT

    print(f"[OPTIONS] {sum(1 for x in rows if x['contracts'])} open option position(s) - "
          f"cost ${total_cost:,.2f}, value ${total_value:,.2f}")
    return ws, total_cost, total_value


def main():
    recommended_rows, is_real = load_recommended_rows()
    universe_data = load_universe_data()
    month_tag = datetime.date.today().strftime("%Y-%m")

    recommended_by_ticker = {
        r["Ticker"]: {"entry": r.get("This Month Entry Price"),
                      "analyst_target": _safe_float(r.get("Analyst Target Price")),
                      "macd_trend": r.get("MACD Histogram Trend", "N/A")}
        for r in recommended_rows
    }

    if os.path.exists(PORTFOLIO_FILE):
        wb = load_workbook(PORTFOLIO_FILE)
    else:
        wb = Workbook()
        wb.remove(wb.active)  # remove default blank sheet, we name our own

    # Trade journal (Trade_Tracker.xlsx > My Trades) drives holdings when it has rows
    try:
        has_journal, _, positions, sells = trade_journal.load()
    except Exception as e:
        print(f"[WARNING] Could not read My Trades from {trade_journal.TRACKER_FILE}: {e} - "
              "keeping My Portfolio as typed.")
        has_journal, positions, sells = False, {}, []
    if has_journal:
        n = sync_from_journal(wb, positions)
        n_opt = sync_options_from_journal(wb, positions)
        print(f"[MY TRADES] Synced from {trade_journal.TRACKER_FILE}: {n} stock position(s) -> My Portfolio, "
              f"{n_opt} option line(s) -> My Options; realized P/L to date "
              f"${sum(x['realized'] for x in sells):,.2f}")
    my_ws, total_value = build_my_portfolio(wb, universe_data, recommended_by_ticker,
                                            from_journal=has_journal)
    build_recommended_portfolio(wb, recommended_rows, is_real, month_tag)

    header_row_idx = {my_ws.cell(row=1, column=c).value: c for c in range(1, my_ws.max_column + 1)}
    my_holdings = {}
    for r in range(2, my_ws.max_row + 1):
        t = my_ws.cell(row=r, column=header_row_idx["Ticker"]).value
        if not (t and isinstance(t, str) and len(t) <= 10 and " " not in t):
            continue  # skip the note row (same column as Ticker) and any blanks
        t = t.upper()
        shares = _safe_float(my_ws.cell(row=r, column=header_row_idx["Shares Held"]).value) or 0
        avg_cost = _safe_float(my_ws.cell(row=r, column=header_row_idx["Average Cost"]).value) or 0
        my_holdings[t] = (shares, avg_cost)

    build_gap_analysis(wb, my_holdings, recommended_by_ticker, universe_data)
    _, opt_cost, opt_value = build_my_options(wb, universe_data, from_journal=has_journal)

    # Sheet order: My Portfolio, My Options, Recommended Portfolio, Portfolio vs Recommended
    order = ["My Portfolio", "My Options", "Recommended Portfolio", "Portfolio vs Recommended"]
    wb._sheets = [wb[n] for n in order if n in wb.sheetnames] + \
                 [s for s in wb._sheets if s.title not in order]

    wb.save(PORTFOLIO_FILE)
    mode = "REAL" if is_real else "SIMULATED"
    print(f"[SUCCESS] Wrote {PORTFOLIO_FILE} ({mode} recommended data) - "
          f"Total Portfolio Value: ${total_value:,.2f}")
    print(f"Options (My Options tab): cost ${opt_cost:,.2f}, value ${opt_value:,.2f}")
    print("Tabs: My Portfolio | My Options | Recommended Portfolio | Portfolio vs Recommended")


if __name__ == "__main__":
    main()
