import os
import time
import json
import threading
import requests
from flask import Flask
from datetime import datetime, timezone, timedelta

# ================= TELEGRAM सेटिंग्स =================
TELEGRAM_BOT_TOKEN = "9229203140:AAHtePWnAsVLyGza-cwMcNqtQ6Vo9PDIjxw" + "1"

TELEGRAM_CHAT_ID = "8466057689"
# ====================================================

S = chr(47)
app = Flask(__name__)

@app.route(S)
def home():
    return "Binance 5-Coin AI Pro Bot is Running 24x7!"

cfg = {
    "tp_pct": 3.0,
    "sl_pct": -1.5,
    "be_trigger": 1.5,
    "risk_pct": 20.0,
    "max_trades": 3,
    "min_score": 70
}

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
balance = 100.0
positions = {}
journal = []
stats = {"wins": 0, "losses": 0, "pnl": 0.0}
last_uid = 0

def tg_url(m):
    host = "".join([chr(97), chr(112), chr(105), chr(46), chr(116), chr(101), chr(108), chr(101), chr(103), chr(114), chr(97), chr(109), chr(46), chr(111), chr(114), chr(103)])
    return "https:" + S + S + host + S + "bot" + str(TELEGRAM_BOT_TOKEN).strip() + S + m

def binance_url(path):
    host = "".join([chr(97), chr(112), chr(105), chr(46), chr(98), chr(105), chr(110), chr(97), chr(110), chr(99), chr(101), chr(46), chr(99), chr(111), chr(109)])
    return "https:" + S + S + host + S + "api" + S + "v3" + S + path

def send_tg(msg):
    tok = str(TELEGRAM_BOT_TOKEN or "")
    if not tok or "YOUR_TELEGRAM" in tok:
        print("TOKEN MISSING IN CODE!", flush=True)
        return
    try:
        r = requests.post(tg_url("sendMessage"), json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=8)
        if r.status_code != 200:
            print("TG Send Err:", r.status_code, r.text[:100], flush=True)
    except Exception as e:
        print("TG Post Err:", e, flush=True)

def get_equity(prices=None):
    val = 0.0
    for sym, p in positions.items():
        cp = prices.get(sym, p["entry"]) if prices else p["entry"]
        val += p["qty"] * cp
    return balance + val

def tg_worker():
    global last_uid
    time.sleep(2)
    send_tg(
        f"🚀 *Binance 5-Coin AI Pro Bot चालू!*\n"
        f"🪙 स्कैनिंग: `BTC, ETH, SOL, BNB, XRP`\n"
        f"💎 इक्विटी: `${balance:.2f}` | 🎯 TP: `+{cfg['tp_pct']}%` | 🛑 SL: `{cfg['sl_pct']}%`\n"
        f"📋 कमांड्स: `/status`, `/journal`, `/tp 3`, `/sl 1.5`"
    )
    while True:
        try:
            tok = str(TELEGRAM_BOT_TOKEN or "")
            if tok and "YOUR_TELEGRAM" not in tok:
                r = requests.get(tg_url("getUpdates"), params={"offset": last_uid + 1, "timeout": 10}, timeout=15).json()
                for item in r.get("result", []):
                    last_uid = item["update_id"]
                    m = item.get("message", {})
                    txt = m.get("text", "").strip().lower()
                    if str(m.get("chat", {}).get("id", "")) != TELEGRAM_CHAT_ID:
                        continue
                    if txt.startswith(S + "tp "):
                        cfg["tp_pct"] = abs(float(txt.split().pop()))
                        send_tg(f"✅ *Binance TP अपडेट:* `+{cfg['tp_pct']}%`")
                    elif txt.startswith(S + "sl "):
                        cfg["sl_pct"] = -abs(float(txt.split().pop()))
                        send_tg(f"🛑 *Binance SL अपडेट:* `{cfg['sl_pct']}%`")
                    elif txt == S + "journal":
                        if not journal:
                            send_tg("📓 अभी तक कोई Binance ट्रेड बंद नहीं हुआ है।")
                        else:
                            lines = ["📓 *BINANCE TRADE JOURNAL*"]
                            for t in reversed(journal[-6:]):
                                ic = "✅" if t["pnl"] >= 0 else "🔻"
                                lines.append(f"{ic} *{t['sym']}* | `{t['pnl']:+.2f}$` (`{t['pct']:+.2f}%`) | {t['why']}")
                            send_tg("\n".join(lines))
                    elif txt in [S + "status", S + "start"]:
                        tot = stats["wins"] + stats["losses"]
                        wr = (stats["wins"] / tot * 100) if tot else 0.0
                        eq = get_equity()
                        send_tg(
                            f"📊 *BINANCE AI PRO STATUS*\n"
                            f"💎 कुल इक्विटी: `${eq:.2f}` (कैश: `${balance:.2f}`)\n"
                            f"📈 नेट PnL: `${stats['pnl']:+.2f}` | 🎯 Win Rate: `{wr:.1f}%` ({stats['wins']}W/{stats['losses']}L)\n"
                            f"⚙️ TP: `+{cfg['tp_pct']}%` | SL: `{cfg['sl_pct']}%` | 🔒 BE: `+{cfg['be_trigger']}%`\n"
                            f"📂 ओपन कॉइन्स: `{len(positions)}/{cfg['max_trades']}`"
                        )
        except Exception as e:
            print("TG Loop Err:", e, flush=True)
            time.sleep(3)

