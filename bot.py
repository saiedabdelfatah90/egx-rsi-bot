import json
import os
import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOLS = ["SKPC", "ABUK", "AMOC", "EFID", "AJWA", "INFI", "ISMA", "MILS", "MOSC", "MPCO", "NEDA", "UEFM", "ZEOT", "GOUR", "CEFM", "AMES", "AXPH", "CPCI", "SIPC", "SPMD", "NIPH", "FCMD", "ACAMD", "ATLC", "FIRE", "BONY", "APSW", "SPIN", "MCQE", "PRCL", "ARCC", "ETRS", "SAUD", "ADIB", "MTIE", "ICFC", "VERT", "SMFR", "AMII", "EEII", "RUBX", "EGAL", "ALUM", "AALR", "EALR", "IEEC", "INEG", "CAED", "MOED", "ROTO", "EGAS", "AMIA", "KWIN", "GGRN"]

# كل فريم بيتتابع كصفقة مستقلة. امسح أي فريم مش عايزه.
FRAMES = [("15 دقيقة", Interval.in_15_minute),
          ("ساعة", Interval.in_1_hour),
          ("يومي", Interval.in_daily)]

BUY1_RSI = 20      # شراء النصف الأول
BUY2_RSI = 10      # شراء النصف التاني
TARGET_PCT = 3     # البيع فوق متوسط الشراء بنسبة %
STOP_PCT = 1       # وقف الخسارة تحت متوسط الشراء بنسبة %
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


def levels(avg):
    return avg * (1 + TARGET_PCT / 100), avg * (1 - STOP_PCT / 100)


state = {}
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}
else:
    send("✅ بوت الصفقات اشتغل")

tv = TvDatafeed()

for sym in SYMBOLS:
    for name, itv in FRAMES:
        try:
            df = tv.get_hist(symbol=sym, exchange="EGX", interval=itv, n_bars=300)
            if df is None or len(df) < 20:
                print(sym, name, "مفيش بيانات")
                continue
            value = float(rsi(df["close"]).iloc[-1])
            price = float(df["close"].iloc[-1])

            key = f"{sym}|{name}"
            pos = state.get(key)
            if not isinstance(pos, dict):
                pos = {"status": "NONE"}
            status = pos.get("status", "NONE")
            head = f"السهم: {sym}\nالفريم: {name}\nRSI: {value:.1f}\nالسعر: {price:.2f}"

            # بعد البيع نستنى RSI يرجع فوق مستوى الشراء الأول قبل صفقة جديدة
            if status == "WAIT" and value > BUY1_RSI:
                status = "NONE"
                pos = {"status": "NONE"}

            if status == "NONE":
                if value <= BUY2_RSI:
                    target, stop = levels(price)
                    pos = {"status": "FULL", "avg": price, "first": price}
                    send(f"🟢🟢 شراء الكمية كاملة\n{head}\nالهدف: {target:.2f}\nالوقف: {stop:.2f}")
                elif value <= BUY1_RSI:
                    target, stop = levels(price)
                    pos = {"status": "HALF", "avg": price, "first": price}
                    send(f"🟢 شراء نصف الكمية\n{head}\nالهدف: {target:.2f}\nالوقف: {stop:.2f}")

            elif status in ("HALF", "FULL"):
                avg = pos["avg"]
                target, stop = levels(avg)
                if price >= target:
                    send(f"💰 بيع (الهدف +{TARGET_PCT}%)\n{head}\nمتوسط الشراء: {avg:.2f}")
                    pos = {"status": "WAIT"}
                elif price <= stop:
                    send(f"🔴 بيع (وقف خسارة)\n{head}\nمتوسط الشراء: {avg:.2f}")
                    pos = {"status": "WAIT"}
                elif status == "HALF" and value <= BUY2_RSI:
                    new_avg = (pos["first"] + price) / 2
                    target, stop = levels(new_avg)
                    pos = {"status": "FULL", "avg": new_avg, "first": pos["first"]}
                    send(f"🟢 شراء النصف التاني\n{head}\nمتوسط الشراء: {new_avg:.2f}\nالهدف: {target:.2f}\nالوقف: {stop:.2f}")

            state[key] = pos
            print(sym, name, round(value, 1), pos["status"])
        except Exception as e:
            print(sym, name, "خطأ:", e)

with open(STATE_FILE, "w") as f:
    json.dump(state, f)
