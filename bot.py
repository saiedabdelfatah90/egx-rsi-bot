import json
import os
import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# زوّد أسهم EGX70 اللي عايزها (الرمز زي ما هو في TradingView)
SYMBOLS = ["COMI", "TMGH", "EAST", "ETEL", "SWDY", "ABUK", "ORAS", "FWRY"]

FRAMES = [("15 دقيقة", Interval.in_15_minute),
          ("ساعة", Interval.in_1_hour),
          ("يومي", Interval.in_daily)]

BUY_LEVEL = 20
SELL_LEVEL = 80
STATE_FILE = "state.json"


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return 100 - (100 / (1 + avg_gain / avg_loss))


def send(text):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": text}, timeout=20)
    except Exception as e:
        print("خطأ في الإرسال:", e)


first_run = not os.path.exists(STATE_FILE)
state = {}
if not first_run:
    with open(STATE_FILE) as f:
        state = json.load(f)

if first_run:
    send("✅ بوت RSI اشتغل على GitHub")

tv = TvDatafeed()

for sym in SYMBOLS:
    for name, itv in FRAMES:
        try:
            df = tv.get_hist(symbol=sym, exchange="EGX", interval=itv, n_bars=300)
            if df is None or len(df) < 20:
                continue
            value = float(rsi(df["close"]).iloc[-1])
            price = float(df["close"].iloc[-1])

            if value <= BUY_LEVEL:
                new = "BUY"
            elif value >= SELL_LEVEL:
                new = "SELL"
            else:
                new = "NONE"

            key = f"{sym}|{name}"
            prev = state.get(key, "NONE")
            state[key] = new
            print(sym, name, round(value, 1), new)

            if new != "NONE" and new != prev:
                emoji = "🟢 BUY" if new == "BUY" else "🔴 SELL"
                send(f"{emoji}\nالسهم: {sym}\nالفريم: {name}\nRSI: {value:.1f}\nالسعر: {price:.2f}")
        except Exception as e:
            print(sym, name, "خطأ:", e)

with open(STATE_FILE, "w") as f:
    json.dump(state, f)
