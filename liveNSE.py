"""
Merged Option-Chain Analyzer for Streamlit
- Combines logic from two provided scripts
- Keeps Decision Engine, Δ OI/Volume, Max Pain, Support/Resistance, Heavy Buyers/Sellers
- Removes Telegram sending entirely
- Shows a Telegram-style formatted message inside Streamlit when you press "Run Analysis"
- Keeps the bar visuals (█ and ░) and emojis exactly as requested
- NEW FEATURE ADDED: A button to send the output to Telegram manually
"""

import streamlit as st
import requests
import pandas as pd
from datetime import datetime

# ---------------------------
# 🔥 NEW — Fill this to enable Telegram sending
# ---------------------------
BOT_TOKEN = "8243416633:AAFjISDBXvhqGsM8xvOkWOeQ4eEmhMPlkNU"
CHAT_ID = "567677761"


st.set_page_config(page_title="Option Chain Analyzer (Telegram-style output)", layout="wide")
st.title("📡 Option Chain Analyzer — Telegram-style Output (Streamlit)")

# ---------------------------
# Helper formatting functions
# ---------------------------
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
    if v >= 0:
        return f"+{fmt_int(v)}"
    else:
        return f"-{fmt_int(abs(v))}"

def signed_lakh(v):
    try:
        v = int(v)
    except:
        return "+0.00L"
    if v >= 0:
        return f"+{fmt_lakh(v)}"
    else:
        return f"-{fmt_lakh(abs(v))}"

def signed_fmt_role(value, role):
    try:
        value = int(value)
    except:
        value = 0
    if role == "seller":
        return f"-{fmt_lakh(value)}"
    else:
        return f"+{fmt_int(value)}"

def bar(value, max_value, length=20, color="green"):
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
    block = "█"
    hollow = "░"
    visual = block * filled + hollow * empty
    prefix = "🟢 " if color == "green" else "🔴 "
    return prefix + visual

# ---------------------------
# Fetch option chain from NSE
# ---------------------------
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
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        raw = session.get(url, headers=headers, timeout=10).json()
        records = raw.get("records", {}).get("data", [])
    except Exception as e:
        st.error(f"NSE fetch error: {e}")
        return None, None

    rows = []
    for it in records:
        strike = it.get("strikePrice")
        ce = it.get("CE")
        pe = it.get("PE")

        ce_oi = int(ce.get("openInterest", 0)) if ce else 0
        pe_oi = int(pe.get("openInterest", 0)) if pe else 0
        ce_vol = int(ce.get("totalTradedVolume", 0)) if ce else 0
        pe_vol = int(pe.get("totalTradedVolume", 0)) if pe else 0

        rows.append({
            "Strike": strike,
            "CE_OI": ce_oi,
            "PE_OI": pe_oi,
            "CE_VOL": ce_vol,
            "PE_VOL": pe_vol,
            "TOTAL_OI": ce_oi + pe_oi
        })

    if not rows:
        return None, raw

    df = pd.DataFrame(rows).dropna().sort_values("Strike").reset_index(drop=True)
    return df, raw

# ---------------------------
# Extract spot
# ---------------------------
def fetch_spot_from_nse(raw):
    try:
        return float(raw["records"]["underlyingValue"])
    except:
        return None

# ---------------------------
# Max pain
# ---------------------------
def calc_max_pain(df):
    if df is None or df.empty:
        return None
    strikes = df["Strike"].values
    ce_oi = df.set_index("Strike")["CE_OI"].to_dict()
    pe_oi = df.set_index("Strike")["PE_OI"].to_dict()

    best = None
    best_val = None

    for exp in strikes:
        total = 0
        for k in strikes:
            if exp > k:
                total += (exp - k) * ce_oi[k]
            if exp < k:
                total += (k - exp) * pe_oi[k]
        if best_val is None or total < best_val:
            best_val = total
            best = exp

    return int(best) if best is not None else None

# ---------------------------
# Support & resistance
# ---------------------------
def support_resistance(df):
    if df is None or df.empty:
        return [], []
    supports = df.sort_values("PE_OI", ascending=False).head(2)["Strike"].tolist()
    resists = df.sort_values("CE_OI", ascending=False).head(2)["Strike"].tolist()
    while len(supports) < 2:
        supports.append(None)
    while len(resists) < 2:
        resists.append(None)
    return supports, resists

