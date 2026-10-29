import ast
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["CHAT_ID"].split(",") if c.strip()]
STATE_FILE = "state.json"

AFTER_CLOSE = (14, 40)   # الفحص بيبدأ بعد الوقت ده بتوقيت القاهرة (بعد قفل شمعة اليوم)
TEST_MODE = False        # True = للتجربة بس: يتخطى شرط الوقت واليوم ومش بيحفظ حاجة

# نقرا قايمة الأسهم من bot.py
SYMBOLS = []
with open("bot.py", encoding="utf-8") as f:
    for node in ast.parse(f.read()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "SYMBOLS":
            SYMBOLS = ast.literal_eval(node.value)


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def send(text):
    for chat in CHAT_IDS:
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        except Exception as e:
            print("خطأ في الإرسال:", e)


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return 100 - (100 / (1 + avg_gain / avg_loss))


now = datetime.now(ZoneInfo("Africa/Cairo"))
today = now.strftime("%Y-%m-%d")

if not TEST_MODE:
    if now.weekday() not in (6, 0, 1, 2, 3):   # الأحد للخميس
        print("مش يوم تداول")
        raise SystemExit
    if (now.hour, now.minute) < AFTER_CLOSE:
        print("الجلسة لسه ما خلصتش")
        raise SystemExit

state = load_json(STATE_FILE, {})
if not isinstance(state, dict):
    state = {}
rec = state.get("_ma200")
if not isinstance(rec, dict) or rec.get("date") != today:
    rec = {"date": today, "checked": []}
checked = set(rec.get("checked", []))
if TEST_MODE:
    checked = set()

remaining = [s for s in SYMBOLS if s not in checked]
if not remaining:
    print("كل الأسهم اتفحصت النهاردة")
    raise SystemExit

tv = TvDatafeed()
found = 0

for sym in remaining:
    try:
        df = tv.get_hist(symbol=sym, exchange="EGX", interval=Interval.in_daily, n_bars=300)
        if df is None or df.empty:
            continue   # هنعيد المحاولة في التشغيل الجاي

        checked.add(sym)
        if not TEST_MODE and str(df.index[-1])[:10] != today:
            continue   # مفيش شمعة النهاردة للسهم ده
        if len(df) < 201:
            continue

        close = df["close"]
        sma200 = float(close.rolling(200).mean().iloc[-1])
        sma50 = float(close.rolling(50).mean().iloc[-1])
        cl = float(close.iloc[-1])
        lo = float(df["low"].iloc[-1])

        if lo <= sma200 and cl > sma200:
            found += 1
            dip = (lo / sma200 - 1) * 100
            above = (cl / sma200 - 1) * 100
            d50 = (cl / sma50 - 1) * 100
            value = float(rsi(close).iloc[-1])
            dip_txt = (f"نزل تحت المتوسط بـ {dip:+.1f}%" if dip < -0.05 else "لمس المتوسط")
            send(f"📈 توصية شراء — ارتداد من متوسط 200 (يومي)\n"
                 f"السهم: {sym}\n"
                 f"إغلاق اليوم: {cl:.2f}\n"
                 f"أدنى سعر اليوم: {lo:.2f} ({dip_txt})\n"
                 f"متوسط 200: {sma200:.2f}\n"
                 f"الإغلاق فوق المتوسط بـ {above:+.1f}%\n"
                 f"RSI اليومي: {value:.1f}\n"
                 f"البعد عن متوسط 50: {d50:+.1f}%\n"
                 f"الشرط: الشمعة لمست أو نزلت تحت المتوسط وقفلت أعلاه")
    except Exception as e:
        print(sym, "خطأ:", e)

print("تم الفحص | توصيات:", found)

if not TEST_MODE:
    rec["checked"] = sorted(checked)
    state["_ma200"] = rec
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)