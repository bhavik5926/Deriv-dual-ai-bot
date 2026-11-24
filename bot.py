import os
import time
import json
import threading
import requests
import websocket
from flask import Flask
from collections import deque
from datetime import datetime, timezone, timedelta

TELEGRAM_BOT_TOKEN = os.environ.get("DERIV_TG_TOKEN") or "9229203140:AAHTePWnAsVLyGza-cwMcNqtQ6Vo9PDIjxw"
TELEGRAM_CHAT_ID = "8466057689"

S = chr(47)
IST = timezone(timedelta(hours=5, minutes=30))
app = Flask(__name__)

@app.route(S)
def home():
    return "Deriv AI Scanner Pro is Live 24x7!"

cfg = {
    "mode": "BOTH",
    "ou_stake_pct": 5.0,
    "match_stake_pct": 1.5,
    "daily_profit_target": 15.0,
    "daily_loss_limit": 10.0,
    "cooldown_sec": 25
}

SYMBOLS = {"R_10": "V10", "R_25": "V25", "R_50": "V50", "R_75": "V75", "R_100": "V100"}
PIP_MAP = {"R_10": 3, "R_25": 3, "R_50": 4, "R_75": 4, "R_100": 2}

balance = 100.0
day_start = 100.0
cur_day = datetime.now(IST).strftime("%Y-%m-%d")
paused = False

history = {k: deque(maxlen=80) for k in SYMBOLS}
open_trade = {}
stats = {"ou_w": 0, "ou_l": 0, "m_w": 0, "m_l": 0, "pnl": 0.0}
last_trade_ts = 0
last_uid = 0

def tg_api(m):
    t_host = "".join([chr(97), chr(112), chr(105), chr(46), chr(116), chr(101), chr(108), chr(101), chr(103), chr(114), chr(97), chr(109), chr(46), chr(111), chr(114), chr(103)])
    return "https:" + S + S + t_host + S + "bot" + str(TELEGRAM_BOT_TOKEN).strip() + S + m

def deriv_ws_url():
    d_host = "".join([chr(103), chr(114), chr(101), chr(101), chr(110), chr(46), chr(100), chr(101), chr(114), chr(105), chr(118), chr(119), chr(115), chr(46), chr(99), chr(111), chr(109)])
    return "wss:" + S + S + d_host + S + "websockets" + S + "v3?app_id=1089&l=EN&brand=deriv"

def send_tg(msg):
    tok = str(TELEGRAM_BOT_TOKEN or "")
    if not tok or "YOUR_NEW" in tok:
        return
    try:
        r = requests.post(tg_api("sendMessage"), json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=8)
        if r.status_code != 200:
            print("TG Send Err:", r.status_code, r.text[:80], flush=True)
    except Exception as e:
        print("TG Post Err:", e, flush=True)

def tg_worker():
    global last_uid, paused
    time.sleep(2)
    send_tg("🚀 *Deriv AI Dual Scanner चालू!* (`V10–V100`)\nकमांड्स: `/status`, `/both`, `/ou`, `/matches`")
    while True:
        try:
            tok = str(TELEGRAM_BOT_TOKEN or "")
            if tok and "YOUR_NEW" not in tok:
                res = requests.get(tg_api("getUpdates"), params={"offset": last_uid + 1, "timeout": 10}, timeout=15).json()
                for item in res.get("result", []):
                    last_uid = item["update_id"]
                    m = item.get("message", {})
                    txt = m.get("text", "").strip().lower()
                    if str(m.get("chat", {}).get("id", "")) != TELEGRAM_CHAT_ID:
                        continue
                    if txt in [S + "both", S + "mode both"]:
                        cfg["mode"] = "BOTH"
                        send_tg("🔄 मोड: `BOTH` (Over/Under + Matches)")
                    elif txt in [S + "ou", S + "mode ou"]:
                        cfg["mode"] = "OVER_UNDER"
                        send_tg("🔄 मोड: `OVER_UNDER`")
                    elif txt in [S + "matches", S + "mode match"]:
                        cfg["mode"] = "MATCHES"
                        send_tg("🔄 मोड: `MATCHES`")
                    elif txt == S + "resume":
                        paused = False
                        send_tg("▶️ ट्रेडिंग फिर से चालू!")
                    elif txt in [S + "status", S + "start"]:
                        ou_t = stats["ou_w"] + stats["ou_l"]
                        ou_wr = (stats["ou_w"] / ou_t * 100) if ou_t else 0.0
                        m_t = stats["m_w"] + stats["m_l"]
                        m_wr = (stats["m_w"] / m_t * 100) if m_t else 0.0
                        send_tg(
                            f"📊 *DERIV AI STATUS*\n"
                            f"💎 बैलेंस: `${balance:.2f}` | PnL: `${stats['pnl']:+.2f}`\n"
                            f"🎯 O/U WR: `{ou_wr:.1f}%` ({stats['ou_w']}W/{stats['ou_l']}L)\n"
                            f"🎰 Match WR: `{m_wr:.1f}%` ({stats['m_w']}W/{stats['m_l']}L)\n"
                            f"⚙️ मोड: `{cfg['mode']}`"
                        )
        except Exception:
            time.sleep(3)

