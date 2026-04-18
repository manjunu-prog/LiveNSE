import requests
import time
import pytz
from datetime import datetime, timedelta

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3NzY1NDAwMTgsImlhdCI6MTc3NjQ1MzYxOCwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd2ViLmRoYW4uY28vaW5kZXgvcHJvZmlsZSIsImRoYW5DbGllbnRJZCI6IjExMDgwNjYwOTQifQ.7MGUMp8u6Gt0liAdwOQJnp_NWeVbOFXfH99_0eb-amqKum-6crJvBGXwOQIJFgPUSIxpL-BnwIGklrpSAbbTiQ"
DHAN_CLIENT_ID    = "1108066094"
TELEGRAM_TOKEN    = "8243416633:AAFjISDBXvhqGsM8xvOkWOeQ4eEmhMPlkNU"
TELEGRAM_CHAT_ID  = "567677761"

# Nifty Fut identifiers on Dhan
# NSE_FNO segment, security_id for Nifty current month future
NIFTY_FUT_SECURITY_ID = "13"       # Nifty 50 index underlying
NIFTY_FUT_SEGMENT     = "IDX_I"    # Will resolve to FNO fut via candle API

# Dhan intraday candle endpoint
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

EMA_PERIODS = [4, 8, 14, 28]

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
    """Calculate EMA using standard smoothing factor."""
    if len(closes) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period  # seed with SMA
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
# FETCH 3-MIN CANDLES FOR NIFTY FUT
# ──────────────────────────────────────────────
def fetch_candles():
    ist  = pytz.timezone("Asia/Kolkata")
    now  = datetime.now(ist)
    # Fetch from market open today to now
    from_date = now.replace(hour=9, minute=15, second=0, microsecond=0)
    to_date   = now

    payload = {
        "securityId":  "13",           # Nifty 50
        "exchangeSegment": "IDX_I",    # Index segment
        "instrument":  "INDEX",
        "interval":    "3",            # 3-minute candles
        "fromDate":    from_date.strftime("%Y-%m-%d %H:%M:%S"),
        "toDate":      to_date.strftime("%Y-%m-%d %H:%M:%S")
    }

    r = requests.post(INTRADAY_URL, json=payload, headers=_headers(), timeout=15)
    data = r.json()

    # Dhan returns: {"open":[], "high":[], "low":[], "close":[], "volume":[], "timestamp":[]}
    closes     = data.get("close", [])
    timestamps = data.get("timestamp", [])

    if not closes:
        print(f"No candle data returned. Response: {data}")
        return None, None

    print(f"Fetched {len(closes)} candles. Latest close: {closes[-1]}")
    return closes, timestamps

# ──────────────────────────────────────────────
# SIGNAL LOGIC
# ──────────────────────────────────────────────
def get_signal(closes):
    if len(closes) < max(EMA_PERIODS):
        print(f"Not enough candles ({len(closes)}) to compute EMAs. Need {max(EMA_PERIODS)}+.")
        return None, {}

    emas = {}
    for period in EMA_PERIODS:
        emas[period] = calc_ema(closes, period)

    latest_close = closes[-1]

    print(f"Close: {latest_close} | EMA4: {emas[4]} | EMA8: {emas[8]} | EMA14: {emas[14]} | EMA28: {emas[28]}")

    # CE signal: price above ALL 4 EMAs
    if all(latest_close > emas[p] for p in EMA_PERIODS):
        return "CE", emas

    # PE signal: price below ALL 4 EMAs
    if all(latest_close < emas[p] for p in EMA_PERIODS):
        return "PE", emas

    # Mixed / no clean signal
    return "NEUTRAL", emas

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    open_status, reason = is_market_open()
    if not open_status:
        print(f"⏸ Skipping — {reason}")
    else:
        print(f"✅ Market open — {reason}")
        closes, timestamps = fetch_candles()
        if closes is None:
            print("❌ Could not fetch candle data.")
        else:
            signal, emas = get_signal(closes)
            latest_close = closes[-1]
            ist_time     = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d-%b %H:%M")

            if signal == "CE":
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
                print("📤 CE signal sent to Telegram.")

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
                print("📤 PE signal sent to Telegram.")

            else:
                # NEUTRAL — no message sent, just log
                print(f"⚪ NEUTRAL — price between EMAs. No alert sent.")
                print(f"   EMA4={emas[4]} | EMA8={emas[8]} | EMA14={emas[14]} | EMA28={emas[28]}")
