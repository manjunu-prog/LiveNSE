"""
Merged Option-Chain Analyzer for Streamlit
- Auto-refresh every 30 seconds
- Quick buttons for Top 10 symbols
- Telegram-style formatted output
- Includes Delta OI, Decision Engine, Heavy Sellers/Buyers, Max Pain, PCR
- ZERO Telegram dependency
"""

import streamlit as st
import requests
import pandas as pd
from datetime import datetime

# ============================================================
# STREAMLIT CONFIG
# ============================================================
st.set_page_config(page_title="Option Chain Analyzer", layout="wide")
st.title("📡 Option Chain Analyzer — Telegram-style Output")

AUTO_REFRESH_RATE = 30  # seconds
st.experimental_autorefresh(interval=AUTO_REFRESH_RATE * 1000, key="auto_refresh")


# ============================================================
# HELPER FUNCTIONS
# ============================================================
def fmt_lakh(x):
    try:
        return f"{x/100000:.2f}L"
    except:
        return "0.00L"


def fmt_int(x):
    try:
        return f"{int(x):,}"
    except:
        return "0"


def signed_fmt(v):
    try:
        v = int(v)
    except:
        return "+0"
    return f"+{fmt_int(v)}" if v >= 0 else f"-{fmt_int(abs(v))}"


def signed_lakh(v):
    try:
        v = int(v)
    except:
        return "+0.00L"
    return f"+{fmt_lakh(v)}" if v >= 0 else f"-{fmt_lakh(abs(v))}"


def bar(value, max_value, length=18, color="green"):
    try:
        value = int(value)
    except:
        value = 0

    if max_value <= 0:
        filled = 0
    else:
        filled = int((value / max_value) * length)

    filled = max(0, min(length, filled))
    empty = length - filled

    visual = "█" * filled + "░" * empty
    prefix = "🟢 " if color == "green" else "🔴 "
    return prefix + visual


# ============================================================
# NSE FETCH
# ============================================================
def get_option_chain(symbol):
    if symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    else:
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.5",
        "Referer": "https://www.nseindia.com/option-chain"
    }

    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=headers, timeout=8)
        raw = session.get(url, headers=headers, timeout=8).json()
        records = raw.get("records", {}).get("data", [])
    except:
        return None, None

    rows = []
    for it in records:
        strike = it.get("strikePrice")
        ce = it.get("CE")
        pe = it.get("PE")

        rows.append({
            "Strike": strike,
            "CE_OI": int(ce.get("openInterest", 0)) if ce else 0,
            "PE_OI": int(pe.get("openInterest", 0)) if pe else 0,
            "CE_VOL": int(ce.get("totalTradedVolume", 0)) if ce else 0,
            "PE_VOL": int(pe.get("totalTradedVolume", 0)) if pe else 0,
        })

    df = pd.DataFrame(rows).dropna().sort_values("Strike")
    return df, raw


def fetch_spot(raw):
    try:
        return float(raw["records"]["underlyingValue"])
    except:
        return None


# ============================================================
# MAX PAIN
# ============================================================
def calc_max_pain(df):
    strikes = df["Strike"].values
    ce_oi = df.set_index("Strike")["CE_OI"].to_dict()
    pe_oi = df.set_index("Strike")["PE_OI"].to_dict()

    best_strike = None
    best_value = None

    for exp in strikes:
        total = 0
        for k in strikes:
            if exp > k:
                total += (exp - k) * ce_oi[k]
            if exp < k:
                total += (k - exp) * pe_oi[k]

        if best_value is None or total < best_value:
            best_value = total
            best_strike = exp

    return best_strike


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================
def support_resistance(df):
    support = df.sort_values("PE_OI", ascending=False).head(2)["Strike"].tolist()
    resistance = df.sort_values("CE_OI", ascending=False).head(2)["Strike"].tolist()

    while len(support) < 2:
        support.append(None)
    while len(resistance) < 2:
        resistance.append(None)

    return support, resistance


