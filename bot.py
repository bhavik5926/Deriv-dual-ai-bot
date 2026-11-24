import os
import time
import json
import threading
import requests
import websocket
from flask import Flask
from collections import deque
from datetime import datetime, timezone, timedelta

# ================= TELEGRAM सेटिंग्स =================
TELEGRAM_BOT_TOKEN = os.environ.get("9256114967:AAFQ4MsUQbb26EGjtZM1H9P1VX5uTFaMzIc")
TELEGRAM_CHAT_ID = "8466057689"
# ====================================================

S = chr(47)
IST = timezone(timedelta(hours=5, minutes=30))
app = Flask(__name__)

@app.route(S)
def home():
    return "Deriv AI Scanner (Over/Under + Matches) is Running 24x7!"

SETTINGS_FILE = "deriv_settings.json"
DEFAULT_SETTINGS = {
    "mode": "BOTH",               # BOTH, OVER_UNDER, या MATCHES
    "ou_stake_pct": 5.0,          # Over/Under में इक्विटी का 5% स्टेक
    "match_stake_pct": 1.5,       # Digit Matches में इक्विटी का 1.5% स्टेक (9x पेआउट के कारण)
    "daily_profit_target": 15.0,  # रोज़ का +15% मुनाफा लक्ष्य
    "daily_loss_limit": 10.0,     # रोज़ की -10% नुकसान सीमा
    "cooldown_sec": 25            # दो ट्रेड्स के बीच न्यूनतम अंतर
}

SYMBOLS = {
    "R_10": "Volatility 10",
    "R_25": "Volatility 25",
    "R_50": "Volatility 50",
    "R_75": "Volatility 75",
    "R_100": "Volatility 100"
}

balance = 100.0
day_start_balance = 100.0
current_day = datetime.now(IST).strftime("%Y-%m-%d")
paused_today = False

digit_history = {sym: deque(maxlen=80) for sym in SYMBOLS}
pending_trades = {}  # {sym: {"type": ..., "pred": ..., "stake": ..., "payout_mult": ...}}
stats = {
    "ou_wins": 0, "ou_losses": 0,
    "m_wins": 0, "m_losses": 0,
    "total_pnl": 0.0
}
last_trade_time = 0
last_update_id = 0

def tg_url(method):
    return "https:" + S + S + "api.telegram.org" + S + "bot" + TELEGRAM_BOT_TOKEN.strip() + S + method

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4)
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            for k, v in DEFAULT_SETTINGS.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        return DEFAULT_SETTINGS.copy()

def save_settings(cfg):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

def send_tg(msg):
    if "YOUR_NEW" in TELEGRAM_BOT_TOKEN:
        print("TG:", msg, flush=True)
        return
    try:
        requests.post(
            tg_url("sendMessage"),
            json={"chat_id": str(TELEGRAM_CHAT_ID).strip(), "text": msg, "parse_mode": "Markdown"},
            timeout=8
        )
    except Exception as e:
        print("TG Err:", e, flush=True)

def check_tg():
    global last_update_id, paused_today
    if "YOUR_NEW" in TELEGRAM_BOT_TOKEN:
        return
    try:
        r = requests.get(tg_url("getUpdates"), params={"offset": last_update_id + 1, "timeout": 1}, timeout=6)
        if r.status_code != 200:
            return
        for item in r.json().get("result", []):
            last_update_id = item["update_id"]
            msg_obj = item.get("message", {})
            text = msg_obj.get("text", "").strip().lower()
            if str(msg_obj.get("chat", {}).get("id", "")) != str(TELEGRAM_CHAT_ID).strip():
                continue

            cfg = load_settings()
            if text in [S + "mode both", S + "both"]:
                cfg["mode"] = "BOTH"
                save_settings(cfg)
                send_tg("🔄 *मोड अपडेट:* अब `OVER/UNDER` और `MATCHES` दोनों चलेंगे।")
            elif text in [S + "mode ou", S + "ou"]:
                cfg["mode"] = "OVER_UNDER"
                save_settings(cfg)
                send_tg("🔄 *मोड अपडेट:* अब केवल `OVER/UNDER` चलेगा।")
            elif text in [S + "mode match", S + "matches"]:
                cfg["mode"] = "MATCHES"
                save_settings(cfg)
                send_tg("🔄 *मोड अपडेट:* अब केवल `DIGIT MATCHES` चलेगा।")
            elif text == S + "resume":
                paused_today = False
                send_tg("▶️ *बॉट फिर से सक्रिय कर दिया गया है।*")
            elif text in [S + "status", S + "start"]:
                ou_tot = stats["ou_wins"] + stats["ou_losses"]
                ou_wr = (stats["ou_wins"] / ou_tot * 100) if ou_tot > 0 else 0.0
                m_tot = stats["m_wins"] + stats["m_losses"]
                m_wr = (stats["m_wins"] / m_tot * 100) if m_tot > 0 else 0.0
                daily_pct = ((balance - day_start_balance) / day_start_balance * 100) if day_start_balance > 0 else 0.0
                send_tg(
                    f"📊 *DERIV AI SCANNER STATUS*\n"
                    f"💎 वर्चुअल बैलेंस: `${balance:.2f}` (`{daily_pct:+.2f}%` आज)\n"
                    f"📈 कुल नेट PnL: `${stats['total_pnl']:+.2f}`\n"
                    f"🎯 Over/Under WR: `{ou_wr:.1f}%` ({stats['ou_wins']}W / {stats['ou_losses']}L)\n"
                    f"🎰 Matches WR: `{m_wr:.1f}%` ({stats['m_wins']}W / {stats['m_losses']}L)\n"
                    f"⚙️ सक्रिय मोड: `{cfg['mode']}`"
                )
    except Exception:
        pass

