import ast
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# نقرا قايمة الأسهم من bot.py
SYMBOLS = []
with open("bot.py", encoding="utf-8") as f:
    for node in ast.parse(f.read()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "SYMBOLS":
            SYMBOLS = ast.literal_eval(node.value)

FRAME_NAME = "30 دقيقة استوكاستيك"
K_PERIOD = 14
SMOOTH_K = 3
D_PERIOD = 3
OVERSOLD = 20
OVERBOUGHT = 80
STATE_FILE = "stoch_state.json"
TRADES_FILE = "trades.json"


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def send(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    except Exception as e:
        print("خطأ في الإرسال:", e)


def stochastic(df):
    low_min = df["low"].rolling(K_PERIOD).min()
    high_max = df["high"].rolling(K_PERIOD).max()
    raw = 100 * (df["close"] - low_min) / (high_max - low_min)
    k = raw.rolling(SMOOTH_K).mean()
    d = k.rolling(D_PERIOD).mean()
    return k, d


state = load_json(STATE_FILE, {})
trades = load_json(TRADES_FILE, [])
if not isinstance(state, dict):
    state = {}
if not isinstance(trades, list):
    trades = []

tv = TvDatafeed()

for sym in SYMBOLS:
    try:
        df = tv.get_hist(symbol=sym, exchange="EGX", interval=Interval.in_30_minute, n_bars=120)
        if df is None or len(df) < 30:
            print(sym, "مفيش بيانات")
            continue

        price = float(df["close"].iloc[-1])   # السعر الحالي
        closed = df.iloc[:-1]                 # الشموع المقفولة بس
        k, d = stochastic(closed)
        if k.iloc[-2:].isna().any() or d.iloc[-2:].isna().any():
            continue

        bar = str(closed.index[-1])
        pos = state.get(sym)
        if not isinstance(pos, dict):
            pos = {"status": "NONE"}
        if pos.get("last_bar") == bar:
            continue   # الشمعة دي اتفحصت قبل كده

        k0, k1 = float(k.iloc[-2]), float(k.iloc[-1])
        d0, d1 = float(d.iloc[-2]), float(d.iloc[-1])
        cross_up = k0 <= d0 and k1 > d1
        cross_dn = k0 >= d0 and k1 < d1

        if pos.get("status") == "NONE":
            if cross_up and min(k0, d0) < OVERSOLD:
                send(f"🟢 شراء — وفقاً للاستوكاستيك\n"
                     f"الفريم: 30 دقيقة\n"
                     f"السهم: {sym}\n"
                     f"سعر الشراء: {price:.2f}\n"
                     f"Stoch %K: {k1:.1f} | %D: {d1:.1f}\n"
                     f"تقاطع صاعد في المنطقة الدنيا (تحت {OVERSOLD})")
                pos = {"status": "OPEN", "entry": price}

        elif pos.get("status") == "OPEN":
            if cross_dn and max(k0, d0) > OVERBOUGHT:
                entry = pos["entry"]
                pnl = (price - entry) / entry * 100
                now = datetime.now(ZoneInfo("Africa/Cairo"))
                trades.append({
                    "date": now.strftime("%Y-%m-%d"),
                    "time": now.strftime("%H:%M"),
                    "symbol": sym,
                    "frame": FRAME_NAME,
                    "entry": round(entry, 3),
                    "exit": round(price, 3),
                    "pnl": round(pnl, 2),
                    "reason": "stoch",
                })
                send(f"🔴 بيع — وفقاً للاستوكاستيك\n"
                     f"الفريم: 30 دقيقة\n"
                     f"السهم: {sym}\n"
                     f"سعر البيع: {price:.2f}\n"
                     f"سعر الشراء: {entry:.2f}\n"
                     f"Stoch %K: {k1:.1f} | %D: {d1:.1f}\n"
                     f"تقاطع هابط في المنطقة العليا (فوق {OVERBOUGHT})")
                word = "ربح" if pnl > 0 else "خسارة"
                emoji = "✅" if pnl > 0 else "❌"
                send(f"{emoji} انتهاء الصفقة وفقاً للاستوكاستيك\n"
                     f"السهم: {sym} | فريم 30 دقيقة\n"
                     f"شراء: {entry:.2f} | بيع: {price:.2f}\n"
                     f"النتيجة: {word} {pnl:+.2f}%")
                pos = {"status": "NONE"}

        pos["last_bar"] = bar
        state[sym] = pos
        print(sym, f"K={k1:.1f} D={d1:.1f}", pos["status"])
    except Exception as e:
        print(sym, "خطأ:", e)

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f)
with open(TRADES_FILE, "w", encoding="utf-8") as f:
    json.dump(trades, f, ensure_ascii=False)