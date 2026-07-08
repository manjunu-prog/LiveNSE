from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # pragma: no cover
    st_autorefresh = None

from fyers_client import FyersDataClient, fyers_credentials_source


IST = ZoneInfo("Asia/Kolkata")
DB_PATH = Path(__file__).resolve().with_name("option_chain_history.sqlite3")
AUTO_REFRESH_MS = 60_000
SNAPSHOT_TABLE = "option_chain_snapshots"

INDEXES = {
    "NIFTY": {"symbol": "NSE:NIFTY50-INDEX", "step": 50, "label": "NIFTY 50"},
    "BANKNIFTY": {"symbol": "NSE:NIFTYBANK-INDEX", "step": 100, "label": "BANK NIFTY"},
    "FINNIFTY": {"symbol": "NSE:FINNIFTY-INDEX", "step": 50, "label": "FINNIFTY"},
    "MIDCPNIFTY": {"symbol": "NSE:MIDCPNIFTY-INDEX", "step": 25, "label": "MIDCPNIFTY"},
    "SENSEX": {"symbol": "BSE:SENSEX-INDEX", "step": 100, "label": "SENSEX"},
}


def now_ist() -> datetime:
    return datetime.now(IST)


def as_bucket(value: Any) -> str:
    try:
        num = float(value or 0)
    except Exception:
        return "-"
    abs_num = abs(num)
    if abs_num >= 1e7:
        return f"{num / 1e7:.2f}Cr"
    if abs_num >= 1e5:
        return f"{num / 1e5:.2f}L"
    if abs_num >= 1e3:
        return f"{num / 1e3:.2f}K"
    return f"{num:.0f}"


def as_pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "-"


