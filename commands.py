import ast
import json
import os
import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
STATE_FILE = "state.json"

# نقرا قايمة الأسهم من bot.py عشان تعدّلها في مكان واحد بس
SYMBOLS = []
with open("bot.py", encoding="utf-8") as f:
    for node in ast.parse(f.read()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "SYMBOLS":
            SYMBOLS = ast.literal_eval(node.value)

FRAMES = {
    "d": ("يومي", Interval.in_daily),
    "h": ("ساعة", Interval.in_1_hour),
    "m": ("15 دقيقة", Interval.in_15_minute),
}


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return 100 - (100 / (1 + avg_gain / avg_loss))


def send(text):
    # تقسيم الرسائل الطويلة (حد تليجرام 4096 حرف)
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 3800:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": CHAT_ID, "text": chunk}, timeout=20)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": chunk}, timeout=20)


def tag(value):
    if value <= 25:
        return " ⚠️"
    if value >= 75:
        return " 🔥"
    return ""


def build_report(tv, key):
    name, itv = FRAMES[key]
    above, below, missing = [], [], []
    for sym in SYMBOLS:
        try:
            df = tv.get_hist(symbol=sym, exchange="EGX", interval=itv, n_bars=300)
            if df is None or len(df) < 51:
                missing.append(sym)
                continue
            close = df["close"]
            value = float(rsi(close).iloc[-1])
            price = float(close.iloc[-1])
            sma50 = float(close.rolling(50).mean().iloc[-1])
            (above if price >= sma50 else below).append((sym, value))
        except Exception:
            missing.append(sym)

    above.sort(key=lambda x: x[1])
    below.sort(key=lambda x: x[1])

    lines = [f"📊 RSI - فريم {name}", ""]
    lines.append(f"🟢 فوق المتوسط 50 ({len(above)}):")
    lines += [f"{s}  {v:.1f}{tag(v)}" for s, v in above] or ["لا يوجد"]
    lines.append("")
    lines.append(f"🔴 تحت المتوسط 50 ({len(below)}):")
    lines += [f"{s}  {v:.1f}{tag(v)}" for s, v in below] or ["لا يوجد"]
    if missing:
        lines.append("")
        lines.append("⚪ بدون بيانات: " + ", ".join(missing))
    return "\n".join(lines)


# ---------- نقرا الأوامر الجديدة ----------
state = {}
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}

last = int(state.get("_last_update_id", 0))
r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                 params={"offset": last + 1, "timeout": 0}, timeout=20).json()

wanted = []
for u in r.get("result", []):
    last = max(last, u["update_id"])
    msg = u.get("message")
    if not msg or str(msg["chat"]["id"]) != str(CHAT_ID):
        continue
    parts = msg.get("text", "").strip().split()
    if not parts or parts[0].split("@")[0].lower() != "/rsi":
        continue
    arg = parts[1].lower() if len(parts) > 1 else "d"
    if arg not in FRAMES:
        arg = "d"
    if arg not in wanted:
        wanted.append(arg)

if wanted:
    tv = TvDatafeed()
    for key in wanted:
        send(build_report(tv, key))

state["_last_update_id"] = last
with open(STATE_FILE, "w") as f:
    json.dump(state, f)
