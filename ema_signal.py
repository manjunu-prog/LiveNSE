import requests
import time
import pytz
from datetime import datetime, timedelta

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3NzY3NDQ3MjMsImlhdCI6MTc3NjY1ODMyMywidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd2ViLmRoYW4uY28vaW5kZXgvcHJvZmlsZSIsImRoYW5DbGllbnRJZCI6IjExMDgwNjYwOTQifQ.NdvBxDiAw0mX46E8CnO2iiVI2bI98KCdYm1XcMPIRHFdKIQDveqmclPsVeXK9PSlR4Xy9PMB7tkOZb1VgG8FmA"
DHAN_CLIENT_ID    = "1108066094"
TELEGRAM_TOKEN    = "8243416633:AAFjISDBXvhqGsM8xvOkWOeQ4eEmhMPlkNU"
TELEGRAM_CHAT_ID  = "567677761"

INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
EMA_PERIODS  = [4, 8, 14, 28]

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────
def _headers():
    return {
        "access-token": DHAN_ACCESS_TOKEN,
        "client-id": DHAN_CLIENT_ID,
        "Content-Type": "application/json"
    }

def send_telegram(msg):
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
# MARKET HOURS CHECK (IST)
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
    return data.get("close", []), data.get("timestamp", [])

def get_prev_trading_day(now):
    day = now - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day

def fetch_candles():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)

    # ── Previous day: full session as EMA warmup ──
    prev      = get_prev_trading_day(now)
    prev_from = prev.replace(hour=9,  minute=15, second=0, microsecond=0)
    prev_to   = prev.replace(hour=15, minute=30, second=0, microsecond=0)
    prev_closes, _ = fetch_candles_range(prev_from, prev_to)
    print(f"Warmup candles (prev day): {len(prev_closes)}")

    # ── Today: 9:15 to now ──
    today_from = now.replace(hour=9, minute=15, second=0, microsecond=0)
    today_closes, today_ts = fetch_candles_range(today_from, now)
    print(f"Today candles: {len(today_closes)} | Latest: {today_closes[-1] if today_closes else 'N/A'}")

    if not today_closes:
        print("No today candle data.")
        return None, None

    # ── Combine: EMA computed on all, signal from today's last close ──
    all_closes = list(prev_closes) + list(today_closes)
    print(f"Total candles for EMA calculation: {len(all_closes)}")
    return all_closes, today_closes[-1]   # return combined closes + today's last close

# ──────────────────────────────────────────────
# SIGNAL LOGIC
# ──────────────────────────────────────────────
def get_signal(closes):
    if len(closes) < max(EMA_PERIODS):
        print(f"Not enough candles ({len(closes)}) — need {max(EMA_PERIODS)}+.")
        return None, {}

    emas         = {p: calc_ema(closes, p) for p in EMA_PERIODS}
    latest_close = closes[-1]

    print(f"Close: {latest_close} | EMA4: {emas[4]} | EMA8: {emas[8]} | EMA14: {emas[14]} | EMA28: {emas[28]}")

    if all(latest_close > emas[p] for p in EMA_PERIODS):
        return "CE", emas
    if all(latest_close < emas[p] for p in EMA_PERIODS):
        return "PE", emas
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

        all_closes, latest_close = fetch_candles()
        if all_closes is None:
            print("Could not fetch candle data.")
        else:
            signal, emas = get_signal(all_closes)
            ist_time     = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d-%b %H:%M")

            if signal is None:
                print("Not enough candles yet.")

            elif signal == "CE":
                msg = (
                    f"🟢 *NIFTY — CE SIGNAL* 🟢\n"
                    f"🕐 {ist_time} | 3-Min EMA Scan\n"
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
                send_telegram(msg)
                print("CE signal sent to Telegram.")

            elif signal == "PE":
                msg = (
                    f"🔴 *NIFTY — PE SIGNAL* 🔴\n"
                    f"🕐 {ist_time} | 3-Min EMA Scan\n"
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
                send_telegram(msg)
                print("PE signal sent to Telegram.")

            else:
                print(f"NEUTRAL — no clean signal.")
                print(f"EMA4={emas[4]} | EMA8={emas[8]} | EMA14={emas[14]} | EMA28={emas[28]}")