def get_signal(sym):
    d = list(history[sym])
    if len(d) < 45:
        return None
    r30 = d[-30:]
    last_d = d[-1]
    if cfg["mode"] in ["BOTH", "OVER_UNDER"]:
        if sum(1 for x in r30 if x > 2) >= 24 and last_d <= 2:
            return {"t": "OVER", "p": 2, "sp": cfg["ou_stake_pct"], "mult": 1.38, "why": f"Over-2 Trend + Dip ({last_d})"}
        if sum(1 for x in r30 if x < 7) >= 24 and last_d >= 7:
            return {"t": "UNDER", "p": 7, "sp": cfg["ou_stake_pct"], "mult": 1.38, "why": f"Under-7 Trend + Spike ({last_d})"}
    if cfg["mode"] in ["BOTH", "MATCHES"] and len(d) >= 60:
        r40 = d[-40:]
        for target in range(10):
            if target not in r40:
                return {"t": "MATCHES", "p": target, "sp": cfg["match_stake_pct"], "mult": 9.0, "why": f"Digit {target} 40 टिक्स से गायब"}
    return None

def settle(sym, digit):
    global balance, paused
    t = open_trade.pop(sym)
    won = (t["t"] == "OVER" and digit > t["p"]) or (t["t"] == "UNDER" and digit < t["p"]) or (t["t"] == "MATCHES" and digit == t["p"])
    if won:
        win_amt = round(t["stake"] * t["mult"], 2)
        prof = round(win_amt - t["stake"], 2)
        balance = round(balance + win_amt, 2)
        stats["pnl"] = round(stats["pnl"] + prof, 2)
        if t["t"] == "MATCHES":
            stats["m_w"] += 1
        else:
            stats["ou_w"] += 1
        send_tg(f"✅ *WIN ({t['t']} {t['p']})* | {SYMBOLS[sym]} | अंक: `{digit}` | `+${prof:.2f}` | बैलेंस: `${balance:.2f}`")
    else:
        stats["pnl"] = round(stats["pnl"] - t["stake"], 2)
        if t["t"] == "MATCHES":
            stats["m_l"] += 1
        else:
            stats["ou_l"] += 1
        send_tg(f"🔻 *LOSS ({t['t']} {t['p']})* | {SYMBOLS[sym]} | अंक: `{digit}` | `-${t['stake']:.2f}` | बैलेंस: `${balance:.2f}`")

    chg = ((balance - day_start) / day_start) * 100
    if not paused and chg >= cfg["daily_profit_target"]:
        paused = True
        send_tg(f"🏆 *DAILY TARGET (+{chg:.1f}%)* — आज का लाभ लॉक!")
    elif not paused and chg <= -cfg["daily_loss_limit"]:
        paused = True
        send_tg(f"🛡️ *DAILY LOSS LIMIT (-{abs(chg):.1f}%)* — आज ट्रेडिंग रुकी!")

def handle_tick(data):
    global balance, last_trade_ts
    if "tick" not in data:
        return
    tk = data["tick"]
    sym = tk["symbol"]
    pip = tk.get("pip_size", PIP_MAP.get(sym, 2))
    digit = int(f"{float(tk['quote']):.{pip}f}"[-1])

    if sym in open_trade:
        settle(sym, digit)
    history[sym].append(digit)

    now = time.time()
    if not paused and not open_trade and (now - last_trade_ts) >= cfg["cooldown_sec"]:
        sig = get_signal(sym)
        if sig:
            stake = round(balance * (sig["sp"] / 100.0), 2)
            if stake >= 0.50 and balance >= stake:
                balance = round(balance - stake, 2)
                last_trade_ts = now
                open_trade[sym] = {"t": sig["t"], "p": sig["p"], "stake": stake, "mult": sig["mult"]}
                send_tg(f"⚡ *SIGNAL: {sig['t']} {sig['p']}* | {SYMBOLS[sym]}\n💲 स्टेक: `${stake:.2f}` | कारण: {sig['why']}")

def ws_worker():
    global day_start, cur_day, paused
    url = deriv_ws_url()
    orig = "https:" + S + S + "://deriv.com"
    while True:
        try:
            ws = websocket.create_connection(url, origin=orig, timeout=15)
            print("Connected to Deriv WS!", flush=True)
            for s in SYMBOLS:
                ws.send(json.dumps({"ticks": s, "subscribe": 1}))
            while True:
                today = datetime.now(IST).strftime("%Y-%m-%d")
                if today != cur_day:
                    cur_day = today
                    day_start = balance
                    paused = False
                handle_tick(json.loads(ws.recv()))
        except Exception as e:
            print("WS Retry:", e, flush=True)
            time.sleep(5)

threading.Thread(target=tg_worker, daemon=True).start()
threading.Thread(target=ws_worker, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
