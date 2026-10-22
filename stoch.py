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
OVERSOLD = 20    # الشراء: تقاطع صاعد تحت المستوى ده
SELL_LEVEL = 80  # البيع: لما %K يوصل للمستوى ده
STOP_PCT = 1.5   # وقف الخسارة تحت سعر الشراء % (لو غيّرته، غيّره في commands.py كمان)
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


def close_position(sym, entry, price, reason, header):
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
        "reason": reason,
    })
    send(header)
    word = "ربح" if pnl > 0 else "خسارة"
    emoji = "✅" if pnl > 0 else "❌"
    send(f"{emoji} انتهاء الصفقة وفقاً للاستوكاستيك\n"
         f"السهم: {sym} | فريم 30 دقيقة\n"
         f"شراء: {entry:.2f} | بيع: {price:.2f}\n"
         f"النتيجة: {word} {pnl:+.2f}%")
    return pnl


tv = TvDatafeed()

for sym in SYMBOLS:
    try:
        df = tv.get_hist(symbol=sym, exchange="EGX", interval=Interval.in_30_minute, n_bars=120)
        if df is None or len(df) < 30:
            print(sym, "مفيش بيانات")
            continue

        price = float(df["close"].iloc[-1])   # السعر الحالي
        pos = state.get(sym)
        if not isinstance(pos, dict):
            pos = {"status": "NONE"}

        if pos.get("status") == "OPEN":
            entry = pos["entry"]

            # 1) وقف الخسارة (بيتفحص في كل تشغيل)
            stop = entry * (1 - STOP_PCT / 100)
            if price <= stop:
                close_position(
                    sym, entry, price, "stoch_stop",
                    f"🔴 بيع — وقف الخسارة ({STOP_PCT}%) على صفقة الاستوكاستيك\n"
                    f"الفريم: 30 دقيقة\n"
                    f"السهم: {sym}\n"
                    f"سعر البيع: {price:.2f}\n"
                    f"سعر الشراء: {entry:.2f}")
                state[sym] = {"status": "NONE", "last_bar": pos.get("last_bar")}
                print(sym, "وقف خسارة")
                continue

            # 2) هدف البيع: الاستوكاستيك يوصل 80 (على الشمعة الحالية)
            k_live, d_live = stochastic(df)
            if not k_live.iloc[-1:].isna().any() and not d_live.iloc[-1:].isna().any():
                k_now = float(k_live.iloc[-1])
                d_now = float(d_live.iloc[-1])
                if k_now >= SELL_LEVEL:
                    close_position(
                        sym, entry, price, "stoch",
                        f"🔴 بيع — وفقاً للاستوكاستيك\n"
                        f"الفريم: 30 دقيقة\n"
                        f"السهم: {sym}\n"
                        f"سعر البيع: {price:.2f}\n"
                        f"سعر الشراء: {entry:.2f}\n"
                        f"Stoch %K: {k_now:.1f} | %D: {d_now:.1f}\n"
                        f"الاستوكاستيك وصل {SELL_LEVEL}")
                    state[sym] = {"status": "NONE", "last_bar": pos.get("last_bar")}
                    print(sym, "بيع عند", round(k_now, 1))
                    continue

        # 3) إشارة الشراء: تقاطع صاعد على الشموع المقفولة
        closed = df.iloc[:-1]
        k, d = stochastic(closed)
        if k.iloc[-2:].isna().any() or d.iloc[-2:].isna().any():
            continue

        bar = str(closed.index[-1])
        if pos.get("last_bar") == bar:
            continue   # الشمعة دي اتفحصت قبل كده

        k0, k1 = float(k.iloc[-2]), float(k.iloc[-1])
        d0, d1 = float(d.iloc[-2]), float(d.iloc[-1])
        cross_up = k0 <= d0 and k1 > d1

        if pos.get("status") == "NONE":
            if cross_up and min(k0, d0) < OVERSOLD:
                stop_price = price * (1 - STOP_PCT / 100)
                send(f"🟢 شراء — وفقاً للاستوكاستيك\n"
                     f"الفريم: 30 دقيقة\n"
                     f"السهم: {sym}\n"
                     f"سعر الشراء: {price:.2f}\n"
                     f"الوقف: {stop_price:.2f} (-{STOP_PCT}%)\n"
                     f"الهدف: وصول الاستوكاستيك لـ {SELL_LEVEL}\n"
                     f"Stoch %K: {k1:.1f} | %D: {d1:.1f}\n"
                     f"تقاطع صاعد في المنطقة الدنيا (تحت {OVERSOLD})")
                pos = {"status": "OPEN", "entry": price}

        pos["last_bar"] = bar
        state[sym] = pos
        print(sym, f"K={k1:.1f} D={d1:.1f}", pos["status"])
    except Exception as e:
        print(sym, "خطأ:", e)

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f)
with open(TRADES_FILE, "w", encoding="utf-8") as f:
    json.dump(trades, f, ensure_ascii=False)