# ============================================================
# MAIN ANALYZER LOGIC
# ============================================================
def analyze(symbol="NIFTY"):
    df, raw = get_option_chain(symbol)
    if df is None or df.empty:
        return None

    spot = fetch_spot(raw)
    now = datetime.now().strftime("%H:%M:%S")

    ce_total = df["CE_OI"].sum()
    pe_total = df["PE_OI"].sum()
    ce_vol_total = df["CE_VOL"].sum()
    pe_vol_total = df["PE_VOL"].sum()

    # Previous snapshot
    prev_df = st.session_state.get("prev_df")
    prev_spot = st.session_state.get("prev_spot")
    prev_ce_total = st.session_state.get("prev_ce_total")
    prev_pe_total = st.session_state.get("prev_pe_total")
    prev_ce_vol = st.session_state.get("prev_ce_vol")
    prev_pe_vol = st.session_state.get("prev_pe_vol")

    # Delta values
    d_ce_oi = None if prev_ce_total is None else ce_total - prev_ce_total
    d_pe_oi = None if prev_pe_total is None else pe_total - prev_pe_total
    d_ce_vol = None if prev_ce_vol is None else ce_vol_total - prev_ce_vol
    d_pe_vol = None if prev_pe_vol is None else pe_vol_total - prev_pe_vol
    spot_move = None if prev_spot is None else round(spot - prev_spot, 2)

    # Levels
    max_pain = calc_max_pain(df)
    support, resistance = support_resistance(df)
    pcr = round(pe_total / ce_total, 2) if ce_total else 0

    seller_side = "CE" if ce_total > pe_total else "PE"
    seller_label = "CE Sellers" if seller_side == "CE" else "PE Sellers"

    buyer_side = "CE" if ce_vol_total > pe_vol_total else "PE"
    buyer_label = "CE Buyers" if buyer_side == "CE" else "PE Buyers"

    # Heavy sellers
    s_df = df.sort_values(f"{seller_side}_OI", ascending=False).head(4)
    max_s_val = s_df[f"{seller_side}_OI"].max()
    seller_lines = [
        f"{int(r['Strike'])}  {bar(r[f'{seller_side}_OI'], max_s_val, color='red')}  {signed_lakh(r[f'{seller_side}_OI'])}"
        for _, r in s_df.iterrows()
    ]

    # Heavy buyers
    b_df = df.sort_values(f"{buyer_side}_VOL", ascending=False).head(4)
    max_b_val = b_df[f"{buyer_side}_VOL"].max()
    buyer_lines = [
        f"{int(r['Strike'])}  {bar(r[f'{buyer_side}_VOL'], max_b_val, color='green')}  +{fmt_int(r[f'{buyer_side}_VOL'])}"
        for _, r in b_df.iterrows()
    ]

    # ΔOI Strikewise
    delta_block = ""
    if prev_df is not None:
        merged = df.set_index("Strike")[["CE_OI", "PE_OI"]].join(
            prev_df.set_index("Strike")[["CE_OI", "PE_OI"]],
            how="left", lsuffix="_now", rsuffix="_prev"
        ).fillna(0)

        merged["dCE"] = merged["CE_OI_now"] - merged["CE_OI_prev"]
        merged["dPE"] = merged["PE_OI_now"] - merged["PE_OI_prev"]
        merged = merged.reset_index()

        top = merged.sort_values(["dCE", "dPE"], ascending=False).head(5)

        delta_block = "Δ OI (Top Strikes):\n"
        for _, r in top.iterrows():
            delta_block += f"{int(r['Strike'])}:  ΔCE {signed_fmt(r['dCE'])} | ΔPE {signed_fmt(r['dPE'])}\n"
        delta_block += "\n"

    # Decision Engine
    decision = "NO CLEAR SIGNAL"
    confidence = 30
    reasons = []

    if d_ce_oi is not None and d_pe_oi is not None and spot_move is not None:

        if d_ce_oi > 0 and spot_move < 0:
            decision = "BUY PE"
            confidence = 85
            reasons.append("CE OI ↑ + Spot ↓ → Bearish")

        elif d_pe_oi > 0 and spot_move > 0:
            decision = "BUY CE"
            confidence = 85
            reasons.append("PE OI ↑ + Spot ↑ → Bullish")

        elif d_ce_oi < 0 and spot_move > 0:
            decision = "BUY CE"
            confidence = 70
            reasons.append("CE Unwinding + Spot ↑ → Bullish")

        elif d_pe_oi < 0 and spot_move < 0:
            decision = "BUY PE"
            confidence = 70
            reasons.append("PE Unwinding + Spot ↓ → Bearish")

    # Build Output Message
    message = (
        f"📊 {symbol} OI Summary — {now}\n"
        f"Spot: {spot} (Δ {spot_move if spot_move is not None else 0})\n\n"
        f"Total CE OI: {fmt_int(ce_total)} | Total PE OI: {fmt_int(pe_total)}\n"
        f"Δ CE OI: {signed_fmt(d_ce_oi or 0)} | Δ PE OI: {signed_fmt(d_pe_oi or 0)}\n"
        f"Total CE Vol: {fmt_int(ce_vol_total)} | Total PE Vol: {fmt_int(pe_vol_total)}\n"
        f"Δ CE Vol: {signed_fmt(d_ce_vol or 0)} | Δ PE Vol: {signed_fmt(d_pe_vol or 0)}\n\n"
        f"🔥 {seller_label}\n" + "\n".join(seller_lines) + "\n\n"
        f"🟢 {buyer_label}\n" + "\n".join(buyer_lines) + "\n\n"
        + delta_block +
        f"PCR: {pcr}\n"
        f"Max Pain: {max_pain}\n"
        f"Support: {support[0]}, {support[1]}\n"
        f"Resistance: {resistance[0]}, {resistance[1]}\n\n"
        f"🎯 Decision: {decision} (Confidence {confidence}%)\n"
        f"Reason: {' | '.join(reasons) if reasons else 'Not enough signals'}\n"
    )

    # Save state
    st.session_state["prev_df"] = df.copy()
    st.session_state["prev_spot"] = spot
    st.session_state["prev_ce_total"] = ce_total
    st.session_state["prev_pe_total"] = pe_total
    st.session_state["prev_ce_vol"] = ce_vol_total
    st.session_state["prev_pe_vol"] = pe_vol_total

    return message


# ============================================================
# UI — QUICK SYMBOL BUTTONS
# ============================================================
st.subheader("Quick Select Index / Stocks")

top_symbols = [
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN"
]

cols = st.columns(5)
clicked_symbol = None

for i, sym in enumerate(top_symbols):
    if cols[i % 5].button(sym):
        clicked_symbol = sym

if clicked_symbol:
    st.session_state["selected_symbol"] = clicked_symbol

symbol = st.session_state.get("selected_symbol", "NIFTY")
st.success(f"Analyzing: {symbol}")

st.markdown("---")

# ============================================================
# RUN ANALYSIS
# ============================================================
result_msg = analyze(symbol)

if result_msg is None:
    st.error("Error fetching data. Try again.")
else:
    st.code(result_msg, language=None)

# Manual Refresh
if st.button("🔄 Manual Refresh"):
    st.experimental_rerun()

# Reset Δ values
if st.button("🧹 Reset Δ History"):
    keys = ["prev_df", "prev_spot", "prev_ce_total", "prev_pe_total", "prev_ce_vol", "prev_pe_vol"]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]
    st.experimental_rerun()
