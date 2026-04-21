import requests
import json
import os
import pytz
from datetime import datetime, timedelta

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3NzY4Nzk0MzAsImlhdCI6MTc3Njc5MzAzMCwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd2ViLmRoYW4uY28vaW5kZXgvcHJvZmlsZSIsImRoYW5DbGllbnRJZCI6IjExMDgwNjYwOTQifQ.j-Kxz_mchy_QGtyNz0_pu-WOfw36mgD0ORY_ngS_GnDTMLFKrQNMO_ViM_a6fl9rb4kfNz5OEN_YA8hep4OM4g"
DHAN_CLIENT_ID   = "1108066094"
TELEGRAM_TOKEN   = "8571189424:AAGgfMZ1ET9s-z3bRqJnoJ_gHuL0JFe4x8k"
TELEGRAM_CHAT_ID = "567677761"
INTRADAY_URL     = "https://api.dhan.co/v2/charts/intraday"
STATE_FILE       = "ema_state.json"   # saved in repo to persist between runs
EMA_PERIODS      = [4, 8, 14, 28]

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────
def _headers():
    return {
        "access-token": DHAN_ACCESS_TOKEN,
        "client-id":    DHAN_CLIENT_ID,
        "Content-Type": "application/json"
    }

def send_telegram(msg, repeat=False):
    """Send once, or repeat every 10s for 2 mins if repeat=True."""
    send_once(msg)
    if repeat:
        import time
        for i in range(1, 12):  # 11 more times = 12 total over 2 mins
            time.sleep(10)
            send_once(msg)

def send_once(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}")

def calc_ema(closes, period):
    if len(closes) < period:
        return None
    k   = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 2)

# ──────────────────────────────────────────────
# STATE — read/write last signal to file
# ──────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_signal": "NEUTRAL", "last_date": ""}

def save_state(signal, date_str):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_signal": signal, "last_date": date_str}, f)
    print(f"State saved: {signal} on {date_str}")

# ──────────────────────────────────────────────
# MARKET HOURS CHECK
# ──────────────────────────────────────────────
def is_market_open():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    if now.weekday() >= 5:
        return False, f"Weekend ({now.strftime('%A')})"
    open_time  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if open_time <= now <= close_time:
        return True, now.strftime('%H:%M IST')
    return False, f"Market closed — {now.strftime('%H:%M IST')}"

# ──────────────────────────────────────────────
# FETCH CANDLES
# ──────────────────────────────────────────────
def fetch_candles_range(from_dt, to_dt):
    payload = {
        "securityId":      "13",
        "exchangeSegment": "IDX_I",
        "instrument":      "INDEX",
        "interval":        "3",
        "fromDate":        from_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate":          to_dt.strftime("%Y-%m-%d %H:%M:%S")
    }
    r    = requests.post(INTRADAY_URL, json=payload, headers=_headers(), timeout=15)
    data = r.json()
    return data.get("close", [])

def get_prev_trading_day(now):
    day = now - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day

def fetch_all_closes():
    ist  = pytz.timezone("Asia/Kolkata")
    now  = datetime.now(ist)

    # Prev day warmup
    prev      = get_prev_trading_day(now)
    prev_from = prev.replace(hour=9,  minute=15, second=0, microsecond=0)
    prev_to   = prev.replace(hour=15, minute=30, second=0, microsecond=0)
    prev_closes = fetch_candles_range(prev_from, prev_to)
    print(f"Warmup candles: {len(prev_closes)}")

    # Today
    today_from   = now.replace(hour=9, minute=15, second=0, microsecond=0)
    today_closes = fetch_candles_range(today_from, now)
    print(f"Today candles : {len(today_closes)} | Latest: {today_closes[-1] if today_closes else 'N/A'}")

    if not today_closes:
        return None

    return list(prev_closes) + list(today_closes), today_closes[-1]

# ──────────────────────────────────────────────
# SIGNAL
# ──────────────────────────────────────────────
def get_signal(all_closes):
    if len(all_closes) < max(EMA_PERIODS):
        print("Not enough candles for EMA calculation.")
        return None, {}

    emas  = {p: calc_ema(all_closes, p) for p in EMA_PERIODS}
    close = all_closes[-1]
    print(f"Close: {close} | EMA4: {emas[4]} | EMA8: {emas[8]} | EMA14: {emas[14]} | EMA28: {emas[28]}")

    if all(close > emas[p] for p in EMA_PERIODS):
        return "BULLISH", emas
    if all(close < emas[p] for p in EMA_PERIODS):
        return "BEARISH", emas
    return "NEUTRAL", emas

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    open_status, reason = is_market_open()
    if not open_status:
        print(f"Skipping — {reason}")
    else:
        print(f"Market open — {reason}")

        result = fetch_all_closes()
        if result is None:
            print("No candle data.")
        else:
            all_closes, latest_close = result
            signal, emas             = get_signal(all_closes)

            if signal is None:
                print("Signal: None — not enough data.")
            else:
                # Load previous state
                state       = load_state()
                prev_signal = state.get("last_signal", "NEUTRAL")
                ist_time    = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d-%b %H:%M")
                today_str   = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d")

                print(f"Signal: {signal} | Previous: {prev_signal}")

                # ── Only alert if signal CHANGED ──
                if signal == prev_signal:
                    print(f"No change in signal ({signal}) — no alert sent.")

                elif signal == "BULLISH":
                    msg = (
                        f"🟢 *NIFTY — BULLISH SIGNAL* 🟢\n"
                        f"🕐 {ist_time} | 3-Min EMA Crossover\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📈 Price `{latest_close}` is *ABOVE all 4 EMAs*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"  EMA  4 : `{emas[4]}`\n"
                        f"  EMA  8 : `{emas[8]}`\n"
                        f"  EMA 14 : `{emas[14]}`\n"
                        f"  EMA 28 : `{emas[28]}`\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"✅ *Bias: BUY CE side*"
                    )
                    send_telegram(msg, repeat=True)
                    save_state("BULLISH", today_str)
                    print("Bullish alert sent ✅")

                elif signal == "BEARISH":
                    msg = (
                        f"🔴 *NIFTY — BEARISH SIGNAL* 🔴\n"
                        f"🕐 {ist_time} | 3-Min EMA Crossover\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📉 Price `{latest_close}` is *BELOW all 4 EMAs*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"  EMA  4 : `{emas[4]}`\n"
                        f"  EMA  8 : `{emas[8]}`\n"
                        f"  EMA 14 : `{emas[14]}`\n"
                        f"  EMA 28 : `{emas[28]}`\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"✅ *Bias: BUY PE side*"
                    )
                    send_telegram(msg, repeat=True)
                    save_state("BEARISH", today_str)
                    print("Bearish alert sent ✅")

                else:
                    # Signal went NEUTRAL — just update state, no alert
                    save_state("NEUTRAL", today_str)
                    print("Signal is NEUTRAL — state updated, no alert sent.")