def as_num(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "-"


def step_round(price: float, step: int) -> int:
    return int(round(price / step) * step)


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def infer_oi_change(current_oi: float, oi_change_pct: float) -> float:
    if current_oi <= 0 or oi_change_pct == 0:
        return 0.0
    denom = 100.0 + oi_change_pct
    if abs(denom) < 1e-9:
        return 0.0
    return current_oi * oi_change_pct / denom


def ensure_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            snapshot_ts TEXT NOT NULL,
            snapshot_minute TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strike INTEGER NOT NULL,
            option_type TEXT NOT NULL,
            ltp REAL,
            ltp_change_pct REAL,
            volume REAL,
            oi REAL,
            oi_change_pct REAL,
            oi_change REAL,
            iv REAL,
            PRIMARY KEY (snapshot_minute, symbol, strike, option_type)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_lookup ON option_chain_snapshots(symbol, strike, option_type, snapshot_ts)")
    return conn


def secret_value(key: str, default: str = "") -> str:
    env_value = os.getenv(key, "").strip()
    if env_value:
        return env_value

    try:
        value = st.secrets.get(key, "")
    except Exception:
        value = ""
    if value:
        return str(value).strip()

    section_map = {
        "SUPABASE_URL": ("supabase", "url"),
        "SUPABASE_KEY": ("supabase", "key"),
        "SUPABASE_ANON_KEY": ("supabase", "anon_key"),
        "SUPABASE_SERVICE_ROLE_KEY": ("supabase", "service_role_key"),
        "SUPABASE_TABLE": ("supabase", "table"),
    }
    section_key = section_map.get(key)
    if section_key is None:
        return default

    section, nested_key = section_key
    try:
        nested_value = st.secrets.get(section, {}).get(nested_key, "")
    except Exception:
        nested_value = ""
    return str(nested_value).strip() if nested_value else default


def supabase_config() -> dict[str, str]:
    url = secret_value("SUPABASE_URL").rstrip("/")
    key = (
        secret_value("SUPABASE_SERVICE_ROLE_KEY")
        or secret_value("SUPABASE_KEY")
        or secret_value("SUPABASE_ANON_KEY")
    )
    table = secret_value("SUPABASE_TABLE", SNAPSHOT_TABLE) or SNAPSHOT_TABLE
    if not url or not key:
        return {}
    return {"url": url, "key": key, "table": table}


def storage_source() -> str:
    cfg = supabase_config()
    if cfg:
        return f"Supabase: {cfg['table']}"
    return f"Local SQLite: {DB_PATH.name}"


def supabase_headers(prefer: str | None = None) -> dict[str, str]:
    cfg = supabase_config()
    headers = {
        "apikey": cfg["key"],
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def upsert_snapshots_supabase(rows: list[dict[str, Any]]) -> bool:
    cfg = supabase_config()
    if not cfg or not rows:
        return False

    url = f"{cfg['url']}/rest/v1/{cfg['table']}"
    params = {"on_conflict": "snapshot_minute,symbol,strike,option_type"}
    response = requests.post(
        url,
        params=params,
        headers=supabase_headers("resolution=merge-duplicates"),
        json=rows,
        timeout=20,
    )
    response.raise_for_status()
    return True


def load_history_supabase(symbol: str, strike: int, option_type: str, limit: int) -> pd.DataFrame | None:
    cfg = supabase_config()
    if not cfg:
        return None

    url = f"{cfg['url']}/rest/v1/{cfg['table']}"
    params = {
        "select": "snapshot_ts,snapshot_minute,ltp,ltp_change_pct,volume,oi,oi_change_pct,oi_change,iv",
        "symbol": f"eq.{symbol}",
        "strike": f"eq.{int(strike)}",
        "option_type": f"eq.{option_type}",
        "order": "snapshot_ts.desc",
        "limit": str(limit),
    }
    response = requests.get(url, params=params, headers=supabase_headers(), timeout=20)
    response.raise_for_status()
    return pd.DataFrame(response.json()).sort_values("snapshot_ts").reset_index(drop=True)


def fetch_snapshot(client: FyersDataClient, symbol: str, strikecount: int) -> tuple[float, list[dict[str, Any]]]:
    quote_resp = client.fyers.quotes(data={"symbols": symbol})
    if quote_resp.get("s") != "ok":
        raise RuntimeError(quote_resp.get("message", "Unable to fetch underlying quote."))
    spot = safe_float(quote_resp["d"][0]["v"].get("lp", 0))

    chain_resp = client.fyers.optionchain(data={"symbol": symbol, "strikecount": strikecount, "timestamp": "", "greeks": "1"})
    if chain_resp.get("s") != "ok":
        raise RuntimeError(chain_resp.get("message", "Unable to fetch FYERS option chain."))
    return spot, chain_resp.get("data", {}).get("optionsChain", [])


def normalize_chain(spot: float, options_chain: list[dict[str, Any]], step: int, strikecount: int) -> pd.DataFrame:
    grouped: dict[int, dict[str, dict[str, Any]]] = {}
    for contract in options_chain:
        strike = safe_int(contract.get("strike_price"))
        side = str(contract.get("option_type", "")).upper().strip()
        if strike > 0 and side in {"CE", "PE"}:
            grouped.setdefault(strike, {})[side] = contract

    center = step_round(spot, step) if spot else 0
    strikes = [center + (i * step) for i in range(-strikecount, strikecount + 1)]

    rows: list[dict[str, Any]] = []
    for strike in strikes:
        ce = grouped.get(strike, {}).get("CE", {})
        pe = grouped.get(strike, {}).get("PE", {})

        ce_oi = safe_float(ce.get("oi", 0))
        pe_oi = safe_float(pe.get("oi", 0))
        ce_oi_pct = safe_float(ce.get("oichp", 0))
        pe_oi_pct = safe_float(pe.get("oichp", 0))

        rows.append(
            {
                "Strike": strike,
                "IV": ((safe_float(ce.get("iv", 0)) + safe_float(pe.get("iv", 0))) / 2) or None,
                "CE Symbol": str(ce.get("symbol", "")).strip(),
                "CE Volume": safe_float(ce.get("volume", 0)),
                "CE OI": ce_oi,
                "CE OI Chg": infer_oi_change(ce_oi, ce_oi_pct),
                "CE OI Chg %": ce_oi_pct,
                "CE Change": safe_float(ce.get("ltpchp", ce.get("chp", 0))),
                "CE LTP": safe_float(ce.get("ltp", 0)),
                "PE Symbol": str(pe.get("symbol", "")).strip(),
                "PE LTP": safe_float(pe.get("ltp", 0)),
                "PE Change": safe_float(pe.get("ltpchp", pe.get("chp", 0))),
                "PE OI Chg %": pe_oi_pct,
                "PE OI Chg": infer_oi_change(pe_oi, pe_oi_pct),
                "PE OI": pe_oi,
                "PE Volume": safe_float(pe.get("volume", 0)),
            }
        )

    return pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)


def store_snapshot(symbol: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    stamp = now_ist()
    minute = stamp.strftime("%Y-%m-%d %H:%M")
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        rows.extend(
            [
                {
                    "snapshot_ts": stamp.isoformat(),
                    "snapshot_minute": minute,
                    "symbol": symbol,
                    "strike": int(row["Strike"]),
                    "option_type": "CE",
                    "ltp": safe_float(row["CE LTP"]),
                    "ltp_change_pct": safe_float(row["CE Change"]),
                    "volume": safe_float(row["CE Volume"]),
                    "oi": safe_float(row["CE OI"]),
                    "oi_change_pct": safe_float(row["CE OI Chg %"]),
                    "oi_change": safe_float(row["CE OI Chg"]),
                    "iv": safe_float(row["IV"]),
                },
                {
                    "snapshot_ts": stamp.isoformat(),
                    "snapshot_minute": minute,
                    "symbol": symbol,
                    "strike": int(row["Strike"]),
                    "option_type": "PE",
                    "ltp": safe_float(row["PE LTP"]),
                    "ltp_change_pct": safe_float(row["PE Change"]),
                    "volume": safe_float(row["PE Volume"]),
                    "oi": safe_float(row["PE OI"]),
                    "oi_change_pct": safe_float(row["PE OI Chg %"]),
                    "oi_change": safe_float(row["PE OI Chg"]),
                    "iv": safe_float(row["IV"]),
                },
            ]
        )

    try:
        if upsert_snapshots_supabase(rows):
            return
    except Exception as exc:
        st.warning(f"Supabase snapshot write failed; using local SQLite for this refresh: {exc}")

    payload = [
        (
            row["snapshot_ts"],
            row["snapshot_minute"],
            row["symbol"],
            row["strike"],
            row["option_type"],
            row["ltp"],
            row["ltp_change_pct"],
            row["volume"],
            row["oi"],
            row["oi_change_pct"],
            row["oi_change"],
            row["iv"],
        )
        for row in rows
    ]
    with ensure_db() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO option_chain_snapshots
            (snapshot_ts, snapshot_minute, symbol, strike, option_type, ltp, ltp_change_pct, volume, oi, oi_change_pct, oi_change, iv)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )


def load_history(symbol: str, strike: int, option_type: str, limit: int = 90) -> pd.DataFrame:
    try:
        supabase_df = load_history_supabase(symbol, strike, option_type, limit)
    except Exception as exc:
        st.warning(f"Supabase history read failed; using local SQLite: {exc}")
        supabase_df = None

    if supabase_df is not None:
        df = supabase_df
    else:
        with ensure_db() as conn:
            df = pd.read_sql_query(
                """
                SELECT snapshot_ts, snapshot_minute, ltp, ltp_change_pct, volume, oi, oi_change_pct, oi_change, iv
                FROM option_chain_snapshots
                WHERE symbol = ? AND strike = ? AND option_type = ?
                ORDER BY snapshot_ts ASC
                """,
                conn,
                params=(symbol, int(strike), option_type),
            )

    if df.empty:
        return df

    df["snapshot_ts"] = pd.to_datetime(df["snapshot_ts"], errors="coerce")
    df["volume_delta"] = df["volume"].diff().fillna(0)
    df["oi_delta"] = df["oi"].diff().fillna(0)
    df["oi_pct_delta"] = df["oi_change_pct"].diff().fillna(0)
    return df.tail(limit).reset_index(drop=True)


def _rank_suffix(rank: int) -> str:
    if rank <= 2:
        return "+++"
    if rank <= 4:
        return "++"
    return "+"


def _tone_for_side_signal(side: str, action: str) -> str:
    side = side.upper()
    action = action.upper()
    if side == "CE":
        return "bullish" if action == "BUY" else "bearish"
    return "bearish" if action == "BUY" else "bullish"


def _empty_tags(frame: pd.DataFrame) -> dict[Any, dict[str, tuple[str, str]]]:
    return {
        idx: {"CE": ("-", "neutral"), "PE": ("-", "neutral")}
        for idx in frame.index
    }


def build_signal_tags(frame: pd.DataFrame, top_n: int = 5) -> dict[Any, dict[str, tuple[str, str]]]:
    tags = _empty_tags(frame)
    volume_winners: list[tuple[float, Any, str]] = []
    oi_winners: list[tuple[float, Any, str]] = []

    for idx, row in frame.iterrows():
        ce_volume = safe_float(row["CE Volume"])
        pe_volume = safe_float(row["PE Volume"])
        ce_oi_pct = safe_float(row["CE OI Chg %"])
        pe_oi_pct = safe_float(row["PE OI Chg %"])

        if ce_volume > pe_volume:
            volume_winners.append((ce_volume, idx, "CE"))
        elif pe_volume > ce_volume:
            volume_winners.append((pe_volume, idx, "PE"))

        if ce_oi_pct > pe_oi_pct:
            oi_winners.append((max(ce_oi_pct, 0.0), idx, "CE"))
        elif pe_oi_pct > ce_oi_pct:
            oi_winners.append((max(pe_oi_pct, 0.0), idx, "PE"))

    for rank, (_, idx, side) in enumerate(sorted(volume_winners, reverse=True)[:top_n], start=1):
        label = f"BUY{_rank_suffix(rank)}"
        tags[idx][side] = (label, _tone_for_side_signal(side, "BUY"))

    for rank, (_, idx, side) in enumerate(sorted(oi_winners, reverse=True)[:top_n], start=1):
        label = f"SELL{_rank_suffix(rank)}"
        tags[idx][side] = (label, _tone_for_side_signal(side, "SELL"))

    return tags


def market_bias(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if frame.empty:
        return {"label": "Neutral", "score": 0.0, "reason": "No chain data yet."}

    signal_tags = build_signal_tags(frame)
    atm = int(frame.iloc[len(frame) // 2]["Strike"])
    step = abs(int(frame.iloc[1]["Strike"]) - int(frame.iloc[0]["Strike"])) if len(frame) > 1 else 50
    score = 0.0
    clues: list[str] = []

    for _, row in frame.iterrows():
        dist = abs(int(row["Strike"]) - atm)
        weight = max(0.35, 1.0 - (dist / max(step * 8, 1)))
        tags = signal_tags[row.name]
        ce_tag, ce_tone = tags["CE"]
        pe_tag, pe_tone = tags["PE"]

        if ce_tone == "bullish":
            score += 2.0 * weight
        elif ce_tone == "bearish":
            score -= 2.0 * weight
        if pe_tone == "bullish":
            score += 2.0 * weight
        elif pe_tone == "bearish":
            score -= 2.0 * weight
        if dist <= step * 2 and (ce_tag != "-" or pe_tag != "-"):
            clues.append(f"{row['Strike']}: {ce_tag} / {pe_tag}")

    if score >= 2:
        label = "Bullish"
    elif score <= -2:
        label = "Bearish"
    else:
        label = "Range / Neutral"
    return {"label": label, "score": round(score, 2), "reason": clues[0] if clues else "Waiting for more snapshots."}


def format_display(frame: pd.DataFrame, symbol: str, atm: int) -> pd.DataFrame:
    signal_tags = build_signal_tags(frame)
    sorted_frame = frame.sort_values("Strike").copy()
    ce_spread_diff = sorted_frame["CE LTP"] - sorted_frame["CE LTP"].shift(-1)
    pe_spread_diff = sorted_frame["PE LTP"] - sorted_frame["PE LTP"].shift(1)
    rows: list[dict[str, Any]] = []
    for _, row in sorted_frame.iterrows():
        tags = signal_tags[row.name]
        ce_tag, _ = tags["CE"]
        pe_tag, _ = tags["PE"]
        rows.append(
            {
                "CE Tag": ce_tag,
                "CE Volume": as_bucket(row["CE Volume"]),
                "CE OI": as_bucket(row["CE OI"]),
                "CE OI Chg": as_bucket(row["CE OI Chg"]),
                "CE OI Chg %": as_pct(row["CE OI Chg %"]),
                "CE LTP": as_num(row["CE LTP"]),
                "CE Spread Diff": as_num(ce_spread_diff.loc[row.name]) if pd.notna(ce_spread_diff.loc[row.name]) else "-",
                "Strike": int(row["Strike"]),
                "PE LTP": as_num(row["PE LTP"]),
                "PE Spread Diff": as_num(pe_spread_diff.loc[row.name]) if pd.notna(pe_spread_diff.loc[row.name]) else "-",
                "PE OI Chg %": as_pct(row["PE OI Chg %"]),
                "PE OI Chg": as_bucket(row["PE OI Chg"]),
                "PE OI": as_bucket(row["PE OI"]),
                "PE Volume": as_bucket(row["PE Volume"]),
                "PE Tag": pe_tag,
            }
        )
    return pd.DataFrame(rows)


def style_table(display: pd.DataFrame, raw_frame: pd.DataFrame, atm: int) -> pd.io.formats.style.Styler:
    top_ce_volume = set(raw_frame["CE Volume"].nlargest(3).index)
    top_ce_oi = set(raw_frame["CE OI"].nlargest(3).index)
    top_pe_volume = set(raw_frame["PE Volume"].nlargest(3).index)
    top_pe_oichg = set(raw_frame["PE OI Chg %"].nlargest(3).index)

    def highlight_css(kind: str) -> str:
        if kind == "ce_volume":
            return "background-color: #dcfce7; color: #0f172a; font-weight: 800;"
        if kind == "ce_oi":
            return "background-color: #dbeafe; color: #0f172a; font-weight: 800;"
        if kind == "pe_volume":
            return "background-color: #fef3c7; color: #0f172a; font-weight: 800;"
        if kind == "pe_oichg":
            return "background-color: #fce7f3; color: #0f172a; font-weight: 800;"
        if kind == "bullish_tag":
            return "background-color: #dcfce7; color: #0f172a; font-weight: 900;"
        if kind == "bearish_tag":
            return "background-color: #fee2e2; color: #0f172a; font-weight: 900;"
        if kind == "atm":
            return "background-color: #fff7cc; color: #0f172a; font-weight: 900;"
        return ""

    def tone_from_tag(tag: Any) -> str:
        text = str(tag or "").upper()
        if text.startswith("BUY"):
            return "bullish"
        if text.startswith("SELL"):
            return "bearish"
        return "neutral"

    def row_style(row: pd.Series) -> list[str]:
        styles = [""] * len(row)
        strike = int(row.get("Strike", 0))
        is_atm = strike == atm
        ce_tone = tone_from_tag(row.get("CE Tag"))
        pe_tone = tone_from_tag(row.get("PE Tag"))

        if ce_tone != "neutral" and "CE Tag" in row.index:
            styles[row.index.get_loc("CE Tag")] = highlight_css("bullish_tag") if ce_tone == "bullish" else highlight_css("bearish_tag")
        if pe_tone != "neutral" and "PE Tag" in row.index:
            styles[row.index.get_loc("PE Tag")] = highlight_css("bullish_tag") if pe_tone == "bullish" else highlight_css("bearish_tag")

        if row.name in top_ce_volume and "CE Volume" in row.index:
            styles[row.index.get_loc("CE Volume")] = highlight_css("ce_volume")
        if row.name in top_ce_oi and "CE OI" in row.index:
            styles[row.index.get_loc("CE OI")] = highlight_css("ce_oi")
        if row.name in top_pe_volume and "PE Volume" in row.index:
            styles[row.index.get_loc("PE Volume")] = highlight_css("pe_volume")
        if row.name in top_pe_oichg and "PE OI Chg %" in row.index:
            styles[row.index.get_loc("PE OI Chg %")] = highlight_css("pe_oichg")

        if is_atm and "Strike" in row.index:
            styles[row.index.get_loc("Strike")] = highlight_css("atm")

        return styles

    styler = display.style.apply(row_style, axis=1)
    styler = styler.set_table_styles(
        [
            {
                "selector": "table",
                "props": [
                    ("border-collapse", "separate"),
                    ("border-spacing", "0"),
                    ("background-color", "#ffffff"),
                    ("color", "#0f172a"),
                ],
            },
            {
                "selector": "thead th",
                "props": [
                    ("background-color", "#f1f5f9"),
                    ("color", "#0f172a"),
                    ("font-weight", "700"),
                    ("border-bottom", "1px solid #cbd5e1"),
                ],
            },
            {
                "selector": "tbody td",
                "props": [
                    ("background-color", "#ffffff"),
                    ("color", "#0f172a"),
                    ("border-color", "#dbe4ee"),
                ],
            },
        ]
    )
    return styler


def inject_style() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg0: #08111f;
            --bg1: #0d1728;
            --panel: rgba(12, 20, 35, 0.88);
            --panel-strong: rgba(10, 18, 32, 0.96);
            --sidebar-bg: #f8fafc;
            --sidebar-text: #1f2937;
            --sidebar-muted: #6b7280;
            --sidebar-border: rgba(31, 41, 55, 0.16);
            --table-bg: #0b1320;
            --thead-bg: #162033;
            --cell-bg: #0f1726;
            --text: #f5f7fb;
            --muted: #a7b3c9;
            --line: rgba(255, 255, 255, 0.08);
            --bull-bg: rgba(46, 204, 113, 0.28);
            --bull-text: #102515;
            --bear-bg: rgba(231, 76, 60, 0.28);
            --bear-text: #2a1010;
        }
        @media (prefers-color-scheme: light) {
            :root {
                --bg0: #eef4fb;
                --bg1: #dfe9f5;
                --panel: rgba(255, 255, 255, 0.92);
                --panel-strong: rgba(255, 255, 255, 0.98);
                --sidebar-bg: #f8fafc;
                --sidebar-text: #1f2937;
                --sidebar-muted: #6b7280;
                --sidebar-border: rgba(31, 41, 55, 0.16);
                --table-bg: #ffffff;
                --thead-bg: #edf2f7;
                --cell-bg: #ffffff;
                --text: #0f172a;
                --muted: #475569;
                --line: rgba(15, 23, 42, 0.10);
                --bull-bg: rgba(34, 197, 94, 0.26);
                --bull-text: #06381a;
                --bear-bg: rgba(239, 68, 68, 0.24);
                --bear-text: #4a0f0f;
            }
        }
        .stApp {
            background:
                radial-gradient(circle at top left, rgba(40, 120, 255, 0.16), transparent 30%),
                radial-gradient(circle at top right, rgba(25, 178, 136, 0.12), transparent 25%),
                linear-gradient(180deg, var(--bg0) 0%, var(--bg1) 100%);
            color: var(--text);
        }
        .hero {
            border: 1px solid var(--line);
            background: var(--panel-strong);
            border-radius: 22px;
            padding: 1.1rem 1.2rem;
            box-shadow: 0 24px 60px rgba(0, 0, 0, 0.22);
            margin-bottom: 0.9rem;
        }
        .hero h1 {
            margin: 0;
            font-size: 2rem;
            letter-spacing: -0.04em;
            color: var(--text);
        }
        .hero p {
            margin: 0.25rem 0 0;
            color: var(--muted);
        }
        .pill {
            display: inline-flex;
            padding: 0.3rem 0.7rem;
            border-radius: 999px;
            margin-top: 0.65rem;
            font-size: 0.82rem;
            border: 1px solid var(--line);
            color: var(--text);
            background: rgba(127, 127, 127, 0.08);
        }
        .metric-card {
            padding: 0.9rem 1rem;
            border-radius: 18px;
            border: 1px solid var(--line);
            background: var(--panel);
            min-height: 108px;
        }
        .metric-good {
            background: linear-gradient(180deg, rgba(34, 197, 94, 0.18), var(--panel));
            border-color: rgba(34, 197, 94, 0.36);
        }
        .metric-bad {
            background: linear-gradient(180deg, rgba(239, 68, 68, 0.18), var(--panel));
            border-color: rgba(239, 68, 68, 0.36);
        }
        .metric-neutral {
            background: var(--panel);
        }
        .metric-card .label {
            text-transform: uppercase;
            letter-spacing: 0.12em;
            color: var(--muted);
            font-size: 0.74rem;
            margin-bottom: 0.25rem;
        }
        .metric-card .value {
            font-size: 1.45rem;
            font-weight: 750;
            color: var(--text);
            line-height: 1.15;
        }
        .metric-card .sub {
            color: var(--muted);
            margin-top: 0.3rem;
            font-size: 0.86rem;
        }
        .good { color: #35d07f; }
        .bad { color: #ff7f7f; }
        .neutral { color: #f0c75e; }

        .stCheckbox label,
        .stCheckbox span,
        .stRadio label,
        .stRadio span,
        div[data-testid="stCheckbox"] label,
        div[data-testid="stCheckbox"] p,
        div[data-testid="stRadio"] label,
        div[data-testid="stRadio"] p,
        div[data-testid="stMarkdownContainer"] p,
        .stCaption,
        .stText,
        .stSelectbox label {
            color: var(--text) !important;
        }

        div[data-testid="stCheckbox"] > label {
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 0.35rem 0.55rem;
            background: rgba(127, 127, 127, 0.05);
            min-height: 44px;
            align-items: center;
        }

        div[data-testid="stCheckbox"] svg {
            color: var(--text) !important;
            fill: var(--text) !important;
        }

        div[data-testid="stRadio"] [role="radiogroup"] {
            display: flex;
            flex-wrap: wrap;
            gap: 0.65rem 1rem;
        }

        div[data-testid="stRadio"] [role="radio"] {
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 0.35rem 0.65rem;
            background: rgba(127, 127, 127, 0.05);
            min-height: 44px;
        }

        div[data-testid="stSidebar"] {
            background: var(--sidebar-bg) !important;
            color: var(--sidebar-text) !important;
        }

        div[data-testid="stSidebar"] > div {
            background: var(--sidebar-bg) !important;
        }

        div[data-testid="stSidebar"] *,
        div[data-testid="stSidebar"] label,
        div[data-testid="stSidebar"] p,
        div[data-testid="stSidebar"] span,
        div[data-testid="stSidebar"] div[data-testid="stWidgetLabel"],
        div[data-testid="stSidebar"] div[data-testid="stWidgetLabel"] *,
        div[data-testid="stSidebar"] h1,
        div[data-testid="stSidebar"] h2,
        div[data-testid="stSidebar"] h3 {
            color: var(--sidebar-text) !important;
            -webkit-text-fill-color: var(--sidebar-text) !important;
        }

        div[data-testid="stSidebar"] .stCaption,
        div[data-testid="stSidebar"] div[data-testid="stCaptionContainer"],
        div[data-testid="stSidebar"] div[data-testid="stCaptionContainer"] * {
            color: var(--sidebar-muted) !important;
            -webkit-text-fill-color: var(--sidebar-muted) !important;
        }

        div[data-testid="stSidebar"] div[data-baseweb="select"] *,
        div[data-testid="stSidebar"] div[data-baseweb="select"] {
            color: var(--sidebar-text) !important;
            -webkit-text-fill-color: var(--sidebar-text) !important;
        }

        div[data-testid="stSidebar"] div[data-baseweb="select"] > div {
            background: #ffffff !important;
            border-color: var(--sidebar-border) !important;
        }

        div[data-testid="stSidebar"] div[data-testid="stCheckbox"] > label {
            background: rgba(31, 41, 55, 0.06) !important;
            border-color: var(--sidebar-border) !important;
        }

        div[data-testid="stSidebar"] div[data-testid="stCheckbox"] p,
        div[data-testid="stSidebar"] div[data-testid="stCheckbox"] span {
            color: var(--sidebar-text) !important;
            -webkit-text-fill-color: var(--sidebar-text) !important;
            opacity: 1 !important;
        }

        div[data-testid="stSidebar"] div[data-testid="stSlider"] *,
        div[data-testid="stSidebar"] div[data-testid="stSlider"] p,
        div[data-testid="stSidebar"] div[data-testid="stSlider"] span {
            color: var(--sidebar-text) !important;
            -webkit-text-fill-color: var(--sidebar-text) !important;
            opacity: 1 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def metric_box(label: str, value: str, sub: str, tone: str = "neutral") -> None:
    st.markdown(
        f"""
        <div class="metric-card metric-{tone}">
            <div class="label">{label}</div>
            <div class="value {tone}">{value}</div>
            <div class="sub">{sub}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def tag_display_tone(tag: str) -> str:
    text = str(tag or "").upper()
    if text.startswith("BUY"):
        return "good"
    if text.startswith("SELL"):
        return "bad"
    return "neutral"


def detect_order_blocks(
    candles: pd.DataFrame,
    spot: float,
    lb: int = 5,
    per_side_limit: int = 3,
    display_date: Any | None = None,
    keep_earliest: bool = True,
    reference_label: str = "spot",
) -> pd.DataFrame:
    if candles.empty or len(candles) < (lb * 2) + 2:
        return pd.DataFrame(columns=["Type", "Zone", "Low", "High", "CreatedTS", "Created", "Distance", "Status"])

    frame = candles.sort_values("timestamp").reset_index(drop=True).copy()
    pivot_highs: dict[int, float] = {}
    pivot_lows: dict[int, float] = {}
    for i in range(lb, len(frame) - lb):
        high_window = frame.loc[i - lb : i + lb, "high"]
        low_window = frame.loc[i - lb : i + lb, "low"]
        if frame.at[i, "high"] == high_window.max():
            pivot_highs[i + lb] = float(frame.at[i, "high"])
        if frame.at[i, "low"] == low_window.min():
            pivot_lows[i + lb] = float(frame.at[i, "low"])

    last_swing_high: float | None = None
    last_swing_low: float | None = None
    last_red_idx: int | None = None
    last_green_idx: int | None = None
    order_blocks: list[dict[str, Any]] = []

    for i, row in frame.iterrows():
        if i in pivot_highs:
            last_swing_high = pivot_highs[i]
        if i in pivot_lows:
            last_swing_low = pivot_lows[i]

        previous_close = float(frame.at[i - 1, "close"]) if i > 0 else float(row["close"])
        close = float(row["close"])

        if last_swing_high is not None and previous_close <= last_swing_high < close and last_red_idx is not None:
            candle = frame.loc[last_red_idx]
            order_blocks.append(
                {
                    "type": "Bullish OB",
                    "low": float(candle["low"]),
                    "high": float(candle["high"]),
                    "created": candle["timestamp"],
                }
            )
            last_swing_high = None

        if last_swing_low is not None and previous_close >= last_swing_low > close and last_green_idx is not None:
            candle = frame.loc[last_green_idx]
            order_blocks.append(
                {
                    "type": "Bearish OB",
                    "low": float(candle["low"]),
                    "high": float(candle["high"]),
                    "created": candle["timestamp"],
                }
            )
            last_swing_low = None

        if close > float(row["open"]):
            last_green_idx = i
        elif close < float(row["open"]):
            last_red_idx = i

    rows: list[dict[str, Any]] = []
    for zone in order_blocks:
        created_ts = pd.to_datetime(zone["created"])
        if zone["low"] <= spot <= zone["high"]:
            status = f"{reference_label.title()} inside zone"
            distance = 0.0
        elif spot < zone["low"]:
            status = f"Above {reference_label}"
            distance = zone["low"] - spot
        else:
            status = f"Below {reference_label}"
            distance = spot - zone["high"]
        rows.append(
            {
                "Type": zone["type"],
                "Zone": f"{zone['low']:,.2f} - {zone['high']:,.2f}",
                "Low": zone["low"],
                "High": zone["high"],
                "CreatedTS": created_ts,
                "Created": created_ts.strftime("%d %b %H:%M") if display_date is None else created_ts.strftime("%H:%M"),
                "Distance": distance,
                "Status": status,
            }
        )

    if not rows:
        return pd.DataFrame(columns=["Type", "Zone", "Low", "High", "CreatedTS", "Created", "Distance", "Status"])

    zones = pd.DataFrame(rows)
    if display_date is not None:
        same_day = zones["CreatedTS"].dt.date == display_date
        if same_day.any():
            zones = zones.loc[same_day].copy()

    selected_parts: list[pd.DataFrame] = []
    for _, side_frame in zones.groupby("Type", sort=False):
        if keep_earliest:
            earliest = side_frame.sort_values("CreatedTS", ascending=True).head(1)
            remaining = side_frame.drop(index=earliest.index)
            closest = remaining.sort_values(["Distance", "CreatedTS"], ascending=[True, False]).head(max(per_side_limit - 1, 0))
            selected_parts.append(pd.concat([earliest, closest]))
        else:
            closest = side_frame.sort_values(["Distance", "CreatedTS"], ascending=[True, False]).head(per_side_limit)
            selected_parts.append(closest)

    selected = pd.concat(selected_parts) if selected_parts else zones.head(0)
    return selected.sort_values("CreatedTS", ascending=False).reset_index(drop=True)


def detect_fvg_zones(
    candles: pd.DataFrame,
    spot: float,
    display_date: Any | None = None,
    limit: int = 6,
    reference_label: str = "spot",
) -> pd.DataFrame:
    if candles.empty or len(candles) < 3:
        return pd.DataFrame(columns=["Type", "Zone", "Low", "High", "CreatedTS", "Created", "Distance", "Status"])

    frame = candles.sort_values("timestamp").reset_index(drop=True).copy()
    rows: list[dict[str, Any]] = []
    for i in range(2, len(frame)):
        current = frame.loc[i]
        middle = frame.loc[i - 1]
        prior = frame.loc[i - 2]
        zone_type: str | None = None
        low = high = 0.0

        if float(prior["high"]) < float(current["low"]) and float(middle["close"]) > float(prior["high"]):
            zone_type = "Bullish FVG"
            low = float(prior["high"])
            high = float(current["low"])
        elif float(prior["low"]) > float(current["high"]) and float(middle["close"]) < float(prior["low"]):
            zone_type = "Bearish FVG"
            low = float(current["high"])
            high = float(prior["low"])

        if zone_type is None:
            continue

        created_ts = pd.to_datetime(current["timestamp"])
        if low <= spot <= high:
            status = f"{reference_label.title()} inside zone"
            distance = 0.0
        elif spot < low:
            status = f"Above {reference_label}"
            distance = low - spot
        else:
            status = f"Below {reference_label}"
            distance = spot - high

        rows.append(
            {
                "Type": zone_type,
                "Zone": f"{low:,.2f} - {high:,.2f}",
                "Low": low,
                "High": high,
                "CreatedTS": created_ts,
                "Created": created_ts.strftime("%d %b %H:%M") if display_date is None else created_ts.strftime("%H:%M"),
                "Distance": distance,
                "Status": status,
            }
        )

    if not rows:
        return pd.DataFrame(columns=["Type", "Zone", "Low", "High", "CreatedTS", "Created", "Distance", "Status"])

    zones = pd.DataFrame(rows)
    if display_date is not None:
        same_day = zones["CreatedTS"].dt.date == display_date
        if same_day.any():
            zones = zones.loc[same_day].copy()

    return zones.sort_values("CreatedTS", ascending=False).head(limit).reset_index(drop=True)


def style_order_block_table(table: pd.DataFrame) -> pd.io.formats.style.Styler:
    def row_style(row: pd.Series) -> list[str]:
        text = str(row.get("Type", ""))
        if text.startswith("Bullish"):
            css = "background-color: #dcfce7; color: #0f172a; font-weight: 700;"
        elif text.startswith("Bearish"):
            css = "background-color: #fee2e2; color: #0f172a; font-weight: 700;"
        else:
            css = "background-color: #ffffff; color: #0f172a;"
        return [css] * len(row)

    return table.style.apply(row_style, axis=1).set_table_styles(
        [
            {"selector": "table", "props": [("background-color", "#ffffff"), ("color", "#0f172a")]},
            {"selector": "thead th", "props": [("background-color", "#f1f5f9"), ("color", "#0f172a"), ("font-weight", "800")]},
            {"selector": "tbody td", "props": [("border-color", "#dbe4ee")]},
        ]
    )


def order_block_panel(client: FyersDataClient, symbol: str, spot: float, resolution: str) -> tuple[pd.DataFrame, int]:
    st.subheader("Order Block Zones")
    end_date = now_ist().date()
    start_date = end_date - timedelta(days=3)

    try:
        candles = client.fetch_history(symbol, resolution, start_date.isoformat(), end_date.isoformat())
    except Exception as exc:
        st.warning(f"Unable to fetch order-block candles: {exc}")
        return pd.DataFrame(), 0

    zones = detect_order_blocks(candles, spot, display_date=end_date)
    if zones.empty:
        st.info("No active order blocks found in the recent candle window.")
        return zones, len(candles)

    nearest = zones.sort_values("Distance").iloc[0]
    tone = "good" if nearest["Type"] == "Bullish OB" else "bad"
    c1, c2, c3 = st.columns(3)
    with c1:
        metric_box("Nearest OB", nearest["Type"], nearest["Zone"], tone)
    with c2:
        metric_box("OB Distance", as_num(nearest["Distance"]), nearest["Status"], tone)
    with c3:
        metric_box("OB Timeframe", f"{resolution} min", f"{len(candles)} candles scanned", "neutral")

    table = zones[["Type", "Zone", "Created", "Distance", "Status"]].copy()
    table["Distance"] = table["Distance"].map(as_num)
    st.dataframe(style_order_block_table(table), use_container_width=True, hide_index=True)
    return zones, len(candles)


def option_contract_ob_context(
    client: FyersDataClient,
    option_symbol: str,
    option_label: str,
    ltp: float,
    resolution: str,
) -> tuple[pd.DataFrame, int]:
    if not option_symbol:
        st.info(f"{option_label}: FYERS did not return an option symbol for candle lookup.")
        return pd.DataFrame(), 0

    end_date = now_ist().date()
    start_date = end_date - timedelta(days=3)
    try:
        candles = client.fetch_history(option_symbol, resolution, start_date.isoformat(), end_date.isoformat())
    except Exception as exc:
        st.warning(f"{option_label}: unable to fetch option candles for {option_symbol}: {exc}")
        return pd.DataFrame(), 0

    # Option contracts are noisier than the index. A wider swing window avoids
    # promoting late displacement candles/FVG stacks as fresh order blocks.
    ob_zones = detect_order_blocks(
        candles,
        ltp,
        lb=7,
        per_side_limit=4,
        display_date=None,
        keep_earliest=True,
        reference_label="LTP",
    )
    fvg_zones = detect_fvg_zones(candles, ltp, display_date=None, reference_label="LTP")
    zones = pd.concat([ob_zones, fvg_zones], ignore_index=True)
    if not zones.empty:
        zones = zones.sort_values("CreatedTS", ascending=False).reset_index(drop=True)
    return zones, len(candles)


def selected_option_order_block_panel(
    client: FyersDataClient,
    selected_row: pd.Series,
    resolution: str,
) -> None:
    strike = int(selected_row["Strike"])
    st.subheader(f"Strike {strike} Option Order Blocks")

    contexts = [
        ("CE", str(selected_row.get("CE Symbol", "") or ""), safe_float(selected_row["CE LTP"])),
        ("PE", str(selected_row.get("PE Symbol", "") or ""), safe_float(selected_row["PE LTP"])),
    ]
    cols = st.columns(2)

    for col, (side, symbol, ltp) in zip(cols, contexts):
        with col:
            st.markdown(f"**{side} option OB**")
            zones, candle_count = option_contract_ob_context(client, symbol, f"{strike} {side}", ltp, resolution)
            if zones.empty:
                st.info(f"No active {side} option OB zones found.")
                continue

            latest = zones.sort_values("CreatedTS", ascending=False).iloc[0]
            tone = "good" if latest["Type"] == "Bullish OB" else "bad"
            is_bullish = str(latest["Type"]).startswith("Bullish")
            tone = "good" if is_bullish else "bad"
            sentiment = "Support zone" if is_bullish else "Resistance zone"
            metric_box(f"{side} Latest Zone", latest["Type"], latest["Zone"], tone)
            metric_box(f"{side} Sentiment", sentiment, f"{symbol} | LTP {as_num(ltp)} | {candle_count} candles", tone)

            table = zones[["Type", "Zone", "Created", "Distance", "Status"]].copy()
            table["Distance"] = table["Distance"].map(as_num)
            st.dataframe(style_order_block_table(table), use_container_width=True, hide_index=True)


def delta_cell_style(val: Any) -> str:
    try:
        num = float(val)
    except Exception:
        return ""
    if num > 0:
        return "background-color: #dcfce7; color: #0f172a; font-weight: 700;"
    if num < 0:
        return "background-color: #fee2e2; color: #0f172a; font-weight: 700;"
    return "color: #475569;"


def history_panel(symbol: str, strike: int) -> None:
    ce_hist = load_history(symbol, strike, "CE")
    pe_hist = load_history(symbol, strike, "PE")
    st.subheader(f"Strike {strike} Minute History")
    st.caption("Stored as 1-minute snapshots. The latest row is shown first, with volume in lakhs/crores and percentages rounded for readability.")

    if ce_hist.empty and pe_hist.empty:
        st.info("No history yet. Keep the app running for a few refreshes and this panel will fill automatically.")
        return

    left, right = st.columns(2)

    def render_history_table(view: pd.DataFrame) -> pd.io.formats.style.Styler:
        latest_first = view.sort_values("snapshot_ts", ascending=False).reset_index(drop=True)
        table = latest_first[["Time", "ltp", "volume", "Δ Volume", "oi", "Δ OI", "oi_change_pct"]].rename(
            columns={"ltp": "LTP", "volume": "Volume", "oi": "OI", "oi_change_pct": "OI Chg %"}
        )
        table["LTP"] = table["LTP"].map(lambda v: as_num(v))
        table["Volume"] = table["Volume"].map(lambda v: as_bucket(v))
        table["Δ Volume"] = table["Δ Volume"].map(lambda v: as_bucket(v))
        table["OI"] = table["OI"].map(lambda v: as_bucket(v))
        table["Δ OI"] = table["Δ OI"].map(lambda v: as_bucket(v))
        table["OI Chg %"] = table["OI Chg %"].map(lambda v: as_pct(v))

        style = table.style
        style = style.set_table_styles(
            [
                {"selector": "table", "props": [("background-color", "#ffffff"), ("color", "#0f172a")]},
                {"selector": "thead th", "props": [("background-color", "#f1f5f9"), ("color", "#0f172a")]},
                {"selector": "tbody td", "props": [("background-color", "#ffffff"), ("color", "#0f172a"), ("border-color", "#dbe4ee")]},
            ]
        )
        def row_style(row: pd.Series) -> list[str]:
            raw = latest_first.iloc[int(row.name)]
            styles = [""] * len(row)
            raw_map = {"Δ Volume": "volume_delta", "Δ OI": "oi_delta", "OI Chg %": "oi_change_pct"}
            for col, raw_col in raw_map.items():
                idx = row.index.get_loc(col)
                styles[idx] = delta_cell_style(raw[raw_col])
            return styles

        style = style.apply(row_style, axis=1)
        return style

    with left:
        st.markdown("**CE snapshot flow**")
        if ce_hist.empty:
            st.info("No CE history yet.")
        else:
            view = ce_hist.copy()
            view["Time"] = view["snapshot_ts"].dt.strftime("%H:%M:%S")
            view["Δ Volume"] = view["volume_delta"].round(0).astype(int)
            view["Δ OI"] = view["oi_delta"].round(0).astype(int)
            st.dataframe(render_history_table(view), use_container_width=True, hide_index=True)
            chart = view.rename(columns={"ltp": "LTP", "oi_change_pct": "OI Chg %"}).set_index("snapshot_ts")
            st.line_chart(chart[["LTP", "OI Chg %"]])

    with right:
        st.markdown("**PE snapshot flow**")
        if pe_hist.empty:
            st.info("No PE history yet.")
        else:
            view = pe_hist.copy()
            view["Time"] = view["snapshot_ts"].dt.strftime("%H:%M:%S")
            view["Δ Volume"] = view["volume_delta"].round(0).astype(int)
            view["Δ OI"] = view["oi_delta"].round(0).astype(int)
            st.dataframe(render_history_table(view), use_container_width=True, hide_index=True)
            chart = view.rename(columns={"ltp": "LTP", "oi_change_pct": "OI Chg %"}).set_index("snapshot_ts")
            st.line_chart(chart[["LTP", "OI Chg %"]])


def strike_checkbox_picker(frame: pd.DataFrame, atm: int) -> int:
    strikes = frame["Strike"].tolist()
    if "selected_strike" not in st.session_state or st.session_state.selected_strike not in strikes:
        st.session_state.selected_strike = atm

    st.markdown("### Choose Strike")
    st.caption("ATM is preselected. Pick one strike to inspect its 1-minute history.")

    labels = [f"{strike} {'(ATM)' if strike == atm else ''}".strip() for strike in strikes]
    selected_index = strikes.index(st.session_state.selected_strike)
    current_label = labels[selected_index]
    forced_label = st.session_state.pop("_force_strike_radio", None)
    if forced_label in labels:
        st.session_state.strike_radio_choice = forced_label
    elif st.session_state.get("strike_radio_choice") not in labels:
        st.session_state.strike_radio_choice = current_label

    selected_label = st.radio(
        "Strike",
        labels,
        index=labels.index(st.session_state.strike_radio_choice),
        horizontal=True,
        label_visibility="collapsed",
        key="strike_radio_choice",
    )
    st.session_state.selected_strike = strikes[labels.index(selected_label)]
    return int(st.session_state.selected_strike)


def main() -> None:
    st.set_page_config(page_title="FYERS Option Chain Desk", layout="wide", initial_sidebar_state="expanded")
    inject_style()

    st.markdown(
        """
        <div class="hero">
            <h1>FYERS Option Chain Desk</h1>
            <p>Live option chain with OI change %, volume pressure, and strike-level 1-minute history.</p>
            <div class="pill">Click a strike row, then inspect its timeline below</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar:
        st.header("Controls")
        index_key = st.selectbox("Index", list(INDEXES.keys()), index=0)
        strikecount = st.slider("Strikes each side", 5, 15, 10, 1)
        show_order_blocks = st.checkbox("Show order blocks", value=True)
        ob_timeframe = st.selectbox("OB timeframe", ["1", "5", "15"], index=1)
        auto_refresh = st.checkbox("Auto refresh every minute", value=True)
        if auto_refresh and st_autorefresh is not None:
            st_autorefresh(interval=AUTO_REFRESH_MS, key="option_chain_refresh")
        st.caption(f"Credentials source: {fyers_credentials_source()}")
        st.caption(f"Storage: {storage_source()}")

    cfg = INDEXES[index_key]

    try:
        client = FyersDataClient.from_env()
    except Exception as exc:
        st.error(f"FYERS login failed: {exc}")
        st.stop()

    with st.spinner("Pulling live FYERS quote and option chain..."):
        try:
            spot, options_chain = fetch_snapshot(client, cfg["symbol"], strikecount)
        except Exception as exc:
            st.error(str(exc))
            st.stop()

    frame = normalize_chain(spot, options_chain, cfg["step"], strikecount)
    if frame.empty:
        st.warning("No option-chain rows were returned.")
        st.stop()

    store_snapshot(cfg["symbol"], frame)
    bias = market_bias(frame, cfg["symbol"])
    atm = step_round(spot, cfg["step"])
    pcr = frame["PE OI"].sum() / frame["CE OI"].sum() if frame["CE OI"].sum() else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        metric_box("Underlying", f"{spot:,.2f}", cfg["label"], "good")
    with c2:
        tone = "good" if bias["label"] == "Bullish" else "bad" if bias["label"] == "Bearish" else "neutral"
        metric_box("Market Bias", bias["label"], bias["reason"], tone)
    with c3:
        metric_box("Net Score", f"{bias['score']:+.2f}", "Higher score means stronger bullish pressure", "good" if bias["score"] > 0 else "bad" if bias["score"] < 0 else "neutral")
    with c4:
        metric_box("PCR", f"{pcr:.2f}", "PE OI / CE OI", "good" if pcr > 1 else "bad" if pcr < 1 else "neutral")
    with c5:
        metric_box("ATM Strike", str(atm), "Nearest rounded strike", "neutral")

    if show_order_blocks:
        order_block_panel(client, cfg["symbol"], spot, ob_timeframe)

    st.markdown("### Option Chain")
    st.caption("OI change % is the primary signal column. The tags summarize the strike pressure on each side.")

    display = format_display(frame, cfg["symbol"], atm)
    selection = st.dataframe(
        style_table(display, frame, atm),
        use_container_width=True,
        hide_index=True,
        key="option_chain_table",
        on_select="rerun",
        selection_mode="single-row",
        column_order=[
            "CE Tag",
            "CE Volume",
            "CE OI",
            "CE OI Chg",
            "CE OI Chg %",
            "CE LTP",
            "CE Spread Diff",
            "Strike",
            "PE LTP",
            "PE Spread Diff",
            "PE OI Chg %",
            "PE OI Chg",
            "PE OI",
            "PE Volume",
            "PE Tag",
        ],
    )

    selected_strike = None
    if hasattr(selection, "selection") and getattr(selection.selection, "rows", None):
        idx = selection.selection.rows[0]
        if 0 <= idx < len(frame):
            selected_strike = int(frame.iloc[idx]["Strike"])

    if selected_strike is None:
        selected_strike = strike_checkbox_picker(frame, atm)
    else:
        st.session_state.selected_strike = selected_strike
        st.session_state._force_strike_radio = f"{selected_strike} {'(ATM)' if selected_strike == atm else ''}".strip()
        strike_checkbox_picker(frame, atm)

    selected_row = frame.loc[frame["Strike"] == selected_strike].iloc[0]
    tags = build_signal_tags(frame)[selected_row.name]

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        ce_tag, _ = tags["CE"]
        metric_box("CE Tag", ce_tag, f"OI Chg %: {as_pct(selected_row['CE OI Chg %'])}", tag_display_tone(ce_tag))
    with s2:
        metric_box("Selected Strike", str(selected_strike), f"ATM distance: {selected_strike - atm:+}", "neutral")
    with s3:
        metric_box("Strike IV", as_num(selected_row["IV"]), "Average IV when available", "neutral")
    with s4:
        pe_tag, _ = tags["PE"]
        metric_box("PE Tag", pe_tag, f"OI Chg %: {as_pct(selected_row['PE OI Chg %'])}", tag_display_tone(pe_tag))

    if show_order_blocks:
        selected_option_order_block_panel(client, selected_row, ob_timeframe)

    history_panel(cfg["symbol"], selected_strike)


if __name__ == "__main__":
    main()