def extract_last_digit(quote_val, pip_size=2):
    formatted = f"{float(quote_val):.{pip_size}f}"
    return int(formatted[-1])

def analyze_ai_signal(sym, cfg):
    digits = list(digit_history[sym])
    if len(digits) < 45:
        return None

    mode = cfg["mode"]
    recent_30 = digits[-30:]
    last_digit = digits[-1]

    # 1. OVER / UNDER AI स्कैनर (High Probability Over 2 / Under 7)
    if mode in ["BOTH", "OVER_UNDER"]:
        over2_count = sum(1 for d in recent_30 if d > 2)
        under7_count = sum(1 for d in recent_30 if d < 7)

        # अगर पिछले 30 में से 24+ बार (80%+) >2 आया हो और अभी-अभी <=2 आकर रिवर्सल सेट हुआ हो
        if over2_count >= 24 and last_digit <= 2:
            return {
                "type": "OVER",
                "pred": 2,
                "stake_pct": cfg["ou_stake_pct"],
                "payout_mult": 1.38,  # +38% लाभ
                "reason": f"Over-2 Trend ({over2_count}/30) + Dip Reversal ({last_digit})"
            }

        if under7_count >= 24 and last_digit >= 7:
            return {
                "type": "UNDER",
                "pred": 7,
                "stake_pct": cfg["ou_stake_pct"],
                "payout_mult": 1.38,
                "reason": f"Under-7 Trend ({under7_count}/30) + Spike Reversal ({last_digit})"
            }

    # 2. DIGIT MATCHES AI स्कैनर (Cold Digit Due Theory)
    if mode in ["BOTH", "MATCHES"] and len(digits) >= 60:
        recent_40 = digits[-40:]
        for target_d in range(10):
            if target_d not in recent_40:
                # 40 टिक्स से यह अंक गायब है — उच्च वापसी संभावना
                return {
                    "type": "MATCHES",
                    "pred": target_d,
                    "stake_pct": cfg["match_stake_pct"],
                    "payout_mult": 9.0,  # +800% जैकपॉट पेआउट
                    "reason": f"Cold Digit '{target_d}' 40 टिक्स से गायब है!"
                }

    return None

def settle_trade(sym, actual_digit):
    global balance, paused_today
    trade = pending_trades.pop(sym)
    t_type = trade["type"]
    pred = trade["pred"]
    stake = trade["stake"]
    won = False

    if t_type == "OVER" and actual_digit > pred:
        won = True
    elif t_type == "UNDER" and actual_digit < pred:
        won = True
    elif t_type == "MATCHES" and actual_digit == pred:
        won = True

    if won:
        payout = round(stake * trade["payout_mult"], 2)
        profit = round(payout - stake, 2)
        balance = round(balance + payout, 2)
        stats["total_pnl"] = round(stats["total_pnl"] + profit, 2)
        if t_type == "MATCHES":
            stats["m_wins"] += 1
        else:
            stats["ou_wins"] += 1
        send_tg(
            f"✅ *DERIV WIN ({t_type} {pred})*\n"
            f"📌 {SYMBOLS[sym]} | आया अंक: `{actual_digit}`\n"
            f"💵 लाभ: `+${profit:.2f}` | नया बैलेंस: `${balance:.2f}`"
        )
    else:
        stats["total_pnl"] = round(stats["total_pnl"] - stake, 2)
        if t_type == "MATCHES":
            stats["m_losses"] += 1
        else:
            stats["ou_losses"] += 1
        send_tg(
            f"🔻 *DERIV LOSS ({t_type} {pred})*\n"
            f"📌 {SYMBOLS[sym]} | आया अंक: `{actual_digit}`\n"
            f"💸 नुकसान: `-${stake:.2f}` | नया बैलेंस: `${balance:.2f}`"
        )

    # Daily Profit Target / Loss Limit चेक
    cfg = load_settings()
    daily_chg_pct = ((balance - day_start_balance) / day_start_balance) * 100
    if not paused_today:
        if daily_chg_pct >= cfg["daily_profit_target"]:
            paused_today = True
            send_tg(f"🏆 *DAILY TARGET HIT (+{daily_chg_pct:.1f}%)*\nआज का मुनाफा लॉक कर दिया गया है! कल फिर शुरू होगा।")
        elif daily_chg_pct <= -cfg["daily_loss_limit"]:
            paused_today = True
            send_tg(f"🛡️ *DAILY LOSS LIMIT (-{abs(daily_chg_pct):.1f}%)*\nसुरक्षा के लिए आज ट्रेडिंग रोकी गई है।")

def deriv_ws_loop():
    global balance, day_start_balance, current_day, paused_today, last_trade_time
    ws_url = "wss:" + S + S + "://derivws.com" + S + "websockets" + S + "v3?app_id=1089"
    pip_map = {"R_10": 3, "R_25": 3, "R_50": 4, "R_75": 4, "R_100": 2}

    send_tg(
        f"🚀 *Deriv AI Dual Scanner (Over/Under + Matches) चालू!*\n"
        f"💎 शुरुआती बैलेंस: `${balance:.2f}`\n"
        f"📡 स्कैनिंग: `V10, V25, V50, V75, V100`\n"
        f"📋 कमांड्स: `/status`, `/both`, `/ou`, `/matches`"
    )

    while True:
        try:
            ws = websocket.create_connection(ws_url, timeout=15)
            for sym in SYMBOLS:
                ws.send(json.dumps({"ticks": sym, "subscribe": 1}))

            last_tg_check = 0
            while True:
                now_ts = time.time()
                if now_ts - last_tg_check >= 5:
                    check_tg()
                    last_tg_check = now_ts

                # नया दिन चेक करें
