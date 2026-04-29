import requests
import time
import io
from datetime import datetime
import pytz
from PIL import Image, ImageDraw, ImageFont

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3Nzc1NzYxNjMsImlhdCI6MTc3NzQ4OTc2MywidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd2ViLmRoYW4uY28vaW5kZXgvcHJvZmlsZSIsImRoYW5DbGllbnRJZCI6IjExMDgwNjYwOTQifQ.A96WW6-wFIhEdiBHi1R3Wd9ds9KRqPWmSfYbSKvszeMWZBN3a_FPM6ubw2WPbcGcn4eag9_u9rC7_L7OflkjCw"
DHAN_CLIENT_ID    = "1108066094"
# Shivu account
TELEGRAM_TOKEN_1   = "8584181321:AAHBBTFlhGPs-mBgbRHXLkJME9FaqJh5ofE"
TELEGRAM_CHAT_ID_1 = "653488319"

# Manju account
TELEGRAM_TOKEN_2   = "8243416633:AAFjISDBXvhqGsM8xvOkWOeQ4eEmhMPlkNU"
TELEGRAM_CHAT_ID_2 = "567677761"

TELEGRAM_ACCOUNTS = [
    {"token": TELEGRAM_TOKEN_1, "chat_id": TELEGRAM_CHAT_ID_1, "name": "Shivu"},
    {"token": TELEGRAM_TOKEN_2, "chat_id": TELEGRAM_CHAT_ID_2, "name": "Manju"},
]

API_BASE        = "https://api.dhan.co/v2"
OPTIONCHAIN_URL = f"{API_BASE}/optionchain"
EXPIRY_LIST_URL = f"{API_BASE}/optionchain/expirylist"

INDICES = {
    "NIFTY":  {"Scrip": 13, "Segments": ["IDX_I", "NSE_FNO"], "step": 50},
    "SENSEX": {"Scrip": 1,  "Segments": ["BSE_FNO", "IDX_I"], "step": 100},
}

ATM_RANGE = 5  # ATM ± 5 strikes

# ──────────────────────────────────────────────
# COLOURS  (matching your dashboard)
# ──────────────────────────────────────────────
BG          = (10,  12,  18)    # #0a0c12
HEADER_BG   = (28,  34,  48)    # #1c2230
ROW_BG      = (10,  12,  18)
ROW_ALT     = (16,  20,  30)
ATM_BG      = (255, 255, 255)
ATM_FG      = (0,   0,   0)
STRIKE_BG   = (20,  24,  36)
CYAN_BG     = (0,   188, 212)   # max CE highlight
PINK_BG     = (233, 30,  99)    # max PE highlight
TEXT_WHITE  = (255, 255, 255)
TEXT_GREY   = (148, 163, 184)
TEXT_BLACK  = (0,   0,   0)


# ──────────────────────────────────────────────
# FONT  — use default PIL bitmap font (no file needed)
# ──────────────────────────────────────────────
def get_font(size=15, bold=False):
    try:
        path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold \
               else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────
def _headers():
    return {
        "access-token": DHAN_ACCESS_TOKEN,
        "client-id": DHAN_CLIENT_ID,
        "Content-Type": "application/json"
    }

def fmt(val):
    return f"{val/1e5:.2f}L"

def send_telegram_text(msg):
    for acc in TELEGRAM_ACCOUNTS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{acc['token']}/sendMessage",
                json={"chat_id": acc["chat_id"], "text": msg, "parse_mode": "Markdown"},
                timeout=10
            )
            print(f"  ✅ Text sent to {acc['name']}")
        except Exception as e:
            print(f"  ❌ Failed to send text to {acc['name']}: {e}")

def send_telegram_image(img_bytes, caption=""):
    for acc in TELEGRAM_ACCOUNTS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{acc['token']}/sendPhoto",
                data={"chat_id": acc["chat_id"], "caption": caption, "parse_mode": "Markdown"},
                files={"photo": ("table.png", img_bytes, "image/png")},
                timeout=15
            )
            print(f"  ✅ Image sent to {acc['name']}")
        except Exception as e:
            print(f"  ❌ Failed to send image to {acc['name']}: {e}")