def analyze_coin(sym):
    try:
        resp = requests.get(binance_url("klines"), params={"symbol": sym, "interval": "5m", "limit": 30}, timeout=10)
        if resp.status_code != 200:
            print("Binance HTTP Err:", resp.status_code, resp.text[:80], flush=True)
            return 0, None, ""
        r = resp.json()
        closes = [float(k.pop(4)) for k in [list(x) for x in r]]
        vols = [float(k.pop(4)) for k in [list(x) for x in r]]
        curr_p = closes[-1]

        gains, losses = [], []
        for i in range(1, 15):
            diff = closes[-i] - closes[-i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_g = sum(gains) / 14.0
        avg_l = sum(losses) / 14.0
        rs = (avg_g / avg_l) if avg_l > 0 else 100
        rsi = 100 - (100 / (1 + rs))

        ema9 = sum(closes[-9:]) / 9.0
        ema21 = sum(closes[-21:]) / 21.0

        avg_vol = sum(vols[-10:-1]) / 9.0
        curr_vol = vols[-1]

        score = 0
        reasons = []

        if 32 <= rsi <= 48:
            score += 35
            reasons.append(f"RSI Bounce ({rsi:.1f})")
        elif 48 < rsi <= 62:
            score += 25
            reasons.append(f"Healthy RSI ({rsi:.1f})")

        if curr_p > ema9 > ema21:
            score += 35
            reasons.append("Bullish EMA")
        elif curr_p > ema21:
            score += 20

        if curr_vol > avg_vol * 1.2:
            score += 30
            reasons.append("Vol Spike")
        elif curr_vol > avg_vol * 0.9:
            score += 20

        return score, curr_p, ", ".join(reasons)
    except Exception as e:
        print("Analyze Err:", sym, e, flush=True)
        return 0, None, ""

def binance_worker():
    global balance
    print("Binance Scanner Thread Started!", flush=True)
    while True:
        try:
            live_prices = {}
            for sym in COINS:
                score, price, reason = analyze_coin(sym)
                if price:
                    live_prices[sym] = price

                if sym in positions and price:
                    pos = positions[sym]
                    pnl_pct = ((price - pos["entry"]) / pos["entry"]) * 100
                    pnl_usd = (price - pos["entry"]) * pos["qty"]

                    if not pos["be_locked"] and pnl_pct >= cfg["be_trigger"]:
                        pos["be_locked"] = True
                        pos["active_sl"] = 0.2
                        send_tg(f"🔒 *BREAKEVEN LOCKED (+{pnl_pct:.2f}%):* `{sym}` अब `+0.2%` पर सुरक्षित!")

                    active_sl = pos["active_sl"]
                    if pnl_pct >= cfg["tp_pct"] or pnl_pct <= active_sl:
                        balance += pos["qty"] * price
                        why = "TAKE-PROFIT" if pnl_pct >= cfg["tp_pct"] else ("BREAKEVEN EXIT" if pnl_usd >= 0 else "STOP-LOSS")
                        if pnl_usd >= 0:
                            stats["wins"] += 1
                        else:
                            stats["losses"] += 1
                        stats["pnl"] += pnl_usd
                        journal.append({"sym": sym, "pnl": pnl_usd, "pct": pnl_pct, "why": why})
                        ic = "✅" if pnl_usd >= 0 else "🔻"
                        send_tg(
                            f"{ic} *{why}:* `{sym}`\n"
                            f"💲 Entry: `${pos['entry']:.4f}` ➔ Exit: `${price:.4f}`\n"
                            f"💵 परिणाम: `${pnl_usd:+.2f}` (`{pnl_pct:+.2f}%`) | कैश: `${balance:.2f}`"
                        )
                        del positions[sym]

                elif sym not in positions and len(positions) < cfg["max_trades"] and score >= cfg["min_score"] and price:
                    eq = get_equity(live_prices)
                    amt = round(eq * (cfg["risk_pct"] / 100.0), 2)
                    if balance >= amt and amt >= 5.0:
                        balance -= amt
                        positions[sym] = {
                            "entry": price,
                            "qty": amt / price,
                            "be_locked": False,
                            "active_sl": cfg["sl_pct"]
                        }
                        send_tg(
                            f"🟢 *BINANCE BUY (Score {score}):* `{sym}`\n"
                            f"💲 एंट्री प्राइस: `${price:.4f}` | निवेश: `${amt:.2f}`\n"
                            f"🎯 TP: `+{cfg['tp_pct']}%` | 🛑 SL: `{cfg['sl_pct']}%`\n"
                            f"🧠 कारण: {reason}"
                        )

            print(f"Binance Scan OK | Cash: ${balance:.2f} | Open: {len(positions)}", flush=True)
            time.sleep(20)
        except Exception as e:
            print("Binance Loop Err:", e, flush=True)
            time.sleep(10)

threading.Thread(target=tg_worker, daemon=True).start()
threading.Thread(target=binance_worker, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