# ---------------------------
# Analysis function
# ---------------------------
def analyze(symbol="NIFTY"):
    df, raw = get_option_chain(symbol)
    if df is None or df.empty:
        return None, "ERROR: Failed to fetch option chain."

    spot = fetch_spot_from_nse(raw)
    now = datetime.now().strftime("%H:%M:%S")

    # totals
    ce_total = df["CE_OI"].sum()
    pe_total = df["PE_OI"].sum()
    ce_vol_total = df["CE_VOL"].sum()
    pe_vol_total = df["PE_VOL"].sum()

    prev_df = st.session_state.get("prev_df", None)
    prev_spot = st.session_state.get("prev_spot", None)
    prev_ce_total = st.session_state.get("prev_ce_total", None)
    prev_pe_total = st.session_state.get("prev_pe_total", None)
    prev_ce_vol = st.session_state.get("prev_ce_vol", None)
    prev_pe_vol = st.session_state.get("prev_pe_vol", None)
    previous_bias = st.session_state.get("previous_bias", None)

    d_ce_oi = None if prev_ce_total is None else ce_total - prev_ce_total
    d_pe_oi = None if prev_pe_total is None else pe_total - prev_pe_total
    d_ce_vol = None if prev_ce_vol is None else ce_vol_total - prev_ce_vol
    d_pe_vol = None if prev_pe_vol is None else pe_vol_total - prev_pe_vol
    spot_move = None if prev_spot is None else round(spot - prev_spot, 2)

    max_pain = calc_max_pain(df)
    supports, resists = support_resistance(df)
    pcr = round(pe_total / ce_total, 2) if ce_total else 0

    seller_side = "CE" if ce_total > pe_total else "PE"
    seller_label = "CE Sellers" if seller_side == "CE" else "PE Sellers"

    buyer_side = "CE" if ce_vol_total > pe_vol_total else "PE"
    buyer_label = "CE Buyers" if buyer_side == "CE" else "PE Buyers"

    s_df = df.sort_values(f"{seller_side}_OI", ascending=False).head(4)
    max_s_val = s_df[f"{seller_side}_OI"].max() if not s_df.empty else 0
    seller_lines = []
    for _, r in s_df.iterrows():
        strike = int(r["Strike"])
        oi_val = int(r[f"{seller_side}_OI"])
        seller_lines.append(f"{strike}  {bar(oi_val, max_s_val, color='red')}  {signed_lakh(oi_val)}")
    if not seller_lines:
        seller_lines = ["No heavy sellers data"]

    b_df = df.sort_values(f"{buyer_side}_VOL", ascending=False).head(4)
    max_b_val = b_df[f"{buyer_side}_VOL"].max() if not b_df.empty else 0
    buyer_lines = []
    for _, r in b_df.iterrows():
        strike = int(r["Strike"])
        vol_val = int(r[f"{buyer_side}_VOL"])
        buyer_lines.append(f"{strike}  {bar(vol_val, max_b_val, color='green')}  +{fmt_int(vol_val)}")
    if not buyer_lines:
        buyer_lines = ["No heavy buyers data"]

    delta_block = ""
    if prev_df is not None:
        merged = df.set_index("Strike")[["CE_OI", "PE_OI"]].join(
            prev_df.set_index("Strike")[["CE_OI", "PE_OI"]],
            how="left", lsuffix="_now", rsuffix="_prev"
        ).fillna(0)
        merged["dCE"] = merged["CE_OI_now"] - merged["CE_OI_prev"]
        merged["dPE"] = merged["PE_OI_now"] - merged["PE_OI_prev"]
        merged_reset = merged.reset_index()

        top = merged_reset.sort_values(["dCE", "dPE"], ascending=False).head(5)
        delta_block = "Δ OI (Top strikes):\n"
        for _, r in top.iterrows():
            delta_block += f"{int(r['Strike'])}:  ΔCE {signed_fmt(int(r['dCE']))}  |  ΔPE {signed_fmt(int(r['dPE']))}\n"
        delta_block += "\n"

    decision = "NO CLEAR SIGNAL"
    confidence = 30
    reasons = []

    if d_ce_oi is not None and d_pe_oi is not None and spot_move is not None:
        if d_ce_oi > 0 and spot_move < 0:
            decision = "BUY PE"
            confidence = 85
            reasons.append("CE O OI increased + Spot falling → Bearish")
        elif d_pe_oi > 0 and spot_move > 0:
            decision = "BUY CE"
            confidence = 85
            reasons.append("PE OI increased + Spot rising → Bullish")
        elif d_ce_oi < 0 and spot_move > 0:
            decision = "BUY CE"
            confidence = 70
            reasons.append("CE OI dropped → Bullish")
        elif d_pe_oi < 0 and spot_move < 0:
            decision = "BUY PE"
            confidence = 70
            reasons.append("PE OI dropped → Bearish")

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
        f"P C R: {pcr}\nMax Pain: {max_pain}\n"
        f"Support: {supports[0]}, {supports[1]}\n"
        f"Resistance: {resists[0]}, {resists[1]}\n\n"
        f"🎯 Decision: {decision} (Confidence {confidence}%)\n"
        f"Reason: {' | '.join(reasons) if reasons else 'Not enough strong signals'}\n"
    )

    curr_bias = "CE" if ce_total > pe_total else "PE"
    trend_change_msg = None
    if previous_bias is not None and curr_bias != previous_bias:
        trend_change_msg = f"⚠️ Writer Trend Change: Prev={previous_bias}, Now={curr_bias}"

    st.session_state["prev_df"] = df.copy()
    st.session_state["prev_spot"] = spot
    st.session_state["prev_ce_total"] = ce_total
    st.session_state["prev_pe_total"] = pe_total
    st.session_state["prev_ce_vol"] = ce_vol_total
    st.session_state["prev_pe_vol"] = pe_vol_total
    st.session_state["previous_bias"] = curr_bias

    return message, trend_change_msg

