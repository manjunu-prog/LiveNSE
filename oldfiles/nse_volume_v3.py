# nse_volume_candle_tracker.py
# NSE Options — OTM Strike Grid  |  2-min Volume + OI Change candles from 9:15
# Run: streamlit run nse_volume_candle_tracker.py

import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
import numpy as np

# -----------------------
# CONFIG
# -----------------------
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3NzY0MDAzNDcsImlhdCI6MTc3NjMxMzk0NywidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd2ViLmRoYW4uY28vaW5kZXgvcHJvZmlsZSIsImRoYW5DbGllbnRJZCI6IjExMDgwNjYwOTQifQ.Hc4X4eg1idBPZ7yREyCSlndHtlb8UZNjiRKBKTtHEBp4t61JAWIGZifY86OGSRUMA1Pkd0bSFQPlTtOsC0y_Lg"
DHAN_CLIENT_ID  = "1108066094"

API_BASE        = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{API_BASE}/optionchain"
EXPIRY_LIST_URL = f"{API_BASE}/optionchain/expirylist"

UNDERLYING_MAP = {
    "NIFTY":     {"UnderlyingScrip": 13,    "UnderlyingSeg": "IDX_I", "step": 50},
    "BANKNIFTY": {"UnderlyingScrip": 26009, "UnderlyingSeg": "IDX_I", "step": 100},
    "FINNIFTY":  {"UnderlyingScrip": 26037, "UnderlyingSeg": "IDX_I", "step": 50},
}

POLL_INTERVAL_SECS = 120   # 2 minutes
MARKET_OPEN        = "09:15"

# -----------------------
# Helpers
# -----------------------
def _headers():
    return {
        "access-token": DHAN_ACCESS_TOKEN,
        "client-id": DHAN_CLIENT_ID,
        "Content-Type": "application/json",
    }

def get_expiries(scrip, seg):
    try:
        r = requests.post(EXPIRY_LIST_URL,
                          json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg},
                          headers=_headers(), timeout=12)
        return r.json().get("data", []) if r.status_code == 200 else []
    except Exception as e:
        st.error(f"Expiry fetch error: {e}")
        return []

def pick_nearest_expiry(expiries):
    today = datetime.utcnow().date()
    best, best_delta = None, 9999
    for e in expiries:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
            delta = (d - today).days
            if 0 <= delta < best_delta:
                best_delta = delta
                best = e
        except Exception:
            continue
    return best or (expiries[0] if expiries else None)

