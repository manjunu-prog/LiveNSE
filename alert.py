import requests
import time

# ──────────────────────────────────────────────
# CONFIG — same as your play.py
# ──────────────────────────────────────────────
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3NzY0NTExMjIsImlhdCI6MTc3NjM2NDcyMiwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd2ViLmRoYW4uY28vaW5kZXgvcHJvZmlsZSIsImRoYW5DbGllbnRJZCI6IjExMDgwNjYwOTQifQ.gV5EAgoVGSxuim4Sk9j4y1JA2dJol_BXr8F_ROLlEiDb9gyV3EDQM50EVLra1BZVuEcJQ54NO3_qT6-q41SUQg"
DHAN_CLIENT_ID    = "1108066094"
TELEGRAM_TOKEN    = "8243416633:AAFjISDBXvhqGsM8xvOkWOeQ4eEmhMPlkNU"
TELEGRAM_CHAT_ID  = "567677761"

API_BASE        = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{API_BASE}/optionchain"
EXPIRY_LIST_URL = f"{API_BASE}/optionchain/expirylist"

INDICES = {
    "NIFTY":  {"Scrip": 13, "Segments": ["IDX_I", "NSE_FNO"], "step": 50},
    "SENSEX": {"Scrip": 1,  "Segments": ["BSE_FNO", "IDX_I"], "step": 100},
}

def _headers():
    return {
        "access-token": DHAN_ACCESS_TOKEN,
        "client-id": DHAN_CLIENT_ID,
        "Content-Type": "application/json"
    }

def send_telegram(msg):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )

def fetch_and_alert(index_name, cfg):
    # Step 1: Get nearest expiry
    found_expiry, used_seg = None, None
    for seg in cfg["Segments"]:
        r = requests.post(EXPIRY_LIST_URL,
                          json={"UnderlyingScrip": cfg["Scrip"], "UnderlyingSeg": seg},
                          headers=_headers(), timeout=10)
        exp_list = r.json().get("data", [])
        if exp_list:
            found_expiry, used_seg = exp_list[0], seg
            break

    if not found_expiry:
        print(f"[{index_name}] No expiry found.")
        return

    # Step 2: Fetch option chain
    r_oc = requests.post(OPTIONCHAIN_URL,
                         json={"UnderlyingScrip": cfg["Scrip"], "UnderlyingSeg": used_seg, "Expiry": found_expiry},
                         headers=_headers(), timeout=10)
    data_sec = r_oc.json().get("data", {})
    oc_map   = data_sec.get("oc", {})
    ltp      = float(data_sec.get("last_price") or 0)

    if not oc_map or ltp == 0:
        print(f"[{index_name}] No OC data.")
        return

    # Step 3: Build rows around ATM
    atm  = round(ltp / cfg["step"]) * cfg["step"]
    rows = []
    for strike_s, legs in oc_map.items():
        strike_f = float(strike_s)
        if abs(strike_f - atm) <= (cfg["step"] * 10):
            ce = legs.get("ce", {})
            pe = legs.get("pe", {})
            rows.append({
                "strike":  strike_f,
                "c_ltp":   float(ce.get("last_price", 0)),
                "p_ltp":   float(pe.get("last_price", 0)),
                "c_oi":    int(ce.get("oi", 0)),
                "p_oi":    int(pe.get("oi", 0)),
                "c_vol":   int(ce.get("volume", 0)),
                "p_vol":   int(pe.get("volume", 0)),
            })

    if not rows:
        return

    # Step 4: Find max OI and max Volume strikes
    max_c_oi_row  = max(rows, key=lambda x: x["c_oi"])
    max_p_oi_row  = max(rows, key=lambda x: x["p_oi"])
    max_c_vol_row = max(rows, key=lambda x: x["c_vol"])
    max_p_vol_row = max(rows, key=lambda x: x["p_vol"])

    total_c_oi = sum(r["c_oi"] for r in rows)
    total_p_oi = sum(r["p_oi"] for r in rows)
    pcr = total_p_oi / total_c_oi if total_c_oi else 0

    # Step 5: Send Telegram message
    msg = (
        f"📊 *{index_name} Option Chain — {time.strftime('%d-%b %H:%M')}*\n"
        f"Expiry: `{found_expiry}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 LTP: `{ltp:,.0f}` | ATM: `{int(atm)}` | PCR: `{pcr:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 *CALL (CE)*\n"
        f"  🔹 Highest OI  : `{int(max_c_oi_row['strike'])}` — OI: `{max_c_oi_row['c_oi']/1e5:.2f}L` | LTP: `{max_c_oi_row['c_ltp']:.1f}`\n"
        f"  🔹 Highest Vol : `{int(max_c_vol_row['strike'])}` — Vol: `{max_c_vol_row['c_vol']/1e5:.2f}L` | LTP: `{max_c_vol_row['c_ltp']:.1f}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📉 *PUT (PE)*\n"
        f"  🔸 Highest OI  : `{int(max_p_oi_row['strike'])}` — OI: `{max_p_oi_row['p_oi']/1e5:.2f}L` | LTP: `{max_p_oi_row['p_ltp']:.1f}`\n"
        f"  🔸 Highest Vol : `{int(max_p_vol_row['strike'])}` — Vol: `{max_p_vol_row['p_vol']/1e5:.2f}L` | LTP: `{max_p_vol_row['p_ltp']:.1f}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"_Auto-alert via GitHub Actions_"
    )
    send_telegram(msg)
    print(f"[{index_name}] Alert sent ✅")

# ──────────────────────────────────────────────
# MAIN — runs both NIFTY and SENSEX
# ──────────────────────────────────────────────
if __name__ == "__main__":
    for name, cfg in INDICES.items():
        try:
            fetch_and_alert(name, cfg)
        except Exception as e:
            print(f"[{name}] Error: {e}")