# ---------------------------
# Streamlit UI
# ---------------------------
col1, col2 = st.columns([3, 1])
with col1:
    symbol = st.text_input("Symbol (e.g. NIFTY, BANKNIFTY, INFY)", value="NIFTY").strip().upper()
with col2:
    run_btn = st.button("Run Analysis")

st.markdown("---")

if "last_message" not in st.session_state:
    st.session_state["last_message"] = ""

if run_btn:
    with st.spinner("Fetching option chain and analyzing..."):
        msg, trend_msg = analyze(symbol=symbol)
        if msg is None:
            st.error("Analysis failed.")
        else:
            st.session_state["last_message"] = msg
            st.session_state["trend_message"] = trend_msg or ""

# Display final output
if st.session_state.get("trend_message"):
    st.info(st.session_state["trend_message"])

if st.session_state.get("last_message"):
    st.code(st.session_state["last_message"], language=None)
else:
    st.info("Press **Run Analysis** to fetch data and show Telegram-style output here.")

# ---------------------------
# ✅ NEW FEATURE — SEND TO TELEGRAM BUTTON
# ---------------------------
if st.session_state.get("last_message"):
    if st.button("📩 Send to Telegram"):
        if BOT_TOKEN == "PUT_YOUR_TOKEN_HERE":
            st.error("❌ Please enter your BOT_TOKEN in the code first.")
        elif CHAT_ID == "PUT_YOUR_CHAT_ID_HERE":
            st.error("❌ Please enter your CHAT_ID in the code first.")
        else:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": CHAT_ID, "text": st.session_state["last_message"]}

            try:
                r = requests.post(url, data=payload)
                if r.status_code == 200:
                    st.success("✅ Sent to Telegram!")
                else:
                    st.error(f"Telegram Error: {r.text}")
            except Exception as e:
                st.error(f"Telegram send failed: {e}")

# Reset button
if st.button("Reset previous snapshot (clear Δ calculations)"):
    keys = ["prev_df", "prev_spot", "prev_ce_total", "prev_pe_total",
            "prev_ce_vol", "prev_pe_vol", "previous_bias",
            "last_message", "trend_message"]
    for k in keys:
        if k in st.session_state:
            del st.session_state[k]
    st.experimental_rerun()
