# ========== TELEGRAM BOT ==========
#TOKEN = "8243416633:AAFjISDBXvhqGsM8xvOkWOeQ4eEmhMPlkNU"
#CHAT_ID = "567677761"

# ===========================================
#DHAN_CLIENT_ID = "1108189278"   # Your client ID
#DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzY0NjU3MDU1LCJpYXQiOjE3NjQ1NzA2NTUsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTA4MTg5Mjc4In0.bMdN4ezitTNhpdcw_5QPKHgRL6O_v_J2ARHG4-5b4_2jGf4w2Q7pQPm8sVw3NOaJH-IkHesGLc02Pdtn1Fud6w"  # <-- INSERT YOUR TOKEN HERE

import time
import requests
import pandas as pd
from datetime import datetime
import os

# =============================================================
# TELEGRAM CONFIG (REPLACE TOKEN)
# =============================================================
TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
SYMBOL = "NIFTY"

# =============================================================
# TELEGRAM SEND
# =============================================================
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": msg}
    try:
        requests.get(url, params=params, timeout=10)
    except:
        pass


# =============================================================
# FORMATTING
# =============================================================
def fmt_lakh(x):
    return f"{x/100000:.2f}L"

def fmt_int(x):
    return f"{int(x):,}"

def signed_fmt(value, role):
    if role == "seller":
        return f"-{fmt_lakh(value)}"
    else:
        return f"+{fmt_int(value)}"


# =============================================================
# COLORED BARS USING EMOJIS
# =============================================================
def bar(value, max_value, length=20, color="green"):
    if max_value <= 0:
        filled = 0
    else:
        filled = int((value / max_value) * length)

    filled = max(0, min(length, filled))
    empty = length - filled

    block = "█"
    hollow = "░"

    bar_visual = (block * filled) + (hollow * empty)

    if color == "red":
        return f"🔴 {bar_visual}"
    else:
        return f"🟢 {bar_visual}"


# =============================================================
# FETCH OPTION CHAIN
# =============================================================
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
        data = session.get(url, headers=headers, timeout=10).json()
        records = data["records"]["data"]
    except:
        return None

    final = []
    for item in records:
        strike = item.get("strikePrice")
        ce = item.get("CE")
        pe = item.get("PE")

        ce_oi = ce["openInterest"] if ce else 0
        pe_oi = pe["openInterest"] if pe else 0
        ce_vol = ce["totalTradedVolume"] if ce else 0
        pe_vol = pe["totalTradedVolume"] if pe else 0

        final.append({
            "Strike": strike,
            "CE_OI": int(ce_oi),
            "PE_OI": int(pe_oi),
            "CE_VOL": int(ce_vol),
            "PE_VOL": int(pe_vol),
            "TOTAL_OI": int(ce_oi + pe_oi)
        })

    return pd.DataFrame(final).dropna().sort_values("Strike")


# =============================================================
# CORRECT MAX PAIN
# =============================================================
def calc_max_pain(df):
    strikes = df["Strike"].values
    ce_oi = df.set_index("Strike")["CE_OI"].to_dict()
    pe_oi = df.set_index("Strike")["PE_OI"].to_dict()

    best_strike = None
    best_total = None

    for exp in strikes:
        total = 0
        for k in strikes:
            if exp > k:
                total += (exp - k) * ce_oi[k]
            if exp < k:
                total += (k - exp) * pe_oi[k]

        if best_total is None or total < best_total:
            best_total = total
            best_strike = exp

    return int(best_strike)


# =============================================================
# SUPPORT & RESISTANCE
# =============================================================
def support_resistance(df):
    supports = df.sort_values("PE_OI", ascending=False).head(2)["Strike"].tolist()
    resist = df.sort_values("CE_OI", ascending=False).head(2)["Strike"].tolist()
    return supports, resist


# =============================================================
# MAIN LOOP
# =============================================================
print("🚀 Market Summary Bot Running...")

previous_bias = None

while True:
    try:
        df = get_option_chain(SYMBOL)
        if df is None or df.empty:
            print("Retrying...")
            time.sleep(20)
            continue

        # Totals
        total_ce_oi = df["CE_OI"].sum()
        total_pe_oi = df["PE_OI"].sum()
        total_ce_vol = df["CE_VOL"].sum()
        total_pe_vol = df["PE_VOL"].sum()

        # Sellers (writers)
        if total_ce_oi > total_pe_oi:
            seller_side = "CE"
            seller_label = "CE Sellers"
        else:
            seller_side = "PE"
            seller_label = "PE Sellers"

        # Buyers (premium buyers)
        if total_ce_vol > total_pe_vol:
            buyer_side = "CE"
            buyer_label = "CE Buyers"
        else:
            buyer_side = "PE"
            buyer_label = "PE Buyers"

        # Market direction
        if total_pe_oi > total_ce_oi:
            direction = "📈 Bullish"
        elif total_ce_oi > total_pe_oi:
            direction = "📉 Bearish"
        else:
            direction = "⚪ Neutral"

        # Max Pain / Support / Resistance
        max_pain = calc_max_pain(df)
        supports, resistances = support_resistance(df)

        # PCR
        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0

        # Heavy Sellers
        sellers = df.sort_values(f"{seller_side}_OI", ascending=False).head(4)
        max_seller = sellers[f"{seller_side}_OI"].max()

        seller_lines = []
        for _, r in sellers.iterrows():
            strike = int(r["Strike"])
            oi = int(r[f"{seller_side}_OI"])
            seller_lines.append(
                f"{strike}  {bar(oi, max_seller, color='red')}  {signed_fmt(oi,'seller')}"
            )

        # Heavy Buyers
        buyers = df.sort_values(f"{buyer_side}_VOL", ascending=False).head(4)
        max_buy = buyers[f"{buyer_side}_VOL"].max()

        buyer_lines = []
        for _, r in buyers.iterrows():
            strike = int(r["Strike"])
            vol = int(r[f"{buyer_side}_VOL"])
            buyer_lines.append(
                f"{strike}  {bar(vol, max_buy, color='green')}  {signed_fmt(vol,'buyer')}"
            )

        # Build Telegram message
        msg = (
            f"🔥 {seller_label} (writers)\n\n" +
            "\n".join(seller_lines) +
            "\n\n🟢 {buyer_label} (premium buyers)\n\n".replace("{buyer_label}", buyer_label) +
            "\n".join(buyer_lines) +
            "\n\n-----------------------------------\n"
            f"📌 MARKET SUMMARY\n"
            f"-----------------------------------\n"
            f"Direction  : {direction}\n"
            f"Sellers    : {seller_label}\n"
            f"Buyers     : {buyer_label}\n"
            f"PCR        : {pcr}\n"
            f"Max Pain   : {max_pain}\n"
            f"Support    : {supports[0]}, {supports[1]}\n"
            f"Resistance : {resistances[0]}, {resistances[1]}\n"
            f"-----------------------------------"
        )

        send_telegram(msg)

        # Trend change alert
        bias_now = "CE" if total_ce_oi > total_pe_oi else "PE"

        if previous_bias and bias_now != previous_bias:
            send_telegram(f"⚠️ Trend Change Alert!\nPrev: {previous_bias}\nNow: {bias_now}")

        previous_bias = bias_now

        time.sleep(180)

    except Exception as e:
        print("Error:", e)
        time.sleep(20)