# ──────────────────────────────────────────────
# IMAGE TABLE GENERATOR
# ──────────────────────────────────────────────
def build_table_image(index_name, ltp, atm, pcr, df, step):
    try:
        # Filter ATM ±5 and sort
        df = df[(df["STRIKE"] >= atm - step*5) & (df["STRIKE"] <= atm + step*5)]
        df = df.sort_values("STRIKE", ascending=False)

        # Increase height to accommodate dual bars (Volume + OI Change)
        width, height = 850, 80 + len(df)*60 + 40
        img = Image.new("RGB", (width, height), (10, 12, 18)) # Darker background
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default()

        # Header
        draw.text((20, 15), f"🚀 {index_name} DUAL ANALYSIS | LTP: {ltp:,.0f} | PCR: {pcr:.2f}", fill=(255,255,255), font=font)
        draw.text((20, 35), "LEFT: CALL (Vol/ΔOI) | RIGHT: PUT (Vol/ΔOI)", fill=(150, 150, 150), font=font)

        # Normalize scaling
        max_vol = max(df["_cv"].max(), df["_pv"].max(), 1)
        max_oi  = max(df["_cd"].abs().max(), df["_pd"].abs().max(), 1) # Use absolute for ΔOI scaling

        y = 75
        bar_max_w = 220

        for _, r in df.iterrows():
            strike = int(r["STRIKE"])
            # Data points
            cv, pv = r["_cv"], r["_pv"]
            cd, pd = r["_cd"], r["_pd"]

            # --- 1. VOLUME BARS (Top thin bar) ---
            cv_w = int((cv / max_vol) * bar_max_w)
            pv_w = int((pv / max_vol) * bar_max_w)
            # CE Vol (Grey)
            draw.rectangle([20, y, 20 + cv_w, y + 8], fill=(100, 116, 139))
            # PE Vol (Grey)
            draw.rectangle([width - 20 - pv_w, y, width - 20, y + 8], fill=(100, 116, 139))

            # --- 2. OI CHANGE BARS (Bottom thick bar) ---
            cd_w = int((abs(cd) / max_oi) * bar_max_w)
            pd_w = int((abs(pd) / max_oi) * bar_max_w)
            
            # CE ΔOI Bar Color: Red if positive (selling), Green if negative (covering)
            ce_color = (239, 68, 68) if cd > 0 else (34, 197, 94)
            draw.rectangle([20, y + 12, 20 + cd_w, y + 25], fill=ce_color)
            
            # PE ΔOI Bar Color: Green if positive (selling/support), Red if negative
            pe_color = (34, 197, 94) if pd > 0 else (239, 68, 68)
            draw.rectangle([width - 20 - pd_w, y + 12, width - 20, y + 25], fill=pe_color)

            # --- 3. STRIKE TEXT (Center) ---
            strike_color = (255, 255, 255)
            txt = f"{strike} ATM" if strike == atm else str(strike)
            if strike == atm:
                draw.rectangle([width//2 - 50, y, width//2 + 50, y + 25], outline=(255,255,255))
            
            draw.text((width//2 - 30, y + 5), txt, fill=strike_color, font=font)

            # --- 4. VALUES ---
            # Volume labels
            draw.text((20 + cv_w + 5, y), f"V:{cv/1e5:.1f}L", fill=(148, 163, 184), font=font)
            draw.text((width - 20 - pv_w - 70, y), f"V:{pv/1e5:.1f}L", fill=(148, 163, 184), font=font)
            # OI Delta labels
            draw.text((20 + cd_w + 5, y + 12), f"Δ:{cd/1e5:.1f}L", fill=ce_color, font=font)
            draw.text((width - 20 - pd_w - 70, y + 12), f"Δ:{pd/1e5:.1f}L", fill=pe_color, font=font)

            y += 55 # Space for next strike

        # Save and Send
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                      data={"chat_id": TELEGRAM_CHAT_ID},
                      files={"photo": ("analysis.png", buf, "image/png")}, timeout=15)
    except Exception as e:
        print(f"Error generating dual chart: {e}")
# ... (ALL YOUR IMPORTS & CONFIG — NO CHANGE)


def build_strikewise_text(index_name, ltp, atm, pcr, rows, step):
    msg_lines = []
    msg_lines.append(f"📊 *{index_name}* | LTP: `{ltp:,.0f}`\n")

    for r in rows:
        strike = int(r["strike"])
        c_delta = r["c_delta"]
        p_delta = r["p_delta"]

        # sentiment
        if c_delta > p_delta:
            icon = "🔴"
        elif p_delta > c_delta:
            icon = "🟢"
        else:
            icon = "⚪"

        # strike label
        if strike == atm:
            strike_txt = f"{icon} {strike} ATM"
        else:
            strike_txt = f"{icon} {strike}"

        def short(v):
            return f"{v/1e5:.1f}"

        c_vol = short(r["c_vol"])
        p_vol = short(r["p_vol"])
        c_oi  = short(abs(c_delta))
        p_oi  = short(abs(p_delta))

        c_ltp = int(r["c_ltp"])
        p_ltp = int(r["p_ltp"])

        c_arrow = "▲" if c_delta >= 0 else "▼"
        p_arrow = "▲" if p_delta >= 0 else "▼"

        line = (
            f"`{c_vol}/{c_oi}{c_arrow}/{c_ltp:<3}`  "
            f"{strike_txt:^14}  "
            f"`{p_ltp:>3}/{p_oi}{p_arrow}/{p_vol}`"
        )

        msg_lines.append(line)

    msg_lines.append(f"\nPCR: `{pcr:.2f}`")

    return "\n".join(msg_lines)

def build_bar_image(index_name, ltp, atm, pcr, rows):
    width  = 900
    row_h  = 36
    top_h  = 60
    height = top_h + len(rows)*row_h + 40

    img  = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    font      = get_font(14)
    font_bold = get_font(14, bold=True)

    # Title
    draw.rectangle([0, 0, width, top_h], fill=HEADER_BG)
    draw.text((10, 10),
              f"{index_name} | LTP: {ltp:,.0f} | ATM: {int(atm)} | PCR: {pcr:.2f}",
              font=font_bold, fill=TEXT_WHITE)

    max_vol = max(max(r["c_vol"], r["p_vol"]) for r in rows) or 1

    y = top_h

    for r in rows:
        strike  = int(r["strike"])
        c_vol   = r["c_vol"]
        p_vol   = r["p_vol"]
        c_delta = r["c_delta"]
        p_delta = r["p_delta"]

        if c_delta > p_delta:
            color = (255, 80, 80)
        elif p_delta > c_delta:
            color = (80, 255, 120)
        else:
            color = (200, 200, 200)

        # bars
        c_w = int((c_vol / max_vol) * 300)
        p_w = int((p_vol / max_vol) * 300)

        draw.rectangle([10, y+8, 10 + c_w, y+20], fill=(120,170,255))
        draw.rectangle([width-10-p_w, y+8, width-10, y+20], fill=(255,120,160))

        txt = f"{strike} ATM" if strike == atm else f"{strike}"
        draw.text((width//2 - 40, y+5), txt, fill=color, font=font_bold)

        draw.text((10 + c_w + 5, y+5), f"{c_vol/1e5:.1f}L", fill=TEXT_WHITE, font=font)
        draw.text((width - 10 - p_w - 60, y+5), f"{p_vol/1e5:.1f}L", fill=TEXT_WHITE, font=font)

        y += row_h

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


# ──────────────────────────────────────────────
# FETCH & ALERT
# ──────────────────────────────────────────────
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

    # Step 3: Build ATM ± 5 rows
    atm  = round(ltp / cfg["step"]) * cfg["step"]
    rows = []
    for strike_s, legs in oc_map.items():
        strike_f = float(strike_s)
        if abs(strike_f - atm) <= (cfg["step"] * ATM_RANGE):
            ce = legs.get("ce", {})
            pe = legs.get("pe", {})
            c_delta = int(ce.get("oi", 0)) - int(ce.get("previous_oi") or 0)
            p_delta = int(pe.get("oi", 0)) - int(pe.get("previous_oi") or 0)
            rows.append({
                "strike":  strike_f,
                "c_ltp":   float(ce.get("last_price", 0)),
                "p_ltp":   float(pe.get("last_price", 0)),
                "c_oi":    int(ce.get("oi", 0)),
                "p_oi":    int(pe.get("oi", 0)),
                "c_delta": c_delta,
                "p_delta": p_delta,
                "c_vol":   int(ce.get("volume", 0)),
                "p_vol":   int(pe.get("volume", 0)),
            })

    if not rows:
        return

    rows = sorted(rows, key=lambda x: x["strike"], reverse=True)

    # Step 4: Identify max values for highlighting (no change)
    max_c_delta = max(r["c_delta"] for r in rows)
    max_p_delta = max(r["p_delta"] for r in rows)
    max_c_vol   = max(r["c_vol"]   for r in rows)
    max_p_vol   = max(r["p_vol"]   for r in rows)

    total_c_oi = sum(r["c_oi"] for r in rows)
    total_p_oi = sum(r["p_oi"] for r in rows)
    pcr        = total_p_oi / total_c_oi if total_c_oi else 0

    # Build new image
    bar_img = build_bar_image(index_name, ltp, atm, pcr, rows)

    # ──────────────────────────────────────────────
    # ✅ NEW: SMART TELEGRAM TEXT (OI SENTIMENT)
    # ──────────────────────────────────────────────
    msg_lines = []
    msg_lines.append(f"📊 *{index_name}*  |  LTP: `{ltp:,.0f}`\n")

    for r in rows:
        strike = int(r["strike"])
        c_delta = r["c_delta"]
        p_delta = r["p_delta"]

        # Sentiment logic
        if c_delta > p_delta:
            icon = "🔴"   # Bearish
        elif p_delta > c_delta:
            icon = "🟢"   # Bullish
        else:
            icon = "⚪"

        # Strike formatting
        if strike == atm:
            strike_txt = f"{icon} {strike} ATM"
        else:
            diff = int((strike - atm) / cfg["step"])
            sign = f"+{diff}" if diff > 0 else f"{diff}"
            strike_txt = f"{icon} {strike} {sign}"

        left  = f"{r['c_vol']:,}"
        right = f"{r['p_vol']:,}"

        line = f"`{left:<10}`  {strike_txt:^14}  `{right:>10}`"
        msg_lines.append(line)

    msg_lines.append(f"\nPCR: `{pcr:.2f}`")

    final_msg = "\n".join(msg_lines)

    # ✅ Send BOTH image + new text
    # keep image clean
    # Build new text
    text_msg = build_strikewise_text(index_name, ltp, atm, pcr, rows, cfg["step"])

    # Build new image
    bar_img = build_bar_image(index_name, ltp, atm, pcr, rows)

    # Send both
    send_telegram_text(text_msg)
    send_telegram_image(bar_img, caption="")

    print(f"[{index_name}] Image + Smart text alert sent ✅")

# ──────────────────────────────────────────────
# MARKET HOURS CHECK
# ──────────────────────────────────────────────
def is_market_open():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    if now.weekday() >= 5:
        return False, f"Weekend ({now.strftime('%A')})"
    open_time  = now.replace(hour=9,  minute=0, second=0, microsecond=0)
    close_time = now.replace(hour=16, minute=0, second=0, microsecond=0)
    if open_time <= now <= close_time:
        return True, now.strftime('%H:%M IST')
    return False, f"Market closed — {now.strftime('%H:%M IST')}"

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    open_status, reason = is_market_open()
    if not open_status:
        print(f"⏸ Skipping — {reason}")
    else:
        print(f"✅ Market is open — {reason}")
        for name, cfg in INDICES.items():
            try:
                fetch_and_alert(name, cfg)
            except Exception as e:
                print(f"[{name}] Error: {e}")
