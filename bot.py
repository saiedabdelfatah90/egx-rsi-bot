import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOLS = ["EGAL", "EGAS", "ICFC", "MPCO", "MTIE", "SKPC", "AMOC", "ARCC", "ATQA", "BIOC", "COSG", "CRST", "IEEC", "IFAP", "ISMA", "MBSC", "MOED", "PRCL", "SPMD", "ZEOT", "AJWA", "CIRA", "CLHO", "EFID", "ETRS", "ISPH", "JUFO", "MCQE", "ORWE", "RACC", "SPIN", "ALUM", "AMES", "APSW", "AXPH", "CAED", "CPCI", "ETEL", "INFI", "KABO", "KRDI", "LCSW", "MILS", "MOSC", "NIPH", "ORAS", "RMDA", "RUBX", "SIPC", "SVCE", "UEFM", "AALR", "AIFI", "CERA", "EALR", "GGRN", "GOUR", "MCRO", "OBRI", "OCPH", "SCEM", "SMFR", "SWDY", "TALM", "MBEG", "ACGC", "ADCI", "AMII", "BIGP", "BONY", "DGTZ", "EEII", "EFIC", "EPCO", "FCMD", "FNAR", "INEG", "LUTS", "MPCI", "OLFI", "ROTO", "VERT"]

# (اسم الفريم, الفريم, الهدف الأول %, الهدف الثاني %)
FRAMES = [
    ("15 دقيقة", Interval.in_15_minute, 3, 6),
    ("ساعة", Interval.in_1_hour, 5, 10),
    ("يومي", Interval.in_daily, 5, 10),
]

BUY1_RSI = 20     # الشراء الأول عند RSI
BUY2_DROP = 5     # الشراء التاني بعد نزول السعر % من سعر الشراء الأول
STOP_PCT = 7      # وقف الخسارة تحت سعر الشراء الأول %
STATE_FILE = "state.json"
TRADES_FILE = "trades.json"


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


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def now_cairo():
    return datetime.now(ZoneInfo("Africa/Cairo"))


state = load_json(STATE_FILE, {})
trades = load_json(TRADES_FILE, [])
if not isinstance(state, dict):
    state = {}
if not isinstance(trades, list):
    trades = []
if not os.path.exists(STATE_FILE):
    send("✅ بوت الصفقات اشتغل")


def next_buy_number():
    today = now_cairo().strftime("%Y-%m-%d")
    seq = state.get("_buy_seq")
    if not isinstance(seq, dict) or seq.get("date") != today:
        seq = {"date": today, "n": 0}
    seq["n"] += 1
    state["_buy_seq"] = seq
    return seq["n"]


def levels(pos, t1p, t2p):
    avg = pos["avg"]
    t1 = avg * (1 + t1p / 100)
    t2 = avg * (1 + t2p / 100)
    stop = pos["first"] * (1 - STOP_PCT / 100)
    return t1, t2, stop


def close_trade(sym, name, avg, exit_price, pnl, reason):
    now = now_cairo()
    trades.append({
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "symbol": sym,
        "frame": name,
        "entry": round(avg, 3),
        "exit": round(exit_price, 3),
        "pnl": round(pnl, 2),
        "reason": reason,
    })


tv = TvDatafeed()

for sym in SYMBOLS:
    for name, itv, t1p, t2p in FRAMES:
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

            # بعد قفل الصفقة نستنى RSI يرجع فوق مستوى الشراء
            if status == "WAIT" and value > BUY1_RSI:
                pos = {"status": "NONE"}
                status = "NONE"

            if status == "NONE":
                if value <= BUY1_RSI:
                    n = next_buy_number()
                    pos = {"status": "HALF", "avg": price, "first": price,
                           "t1_done": False, "realized": 0.0}
                    t1, t2, stop = levels(pos, t1p, t2p)
                    buy2_at = price * (1 - BUY2_DROP / 100)
                    send(f"🟢 شراء أول (رقم {n} اليوم)\n{head}\n"
                         f"الشراء الثاني عند: {buy2_at:.2f}\n"
                         f"الوقف: {stop:.2f}\n"
                         f"الهدف الأول (+{t1p}%): {t1:.2f}\n"
                         f"الهدف الثاني (+{t2p}%): {t2:.2f}")

            elif status in ("HALF", "FULL"):
                avg = pos["avg"]
                t1, t2, stop = levels(pos, t1p, t2p)
                t1_done = pos.get("t1_done", False)
                realized = pos.get("realized", 0.0)
                leg = (price - avg) / avg * 100
                remaining = 0.5 if t1_done else 1.0

                if price <= stop:
                    total = realized + remaining * leg
                    reason = "t1+stop" if t1_done else "stop"
                    close_trade(sym, name, avg, price, total, reason)
                    send(f"🔴 بيع (وقف خسارة)\n{head}\n"
                         f"متوسط الشراء: {avg:.2f}\n"
                         f"نتيجة الصفقة: {total:+.2f}%")
                    pos = {"status": "WAIT"}

                elif price >= t2:
                    total = realized + remaining * leg
                    reason = "t1+t2" if t1_done else "t2"
                    close_trade(sym, name, avg, price, total, reason)
                    send(f"💰 بيع (الهدف الثاني +{t2p}%)\n{head}\n"
                         f"متوسط الشراء: {avg:.2f}\n"
                         f"نتيجة الصفقة: {total:+.2f}%")
                    pos = {"status": "WAIT"}

                elif (not t1_done) and price >= t1:
                    pos["t1_done"] = True
                    pos["realized"] = 0.5 * leg
                    send(f"💰 بيع نصف الكمية (الهدف الأول +{t1p}%)\n{head}\n"
                         f"متوسط الشراء: {avg:.2f}\n"
                         f"ربح النصف: {leg:+.2f}%\n"
                         f"الهدف الثاني: {t2:.2f}\n"
                         f"الوقف: {stop:.2f}")

                elif status == "HALF" and (not t1_done) and price <= pos["first"] * (1 - BUY2_DROP / 100):
                    n = next_buy_number()
                    new_avg = (pos["first"] + price) / 2
                    pos["avg"] = new_avg
                    pos["status"] = "FULL"
                    t1, t2, stop = levels(pos, t1p, t2p)
                    send(f"🟢 شراء ثاني (رقم {n} اليوم)\n{head}\n"
                         f"متوسط الشراء أصبح: {new_avg:.2f}\n"
                         f"الوقف: {stop:.2f}\n"
                         f"الهدف الأول (+{t1p}%): {t1:.2f}\n"
                         f"الهدف الثاني (+{t2p}%): {t2:.2f}")

            state[key] = pos
            print(sym, name, round(value, 1), pos["status"])
        except Exception as e:
            print(sym, name, "خطأ:", e)

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f)
with open(TRADES_FILE, "w", encoding="utf-8") as f:
    json.dump(trades, f, ensure_ascii=False)