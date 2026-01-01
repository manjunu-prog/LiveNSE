# dhan_oc_app.py
import streamlit as st
import requests
import pandas as pd
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

# -----------------------
# CONFIG — replace these
# -----------------------
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3NjczMjk1MDUsImlhdCI6MTc2NzI0MzEwNSwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd3d3Lm5zZWluZGlhLmNvbS8iLCJkaGFuQ2xpZW50SWQiOiIxMTA4MTg5Mjc4In0.A0x6pKfzs6VJI7crRYyNVEcJ3R-Z-_hMcofUvLfYR_Z1XPqiqqZu9l6MIjbglUkzKqKjEM6i7mc4CKTyrGx7mw"   # put your JWT here
DHAN_CLIENT_ID = "1108189278"             # your client id / app name
TELEGRAM_BOT_TOKEN = "8243416633:AAFjISDBXvhqGsM8xvOkWOeQ4eEmhMPlkNU"
TELEGRAM_CHAT_ID = "567677761"
# -----------------------

API_BASE = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{API_BASE}/optionchain"
EXPIRY_LIST_URL = f"{API_BASE}/optionchain/expirylist"

# Map friendly names to Dhan underlying + segment
UNDERLYING_MAP = {
    "NIFTY": {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"},
    "BANKNIFTY": {"UnderlyingScrip": 26009, "UnderlyingSeg": "IDX_I"},
    "FINNIFTY": {"UnderlyingScrip": 26037, "UnderlyingSeg": "IDX_I"},
}

# -----------------------
# Utility functions
# -----------------------
def dh_get_expiries(underlying_scrip: int, underlying_seg: str) -> Optional[List[str]]:
    headers = {"access-token": DHAN_ACCESS_TOKEN, "client-id": DHAN_CLIENT_ID, "Content-Type": "application/json"}
    payload = {"UnderlyingScrip": underlying_scrip, "UnderlyingSeg": underlying_seg}
    try:
        r = requests.post(EXPIRY_LIST_URL, json=payload, headers=headers, timeout=12)
        if r.status_code != 200:
            st.error(f"Expiry list failed: {r.status_code} {r.text[:400]}")
            return None
        j = r.json()
        return j.get("data", [])
    except Exception as e:
        st.error(f"Expiry request error: {e}")
        return None

def pick_weekly_expiry(expiries: List[str]) -> Optional[str]:
    today = datetime.utcnow().date()
    candidates = []
    for e in expiries:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except:
            continue
        delta = (d - today).days
        if 0 <= delta <= 14:
            candidates.append((delta, d))
    if candidates:
        candidates.sort()
        return candidates[0][1].strftime("%Y-%m-%d")
    return expiries[0] if expiries else None

def dh_fetch_option_chain(underlying_scrip:int, underlying_seg:str, expiry:Optional[str]=None) -> Optional[Dict[str,Any]]:
    headers = {"access-token": DHAN_ACCESS_TOKEN, "client-id": DHAN_CLIENT_ID, "Content-Type": "application/json"}
    payload = {"UnderlyingScrip": underlying_scrip, "UnderlyingSeg": underlying_seg}
    if expiry:
        payload["Expiry"] = expiry
    try:
        r = requests.post(OPTIONCHAIN_URL, json=payload, headers=headers, timeout=20)
        if r.status_code != 200:
            st.error(f"OC Status: {r.status_code}\nRaw: {r.text[:600]}")
            return None
        return r.json()
    except Exception as e:
        st.error(f"Option chain request error: {e}")
        return None

def parse_oc_to_df(raw_json: Dict[str,Any]) -> pd.DataFrame:
    if not raw_json or "data" not in raw_json:
        return pd.DataFrame()
    data = raw_json["data"]
    oc = data.get("oc") or {}
    rows = []
    for strike_s, legs in oc.items():
        try:
            strike = float(strike_s)
        except:
            try:
                strike = float(strike_s.split(".")[0])
            except:
                continue
        ce = legs.get("ce") if isinstance(legs, dict) else None
        pe = legs.get("pe") if isinstance(legs, dict) else None

        for side_name, leg in (("CE", ce), ("PE", pe)):
            if not leg or not isinstance(leg, dict):
                continue
            oi = leg.get("oi", 0) or 0
            prev_oi = leg.get("previous_oi") if leg.get("previous_oi") is not None else leg.get("previousOi") if leg.get("previousOi") is not None else 0
            
            changeOi_field = leg.get("changeInOi") if leg.get("changeInOi") is not None else leg.get("changeOi") if leg.get("changeOi") is not None else None
            if changeOi_field is None:
                try:
                    changeOi = int(oi) - int(prev_oi or 0)
                except:
                    changeOi = 0
            else:
                changeOi = changeOi_field or 0

            rows.append({
                "strike": strike,
                "side": side_name,
                "last_price": leg.get("last_price") or leg.get("last_price") if leg.get("last_price") is not None else leg.get("ltp", 0),
                "oi": int(oi),
                "previous_oi": int(prev_oi or 0),
                "changeOi": int(changeOi),
                "iv": float(leg.get("implied_volatility") or leg.get("iv") or 0),
                "volume": int(leg.get("volume") or 0),
                "delta": leg.get("greeks", {}).get("delta") if isinstance(leg.get("greeks"), dict) else leg.get("delta"),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["strike", "side"], ascending=[True, True]).reset_index(drop=True)
    return df

def top_changes(df: pd.DataFrame, side: str, top_n: int = 5):
    sdf = df[df["side"] == side].copy()
    sdf_pos = sdf[sdf["changeOi"] > 0].sort_values("changeOi", ascending=False).head(top_n)
    sdf_neg = sdf[sdf["changeOi"] < 0].sort_values("changeOi").head(top_n)
    return sdf_pos, sdf_neg

def compute_summary(df: pd.DataFrame):
    total_ce_oi = int(df[df["side"]=="CE"]["oi"].sum())
    total_pe_oi = int(df[df["side"]=="PE"]["oi"].sum())
    total_ce_change_pos = int(df[(df["side"]=="CE") & (df["changeOi"]>0)]["changeOi"].sum())
    total_pe_change_pos = int(df[(df["side"]=="PE") & (df["changeOi"]>0)]["changeOi"].sum())
    total_ce_change_neg = int(df[(df["side"]=="CE") & (df["changeOi"]<0)]["changeOi"].sum())
    total_pe_change_neg = int(df[(df["side"]=="PE") & (df["changeOi"]<0)]["changeOi"].sum())

    pcr = (total_pe_oi / total_ce_oi) if total_ce_oi>0 else None
    dominant_buy = "CE" if total_ce_change_pos > total_pe_change_pos else "PE"
    dominant_sell = "CE" if abs(total_ce_change_neg) > abs(total_pe_change_neg) else "PE"

    return {
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "pcr": pcr,
        "total_ce_change_pos": total_ce_change_pos,
        "total_pe_change_pos": total_pe_change_pos,
        "total_ce_change_neg": total_ce_change_neg,
        "total_pe_change_neg": total_pe_change_neg,
        "dominant_buy": dominant_buy,
        "dominant_sell": dominant_sell
    }

def detect_trend_and_reversal(summary: Dict[str,Any], underlying_ltp: float):
    pcr = summary.get("pcr") or 0
    buy_ce = summary.get("total_ce_change_pos", 0)
    buy_pe = summary.get("total_pe_change_pos", 0)

    if buy_ce > buy_pe and pcr is not None and pcr < 0.95:
        trend = "Bullish"
    elif buy_pe > buy_ce and pcr is not None and pcr > 1.05:
        trend = "Bearish"
    else:
        trend = "Neutral"

    reversal = "No clear reversal signal"
    confidence = 20
    if pcr is not None:
        if pcr < 0.8 and buy_pe > buy_ce * 1.5 and buy_pe > 10000:
            reversal = "Bearish reversal possible (PE buying increasing)"
            confidence = 45
        elif pcr > 1.2 and buy_ce > buy_pe * 1.5 and buy_ce > 10000:
            reversal = "Bullish reversal possible (CE buying increasing)"
            confidence = 45
    return trend, reversal, confidence

def format_counts(num:int):
    return f"{num:,}"

def build_telegram_message(expiry, ltp, atm, summary, top_buy_side_rows:List[Dict], top_sell_side_rows:List[Dict], trend, reversal, conf):
    lines = []
    lines.append(f"🔥 Option Chain Snapshot — Expiry {expiry}")
    lines.append(f"Underlying LTP: {int(ltp)}")
    lines.append(f"ATM approx: {atm}")
    lines.append("") 
    lines.append("🔥 Heavy Buys (top 5)")
    dom = summary["dominant_buy"]
    lines.append(f"🟢 {dom} Buyers:")
    for r in top_buy_side_rows:
        lines.append(f"{int(r['strike'])}  +{format_counts(int(r['changeOi']))}")
    lines.append("")
    lines.append("🔴 Heavy Sells (top 5)")
    doms = summary["dominant_sell"]
    lines.append(f"🔴 {doms} Sellers:")
    for r in top_sell_side_rows:
        lines.append(f"{int(r['strike'])}  {format_counts(int(r['changeOi']))}")
    lines.append("")
    lines.append(f"PCR: {summary['pcr']:.2f}" if summary['pcr'] else "PCR: N/A")
    lines.append(f"Market Trend (basic): {trend}")
    lines.append(f"Reversal: {reversal} | Confidence {conf}%")
    return "\n".join(lines)

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Dhan Option Chain Analyzer", layout="wide")
st.title("Dhan Option Chain Analyzer — Fixed")

# --- INITIALIZE SESSION STATE ---
if "data_fetched" not in st.session_state:
    st.session_state["data_fetched"] = False
if "telegram_msg" not in st.session_state:
    st.session_state["telegram_msg"] = ""

with st.sidebar:
    st.header("Config")
    underlying_choice = st.selectbox("Underlying", list(UNDERLYING_MAP.keys()), index=0)
    weekly_only = st.checkbox("Weekly expiry only", value=True)
    top_n = st.number_input("Top strikes to show", min_value=1, max_value=10, value=5)
    st.markdown("Press **Fetch Option Chain** to load data.")

    if st.button("Fetch Option Chain"):
        st.session_state["data_fetched"] = False # Reset
        
        cfg = UNDERLYING_MAP.get(underlying_choice)
        expiries = dh_get_expiries(cfg["UnderlyingScrip"], cfg["UnderlyingSeg"])
        
        if expiries:
            expiry_to_use = pick_weekly_expiry(expiries) if weekly_only else expiries[0]
            st.session_state["expiry"] = expiry_to_use
            
            oc_raw = dh_fetch_option_chain(cfg["UnderlyingScrip"], cfg["UnderlyingSeg"], expiry_to_use)
            if oc_raw:
                df = parse_oc_to_df(oc_raw)
                if not df.empty:
                    summary = compute_summary(df)
                    ltp = oc_raw.get("data", {}).get("last_price") or oc_raw.get("data", {}).get("lastPrice") or 0
                    atm = round(float(ltp)/50)*50 if ltp else df["strike"].iloc[len(df)//2]
                    trend, reversal, conf = detect_trend_and_reversal(summary, ltp)

                    dom_buy = summary["dominant_buy"]
                    dom_sell = summary["dominant_sell"]
                    pos_ce, neg_ce = top_changes(df, "CE", top_n)
                    pos_pe, neg_pe = top_changes(df, "PE", top_n)

                    top_buy_rows = pos_ce.to_dict("records") if dom_buy == "CE" else pos_pe.to_dict("records")
                    top_sell_rows = neg_ce.to_dict("records") if dom_sell == "CE" else neg_pe.to_dict("records")

                    # Generate Message immediately
                    msg = build_telegram_message(
                        expiry_to_use, ltp, atm, summary,
                        top_buy_side_rows=top_buy_rows[:top_n],
                        top_sell_side_rows=top_sell_rows[:top_n],
                        trend=trend, reversal=reversal, conf=conf
                    )

                    # Store everything in session state
                    st.session_state["data_fetched"] = True
                    st.session_state["df"] = df
                    st.session_state["summary"] = summary
                    st.session_state["ltp"] = ltp
                    st.session_state["atm"] = atm
                    st.session_state["trend"] = trend
                    st.session_state["reversal"] = reversal
                    st.session_state["conf"] = conf
                    st.session_state["telegram_msg"] = msg
                    st.session_state["top_buy_rows"] = top_buy_rows
                    st.session_state["top_sell_rows"] = top_sell_rows
                    st.rerun() # Force refresh to show data

# --- MAIN DISPLAY AREA ---
if st.session_state["data_fetched"]:
    col1, col2 = st.columns([2, 1])
    
    with col1:
        st.subheader(f"Option Chain — Expiry {st.session_state['expiry']}")
        st.markdown(f"**Underlying LTP:** {st.session_state['ltp']} | **ATM:** {st.session_state['atm']}")
        
        # Display Stats
        st.write("### Summary Stats")
        summary = st.session_state["summary"]
        st.json({
            "PCR": f"{summary['pcr']:.2f}" if summary["pcr"] else "N/A",
            "Trend": st.session_state["trend"],
            "Dominant Buy": summary["dominant_buy"],
            "Dominant Sell": summary["dominant_sell"]
        })

        st.write("### Top Buys")
        if st.session_state["top_buy_rows"]:
            st.dataframe(pd.DataFrame(st.session_state["top_buy_rows"])[["strike","changeOi","oi","iv"]])

        st.write("### Top Sells")
        if st.session_state["top_sell_rows"]:
            st.dataframe(pd.DataFrame(st.session_state["top_sell_rows"])[["strike","changeOi","oi","iv"]])

    with col2:
        st.info("### Telegram Action")
        st.text_area("Preview Message", st.session_state["telegram_msg"], height=300)
        
        if st.button("📩 Send Telegram Message Now"):
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": st.session_state["telegram_msg"]
            }
            try:
                r = requests.post(url, data=payload)
                if r.status_code == 200:
                    st.success("✅ Message sent to Telegram!")
                else:
                    st.error(f"❌ Telegram error: {r.text}")
            except Exception as e:
                st.error(f"❌ Failed to send: {e}")

else:
    st.info("👈 Please click 'Fetch Option Chain' in the sidebar to start.")