def fetch_option_chain(scrip, seg, expiry):
    try:
        r = requests.post(OPTIONCHAIN_URL,
                          json={"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry},
                          headers=_headers(), timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        st.error(f"OC fetch error: {e}")
        return None

def parse_oc(raw):
    oc  = raw.get("data", {}).get("oc", {})
    ltp = float(raw.get("data", {}).get("last_price")
                or raw.get("data", {}).get("lastPrice") or 0)
    rows = []
    for strike_s, legs in oc.items():
        try:
            strike = float(strike_s)
        except Exception:
            continue
        for side in ("CE", "PE"):
            leg = legs.get(side.lower()) if isinstance(legs, dict) else None
            if not isinstance(leg, dict):
                continue
            prev_oi = int(leg.get("previous_oi") or leg.get("previousOi") or 0)
            oi      = int(leg.get("oi") or 0)
            rows.append({
                "strike":    strike,
                "side":      side,
                "volume":    int(leg.get("volume") or 0),
                "oi":        oi,
                "prev_oi":   prev_oi,
                "oi_change": oi - prev_oi,
                "ltp":       float(leg.get("last_price") or leg.get("ltp") or 0),
                "iv":        float(leg.get("implied_volatility") or leg.get("iv") or 0),
            })
    return rows, ltp

def compute_vol_delta(rows, prev_vol):
    result = []
    for r in rows:
        key   = f"{r['strike']}_{r['side']}"
        prev  = prev_vol.get(key, r["volume"])
        delta = max(0, r["volume"] - prev)
        prev_vol[key] = r["volume"]
        result.append({**r, "vol_delta": delta})
    return result

def get_otm_strikes(all_strikes, atm, n, side):
    s = sorted(set(all_strikes))
    if side == "CE":
        return [x for x in s if x > atm][:n]
    else:
        otm = [x for x in reversed(s) if x < atm][:n]
        return sorted(otm)

def fmt(v):
    av = abs(v)
    if av >= 100_000: return f"{v/100_000:.1f}L"
    if av >= 1_000:   return f"{v/1_000:.1f}K"
    return str(v)

# -----------------------
# Mini chart per strike
# -----------------------
def build_strike_mini_chart(history: List[Dict], strike: float, side: str) -> go.Figure:
    """
    Single mini chart for one strike:
    - Pink bars  = volume per 2-min candle
    - Teal bars  = OI change per 2-min candle  (can be negative → below zero line)
    - Dark line  = cumulative volume trend
    x-axis = candle times (09:15 → now), full day view
    """
    times    = [c["ts"] for c in history]
    vol_data = []
    oi_data  = []

    for candle in history:
        row = next((r for r in candle["rows"]
                    if r["strike"] == strike and r["side"] == side), None)
        vol_data.append(row["vol_delta"]  if row else 0)
        oi_data.append(row["oi_change"]   if row else 0)

    # cumulative vol for trend line
    cumvol = list(np.cumsum(vol_data))
    # scale trend to sit nicely over vol bars
    max_vol = max(vol_data) if any(v > 0 for v in vol_data) else 1
    max_cum = max(cumvol)   if cumvol and max(cumvol) > 0 else 1
    trend_scaled = [v * (max_vol / max_cum) for v in cumvol]

    fig = go.Figure()

    # Volume bars — pink/salmon
    fig.add_trace(go.Bar(
        x=times, y=vol_data,
        name="Vol",
        marker_color="#e88080",
        opacity=0.9,
        showlegend=False,
        hovertemplate="%{x}<br>Vol: %{y:,}<extra></extra>",
    ))

    # OI change bars — teal/green (positive) and light red (negative)
    oi_colors = ["#5ec4a8" if v >= 0 else "#f0aaaa" for v in oi_data]
    fig.add_trace(go.Bar(
        x=times, y=oi_data,
        name="OI Chg",
        marker_color=oi_colors,
        opacity=0.85,
        showlegend=False,
        hovertemplate="%{x}<br>OI Chg: %{y:,}<extra></extra>",
        yaxis="y2",
    ))

    # Trend line — cumulative volume (scaled)
    fig.add_trace(go.Scatter(
        x=times, y=trend_scaled,
        mode="lines",
        line=dict(color="#5555aa", width=1.2, dash="solid"),
        name="Cum Vol",
        showlegend=False,
        hovertemplate="%{x}<br>Cum Vol: %{customdata:,}<extra></extra>",
        customdata=cumvol,
    ))

    color = "#c83232" if side == "PE" else "#2255aa"
    fig.update_layout(
        title=dict(
            text=f"<b>{int(strike)} {side}</b>",
            font=dict(size=12, color=color),
            x=0, xanchor="left",
            pad=dict(l=4, t=2),
        ),
        barmode="overlay",
        height=200,
        margin=dict(l=30, r=6, t=28, b=28),
        xaxis=dict(
            tickfont=dict(size=8),
            tickangle=-45,
            showgrid=False,
            # ensure x-axis always starts from 09:15
            range=[MARKET_OPEN, times[-1]] if times else None,
        ),
        yaxis=dict(
            tickfont=dict(size=8),
            showgrid=True,
            gridcolor="rgba(180,180,180,0.2)",
            title=dict(text="Vol", font=dict(size=8)),
            zeroline=True,
            zerolinecolor="rgba(128,128,128,0.3)",
        ),
        yaxis2=dict(
            overlaying="y",
            side="right",
            tickfont=dict(size=7),
            showgrid=False,
            title=dict(text="OI Δ", font=dict(size=7)),
            zeroline=True,
            zerolinecolor="rgba(128,128,128,0.4)",
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )

    # Annotation: latest values top-left
    latest_vol = vol_data[-1] if vol_data else 0
    latest_oi  = oi_data[-1]  if oi_data  else 0
    fig.add_annotation(
        text=f"V:{fmt(latest_vol)}  OI:{fmt(latest_oi)}",
        xref="paper", yref="paper",
        x=0.01, y=0.99,
        showarrow=False,
        font=dict(size=9, color="#444444"),
        align="left",
        bgcolor="rgba(255,255,255,0.6)",
        borderpad=2,
    )

    return fig

# -----------------------
# Streamlit App
# -----------------------
st.set_page_config(page_title="NSE OTM Strike Grid", layout="wide")

for key, default in [
    ("candle_history", []),
    ("prev_vol",       {}),
    ("expiry",         None),
    ("tracking",       False),
    ("last_ltp",       0),
    ("atm",            0),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ---- Sidebar ----
with st.sidebar:
    st.header("Settings")
    underlying = st.selectbox("Underlying", list(UNDERLYING_MAP.keys()))
    cfg  = UNDERLYING_MAP[underlying]
    step = cfg["step"]
    otm_n = st.slider("OTM strikes per side", 3, 8, 5)

    st.divider()
    col_a, col_b = st.columns(2)
    if col_a.button("▶ Start", type="primary", use_container_width=True):
        st.session_state["tracking"] = True
    if col_b.button("⏹ Stop", use_container_width=True):
        st.session_state["tracking"] = False
    if st.button("🗑 Clear & Restart", use_container_width=True):
        st.session_state.update({
            "candle_history": [], "prev_vol": {},
            "expiry": None, "last_ltp": 0, "atm": 0,
        })
        st.rerun()

    st.divider()
    st.caption(f"Poll every {POLL_INTERVAL_SECS // 60} min")
    if st.session_state["expiry"]:
        st.success(f"Expiry: {st.session_state['expiry']}")
    st.caption("🟢 Live" if st.session_state["tracking"] else "🔴 Stopped")
    st.caption(f"Candles: {len(st.session_state['candle_history'])}")

    st.divider()
    st.markdown("""
**Legend**
- 🩷 Pink bars = Volume (2-min candle)
- 🩵 Teal bars = OI Change
- 🟣 Line = Cumulative volume trend
    """)

# ---- Fetch ----
if st.session_state["tracking"]:
    if not st.session_state["expiry"]:
        expiries = get_expiries(cfg["UnderlyingScrip"], cfg["UnderlyingSeg"])
        st.session_state["expiry"] = pick_nearest_expiry(expiries)

    raw = fetch_option_chain(cfg["UnderlyingScrip"], cfg["UnderlyingSeg"],
                             st.session_state["expiry"])
    if raw:
        rows, ltp  = parse_oc(raw)
        rows_delta = compute_vol_delta(rows, st.session_state["prev_vol"])
        atm = round(ltp / step) * step
        ts  = datetime.now().strftime("%H:%M")

        # Only store if new timestamp (avoid duplicates on rerun)
        existing_ts = [c["ts"] for c in st.session_state["candle_history"]]
        if ts not in existing_ts:
            st.session_state["candle_history"].append({"ts": ts, "rows": rows_delta, "ltp": ltp})
        st.session_state["last_ltp"] = ltp
        st.session_state["atm"]      = atm

    st.markdown(f'<meta http-equiv="refresh" content="{POLL_INTERVAL_SECS}">',
                unsafe_allow_html=True)

# ---- Guard ----
history = st.session_state["candle_history"]
if not history:
    st.title("NSE OTM Strike Grid — 2-min Candles")
    st.info("👈 Click **▶ Start** in the sidebar. Charts build up from 9:15 as each 2-min candle comes in.")
    st.stop()

latest_rows  = history[-1]["rows"]
ltp          = st.session_state["last_ltp"]
atm          = st.session_state["atm"]
all_strikes  = sorted({r["strike"] for r in latest_rows})
last_ts      = history[-1]["ts"]

# ---- Header metrics ----
ce_oi  = sum(r["oi"]        for r in latest_rows if r["side"] == "CE")
pe_oi  = sum(r["oi"]        for r in latest_rows if r["side"] == "PE")
pcr    = pe_oi / ce_oi if ce_oi else 0
ce_oic = sum(r["oi_change"] for r in latest_rows if r["side"] == "CE")
pe_oic = sum(r["oi_change"] for r in latest_rows if r["side"] == "PE")

st.title(f"NSE {underlying}  |  ATM {int(atm)}  |  LTP {ltp:,.0f}  |  {last_ts}")

m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
m1.metric("LTP",        f"{ltp:,.0f}")
m2.metric("ATM",        f"{int(atm)}")
m3.metric("PCR",        f"{pcr:.2f}",
          delta="Bullish" if pcr > 1.05 else ("Bearish" if pcr < 0.95 else "Neutral"))
m4.metric("CE OI",      f"{ce_oi/1e5:.1f}L")
m5.metric("PE OI",      f"{pe_oi/1e5:.1f}L")
m6.metric("CE OI Chg",  fmt(ce_oic))
m7.metric("PE OI Chg",  fmt(pe_oic))
st.caption(
    f"Candles: {len(history)}  |  From {history[0]['ts']} → {last_ts}  |  "
    f"Expiry: {st.session_state['expiry']}  |  "
    f"Pink=Vol  Teal=OI Change  Purple line=Cum Vol"
)
st.divider()

# ---- OTM strike lists ----
ce_strikes = get_otm_strikes(all_strikes, atm, otm_n, "CE")
pe_strikes = get_otm_strikes(all_strikes, atm, otm_n, "PE")

# ============================================================
# GRID — CE row  (all CE strikes side by side)
# ============================================================
st.markdown(f"### 🔵 CE OTM Strikes — Vol + OI Change (each bar = 1 candle × 2 min)")
ce_cols = st.columns(len(ce_strikes))
for col, strike in zip(ce_cols, ce_strikes):
    with col:
        fig = build_strike_mini_chart(history, strike, "CE")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        # compact data pill below chart
        latest = next((r for r in latest_rows if r["strike"] == strike and r["side"] == "CE"), {})
        st.markdown(
            f"<div style='font-size:11px;text-align:center;color:#555'>"
            f"Vol: <b>{fmt(latest.get('vol_delta',0))}</b>  "
            f"OI: <b>{fmt(latest.get('oi',0))}</b><br>"
            f"OI Δ: <b>{fmt(latest.get('oi_change',0))}</b>  "
            f"LTP: <b>{latest.get('ltp',0):.1f}</b></div>",
            unsafe_allow_html=True,
        )

st.divider()

# ============================================================
# GRID — PE row  (all PE strikes side by side)
# ============================================================
st.markdown(f"### 🔴 PE OTM Strikes — Vol + OI Change (each bar = 1 candle × 2 min)")
pe_cols = st.columns(len(pe_strikes))
for col, strike in zip(pe_cols, pe_strikes):
    with col:
        fig = build_strike_mini_chart(history, strike, "PE")
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        latest = next((r for r in latest_rows if r["strike"] == strike and r["side"] == "PE"), {})
        st.markdown(
            f"<div style='font-size:11px;text-align:center;color:#555'>"
            f"Vol: <b>{fmt(latest.get('vol_delta',0))}</b>  "
            f"OI: <b>{fmt(latest.get('oi',0))}</b><br>"
            f"OI Δ: <b>{fmt(latest.get('oi_change',0))}</b>  "
            f"LTP: <b>{latest.get('ltp',0):.1f}</b></div>",
            unsafe_allow_html=True,
        )

st.divider()

# ============================================================
# Detailed data table — expandable
# ============================================================
with st.expander("📋 Full data table — latest snapshot"):
    all_otm = [(s, "CE") for s in ce_strikes] + [(s, "PE") for s in pe_strikes]
    table_rows = []
    for strike, side in all_otm:
        r = next((x for x in latest_rows if x["strike"] == strike and x["side"] == side), {})
        table_rows.append({
            "Strike": int(strike),
            "Side":   side,
            "Vol (candle)": r.get("vol_delta", 0),
            "Total Vol":    r.get("volume", 0),
            "OI":           r.get("oi", 0),
            "OI Change":    r.get("oi_change", 0),
            "Prev OI":      r.get("prev_oi", 0),
            "LTP":          round(r.get("ltp", 0), 2),
            "IV %":         round(r.get("iv", 0), 2),
        })
    df = pd.DataFrame(table_rows).set_index(["Strike", "Side"])
    st.dataframe(
        df.style.background_gradient(subset=["Vol (candle)", "OI Change"], cmap="RdYlGn"),
        use_container_width=True,
    )
