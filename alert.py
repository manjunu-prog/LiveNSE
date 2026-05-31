import requests
import time
import io
from datetime import datetime
import pytz
from PIL import Image, ImageDraw, ImageFont

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
DHAN_ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJwX2lwIjoiIiwic19pcCI6IiIsImlzcyI6ImRoYW4iLCJwYXJ0bmVySWQiOiIiLCJleHAiOjE3ODAzMjM5MDIsImlhdCI6MTc4MDIzNzUwMiwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6Imh0dHBzOi8vd2ViLmRoYW4uY28vaW5kZXgvcHJvZmlsZSIsImRoYW5DbGllbnRJZCI6IjExMDgwNjYwOTQifQ.cYSJ99g4qKKvENSIc51UDQLweE5iuR1W0AyF9fTtuSAqJCClYI0s2ng_sqHBOi5t19-S5wyH_qUOnaQAJETtMQ"
DHAN_CLIENT_ID    = "1108066094"
# Shivu account
TELEGRAM_TOKEN_1   = "8584181321:AAHBBTFlhGPs-mBgbRHXLkJME9FaqJh5ofE"
TELEGRAM_CHAT_ID_1 = "653488319"

# Manju account
TELEGRAM_TOKEN_2   = "7851529826:AAHfyHVrVZi5iQubljaNgde76gPhr8pxql4"
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
def build_table_image(index_name, ltp, atm, expiry, pcr, rows,
                      max_c_delta, max_p_delta, max_c_vol, max_p_vol):
    
    # 1. SETUP DIMENSIONS & FONTS
    # We increase the row height to 110 to prevent text overlap
    ROW_H, TOP_H, PAD = 110, 100, 40
    WIDTH = 1000
    HEIGHT = TOP_H + (len(rows) * ROW_H) + 60
    BAR_MAX_W = 300 

    img = Image.new("RGB", (WIDTH, HEIGHT), (10, 12, 18))
    draw = ImageDraw.Draw(img)

    # Load Fonts (Using your existing get_font helper or TTF paths)
    title_f = get_font(32, bold=True)
    label_f = get_font(22)
    strike_f = get_font(26, bold=True)

    # 2. HEADER
    draw.text((PAD, 25), f"📊 {index_name} | LTP: {ltp:,.0f} | PCR: {pcr:.2f}", fill=(255,255,255), font=title_f)
    draw.text((PAD, 70), f"Expiry: {expiry} | Generated: {datetime.now().strftime('%H:%M IST')}", fill=(150,150,150), font=get_font(16))

    # 3. DRAW DATA ROWS
    # We use abs() for ΔOI scaling to handle potential short covering (negative values)
    max_oi_val = max(abs(max_c_delta), abs(max_p_delta), 1)

    y = TOP_H
    for row in rows:
        strike = int(row["strike"])
        cv, pv = row["c_vol"], row["p_vol"]
        cd, pd = row["c_delta"], row["p_delta"]

        # --- VOLUME BARS (Grey) ---
        cv_w = int((cv / max_c_vol) * BAR_MAX_W)
        pv_w = int((pv / max_p_vol) * BAR_MAX_W)
        draw.rectangle([PAD, y + 25, PAD + cv_w, y + 35], fill=(100, 116, 139))
        draw.rectangle([WIDTH - PAD - pv_w, y + 25, WIDTH - PAD, y + 35], fill=(100, 116, 139))

        # --- OI CHANGE BARS (Red/Green) ---
        cd_w = int((abs(cd) / max_oi_val) * BAR_MAX_W)
        pd_w = int((abs(pd) / max_oi_val) * BAR_MAX_W)
        
        # CE Side Colors (Left)
        ce_color = (239, 68, 68) if cd > 0 else (34, 197, 94)
        # PE Side Colors (Right)
        pe_color = (34, 197, 94) if pd > 0 else (239, 68, 68)

        draw.rectangle([PAD, y + 40, PAD + cd_w, y + 70], fill=ce_color)
        draw.rectangle([WIDTH - PAD - pd_w, y + 40, WIDTH - PAD, y + 70], fill=pe_color)

        # --- STRIKE TEXT (Center) ---
        txt = f" {strike} ATM " if strike == atm else f" {strike} "
        strike_x = WIDTH // 2 - 70
        if strike == atm:
            draw.rectangle([strike_x - 10, y + 20, strike_x + 150, y + 70], outline=(255,255,255), width=3)
        draw.text((strike_x, y + 30), txt, fill=(255,255,255), font=strike_f)

        # --- TEXT LABELS (Positioned ABOVE the bars) ---
        # Call side (Left)
        draw.text((PAD, y), f"V: {fmt(cv)}", fill=(148, 163, 184), font=label_f)
        draw.text((PAD + cd_w + 15, y + 40), f"Δ: {fmt(cd)}", fill=ce_color, font=label_f)
        
        # Put side (Right)
        draw.text((WIDTH - PAD - 180, y), f"V: {fmt(pv)}", fill=(148, 163, 184), font=label_f)
        draw.text((WIDTH - PAD - pd_w - 180, y + 40), f"Δ: {fmt(pd)}", fill=pe_color, font=label_f)

        y += ROW_H

    # 4. SAVE TO BYTES
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()

# ... (ALL YOUR IMPORTS & CONFIG — NO CHANGE)


def build_strikewise_text(index_name, ltp, atm, pcr, rows, step):
    msg_lines = []
    msg_lines.append(f"📊 *{index_name}* | LTP: `{ltp:,.0f}`\n")

    for r in rows:
        strike = int(r["strike"])
        c_vol_raw = r["c_vol"]
        p_vol_raw = r["p_vol"]

        # logic: if call volume > put volume, show Green (Bullish volume)
        # if call volume < put volume, show Red (Bearish volume)
        if c_vol_raw > p_vol_raw:
            icon = "🟢"
        elif p_vol_raw > c_vol_raw:
            icon = "🔴"
        else:
            icon = "⚪"

        # strike label
        if strike == atm:
            strike_txt = f"{icon} {strike} ATM"
        else:
            strike_txt = f"{icon} {strike}"

        def short(v):
            return f"{v/1e5:.1f}L"

        c_vol_str = short(c_vol_raw)
        p_vol_str = short(p_vol_raw)
        
        c_ltp = int(r["c_ltp"])
        p_ltp = int(r["p_ltp"])

        # Layout: [Call Vol / Call LTP]  [Icon Strike]  [Put LTP / Put Vol]
        # Removed Change in OI and Arrows
        line = (
            f"`{c_vol_str}/{c_ltp:<3}`  "
            f"{strike_txt:^14}  "
            f"`{p_ltp:>3}/{p_vol_str}`"
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
