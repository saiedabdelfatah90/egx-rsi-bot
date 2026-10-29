import ast
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
STATE_FILE = "state.json"
TRADES_FILE = "trades.json"

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
NAME_TO_INTERVAL = {v[0]: v[1] for v in FRAMES.values()}
FRAME_ORDER = ["15 دقيقة", "ساعة", "يومي"]
SHORT_FRAME = {"15 دقيقة": "15د", "ساعة": "ساعة", "يومي": "يومي"}

# لازم تطابق إعدادات bot.py (لو غيّرتها هناك، غيّرها هنا)
TARGETS = {"15 دقيقة": (3, 6), "ساعة": (5, 10), "يومي": (5, 10)}
STOP_PCT = 7

REASONS = {
    "target": "هدف",
    "t1+t2": "هدفين",
    "t2": "هدف ثاني",
    "stop": "وقف خسارة",
    "t1+stop": "هدف أول ثم وقف",
}
CLOSED_LIMIT = 60


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


def set_menu():
    cmds = [
        {"command": "rsi", "description": "تقرير RSI للفريم اليومي"},
        {"command": "rsih", "description": "تقرير RSI لفريم الساعة"},
        {"command": "rsim", "description": "تقرير RSI لفريم 15 دقيقة"},
        {"command": "open", "description": "الصفقات المفتوحة حالياً"},
        {"command": "closed", "description": "الصفقات المقفولة اليوم بالتفاصيل"},
        {"command": "closedweek", "description": "الصفقات المقفولة آخر 7 أيام"},
        {"command": "closedall", "description": "كل الصفقات المقفولة من الأول"},
        {"command": "report", "description": "إحصائية اليوم حتى الآن"},        {"command": "excel", "description": "تقرير Excel لصفقات اليوم"},
        {"command": "excelweek", "description": "تقرير Excel لآخر 7 أيام"},
        {"command": "excelall", "description": "تقرير Excel لكل الصفقات"},
        {"command": "help", "description": "شرح الاختصارات"},
    ]
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands",
                      json={"commands": cmds}, timeout=20)
    except Exception as e:
        print("setMyCommands:", e)


def tag(value):
    if value <= 25:
        return " ⚠️"
    if value >= 75:
        return " 🔥"
    return ""


def build_help():
    return (
               "/excel — ملف Excel لصفقات النهاردة\n"
        "/excelweek — ملف Excel لآخر 7 أيام\n"
        "/excelall — ملف Excel لكل الصفقات\n" "📋 الاختصارات:\n\n"
        "/rsi — تقرير RSI للفريم اليومي (فوق وتحت المتوسط 50)\n"
        "/rsih — نفس التقرير على فريم الساعة\n"
        "/rsim — نفس التقرير على فريم 15 دقيقة\n"
        "/open — الصفقات المفتوحة\n"
        "/closed — الصفقات المقفولة النهاردة بالتفاصيل\n"
        "/closedweek — الصفقات المقفولة آخر 7 أيام\n"
        "/closedall — كل الصفقات المقفولة من الأول\n"
        "/report — إحصائية اليوم حتى الآن\n"
        "/help — القايمة دي\n\n"
        "ℹ️ الرد بيوصل في التشغيل الجاي (لحد 5 دقايق في ساعات الجلسة).\n"
        "تنبيهات الشراء والبيع والتقرير اليومي بيوصلوا لوحدهم."
    )


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


def current_price(tv, sym, fr):
    try:
        df = tv.get_hist(symbol=sym, exchange="EGX", interval=NAME_TO_INTERVAL[fr], n_bars=5)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])
    except Exception:
        return None


def build_open_report(tv, state):
    by_frame = {fr: [] for fr in FRAME_ORDER}
    for key, pos in state.items():
        if not isinstance(pos, dict) or pos.get("status") not in ("HALF", "FULL"):
            continue
        if "|" not in key:
            continue
        sym, fr = key.split("|", 1)
        if fr in by_frame:
            by_frame[fr].append((sym, pos))

    total = sum(len(v) for v in by_frame.values())
    if total == 0:
        return "🔓 لا توجد صفقات مفتوحة حالياً"

    lines = [f"🔓 الصفقات المفتوحة ({total})", ""]
    for fr in FRAME_ORDER:
        items = sorted(by_frame[fr], key=lambda x: x[0])
        if not items:
            continue
        lines.append(f"━━ فريم {fr} ({len(items)}) ━━")
        t1p, t2p = TARGETS[fr]
        for sym, pos in items:
            avg = pos["avg"]
            first = pos["first"]
            two = pos.get("status") == "FULL"
            t1_done = pos.get("t1_done", False)
            realized = pos.get("realized", 0.0)
            t1 = avg * (1 + t1p / 100)
            t2 = avg * (1 + t2p / 100)
            stop = first * (1 - STOP_PCT / 100)
            price = current_price(tv, sym, fr)

            lines.append(f"📌 {sym}")
            lines.append("الشراء: جزءين (أول + ثاني)" if two else "الشراء: جزء واحد (الأول فقط)")
            lines.append(f"سعر الشراء الأول: {first:.2f}")
            lines.append(f"متوسط الشراء: {avg:.2f}")
            lines.append("البيع: تم بيع نصف الكمية (الهدف الأول)" if t1_done
                         else "البيع: لم يتم بيع شيء")
            if price is None:
                lines.append("السعر الحالي: غير متاح")
            else:
                remaining = 0.5 if t1_done else 1.0
                result = realized + remaining * (price - avg) / avg * 100
                lines.append(f"السعر الحالي: {price:.2f} | النتيجة الحالية: {result:+.2f}%")
            lines.append(f"الوقف: {stop:.2f}")
            lines.append(f"الهدف الأول (+{t1p}%): {t1:.2f}" + (" ✅" if t1_done else ""))
            lines.append(f"الهدف الثاني (+{t2p}%): {t2:.2f}")
            lines.append("")
    return "\n".join(lines)


def stats(items):
    wins = [t["pnl"] for t in items if t["pnl"] > 0]
    losses = [t["pnl"] for t in items if t["pnl"] <= 0]
    total = len(items)
    rate = (len(wins) / total * 100) if total else 0
    return len(wins), len(losses), rate, sum(wins), sum(losses)


def stats_block(title, items):
    w, l, rate, sw, sl = stats(items)
    lines = [f"━━ {title} ━━"]
    if not items:
        lines.append("لا توجد صفقات مغلقة")
        return "\n".join(lines)
    lines.append(f"✅ ناجحة: {w} | نسبة النجاح: {rate:.1f}%")
    lines.append(f"   مجموع الأرباح: {sw:+.2f}%")
    lines.append(f"❌ خاسرة: {l}")
    lines.append(f"   مجموع الخسائر: {sl:+.2f}%")
    lines.append(f"📈 الصافي: {sw + sl:+.2f}%")
    return "\n".join(lines)


def load_dict(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def load_trades():
    # بنقرا صفقات الفريمات الثلاثة بس
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [t for t in data if t.get("frame") in FRAME_ORDER]
        except Exception:
            return []
    return []


def build_today_report(state):
    today = datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d")
    todays = [t for t in load_trades() if t.get("date") == today]

    parts = [f"📅 إحصائية يوم {today} (حتى الآن)", ""]
    for fr in FRAME_ORDER:
        parts.append(stats_block(f"فريم {fr}", [t for t in todays if t.get("frame") == fr]))
        parts.append("")
    parts.append(stats_block("الإجمالي", todays))

    open_counts = {fr: 0 for fr in FRAME_ORDER}
    for key, pos in state.items():
        if isinstance(pos, dict) and pos.get("status") in ("HALF", "FULL") and "|" in key:
            fr = key.split("|", 1)[-1]
            if fr in open_counts:
                open_counts[fr] += 1
    parts.append("")
    parts.append("🔓 صفقات لسه مفتوحة:")
    parts += [f"{fr}: {n}" for fr, n in open_counts.items()]
    return "\n".join(parts)


def build_closed(period):
    now = datetime.now(ZoneInfo("Africa/Cairo"))
    today = now.strftime("%Y-%m-%d")
    trades = load_trades()

    if period == "today":
        items = [t for t in trades if t.get("date") == today]
        title = f"اليوم {today}"
    elif period == "week":
        start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        items = [t for t in trades if t.get("date", "") >= start]
        title = "آخر 7 أيام"
    else:
        items = list(trades)
        title = "كل الصفقات"

    items = sorted(items, key=lambda t: (t.get("date", ""), t.get("time", "")))
    lines = [f"📕 الصفقات المقفولة — {title} ({len(items)})", ""]
    if not items:
        lines.append("لا توجد صفقات مقفولة في الفترة دي")
        return "\n".join(lines)

    for fr in FRAME_ORDER:
        lines.append(stats_block(f"فريم {fr}", [t for t in items if t.get("frame") == fr]))
        lines.append("")
    lines.append(stats_block("الإجمالي", items))
    lines.append("")
    lines.append("📋 التفاصيل (الأحدث أولاً):")

    shown = items[::-1][:CLOSED_LIMIT]
    for t in shown:
        mark = "✅" if t.get("pnl", 0) > 0 else "❌"
        reason = REASONS.get(t.get("reason", ""), t.get("reason", ""))
        fr = SHORT_FRAME.get(t.get("frame", ""), t.get("frame", ""))
        lines.append(f"{mark} {t.get('date', '')[5:]} {t.get('time', '')} | {t.get('symbol', '')} | {fr}")
        lines.append(f"   {t.get('entry', 0):.2f} ← {t.get('exit', 0):.2f} | {t.get('pnl', 0):+.2f}% | {reason}")
    if len(items) > CLOSED_LIMIT:
        lines.append(f"... وفيه {len(items) - CLOSED_LIMIT} صفقة أقدم مش معروضة")
    return "\n".join(lines)

def _fmt_row(r):
    sym, value, price, d50, d200 = r
    s200 = f"{d200:+.1f}%" if d200 is not None else "—"
    return f"{sym}  {price:.2f} | RSI {value:.1f}{tag(value)} | م50 {d50:+.1f}% | م200 {s200}"


def build_report2(tv, key):
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
            d50 = (price / sma50 - 1) * 100
            d200 = None
            if len(close) >= 200:
                sma200 = float(close.rolling(200).mean().iloc[-1])
                d200 = (price / sma200 - 1) * 100
            row = (sym, value, price, d50, d200)
            (above if price >= sma50 else below).append(row)
        except Exception:
            missing.append(sym)

    above.sort(key=lambda x: x[1])
    below.sort(key=lambda x: x[1])

    lines = [f"📊 RSI - فريم {name}",
             "م50 / م200 = بعد السعر عن المتوسط المتحرك 50 / 200", ""]
    lines.append(f"🟢 فوق المتوسط 50 ({len(above)}):")
    lines += [_fmt_row(r) for r in above] or ["لا يوجد"]
    lines.append("")
    lines.append(f"🔴 تحت المتوسط 50 ({len(below)}):")
    lines += [_fmt_row(r) for r in below] or ["لا يوجد"]
    if missing:
        lines.append("")
        lines.append("⚪ بدون بيانات: " + ", ".join(missing))
    return "\n".join(lines)
LRM = "\u200e"


def _arrow(v):
    if v is None:
        return "—"
    return f"{'▲' if v >= 0 else '▼'} {abs(v):.1f}%"


def _stock_block(r):
    sym, value, price, d50, d200 = r
    return (f"{LRM}🔹 {sym}  ·  {price:.2f}\n"
            f"{LRM}RSI {value:.1f}{tag(value)}  |  MA50 {_arrow(d50)}  |  MA200 {_arrow(d200)}")


def send_blocks(text):
    chunk = ""
    for block in text.split("\n\n"):
        if chunk and len(chunk) + len(block) + 2 > 3500:
            send(chunk)
            chunk = ""
        chunk += block + "\n\n"
    if chunk.strip():
        send(chunk)


def build_report3(tv, key):
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
            d50 = (price / sma50 - 1) * 100
            d200 = None
            if len(close) >= 200:
                sma200 = float(close.rolling(200).mean().iloc[-1])
                d200 = (price / sma200 - 1) * 100
            row = (sym, value, price, d50, d200)
            (above if price >= sma50 else below).append(row)
        except Exception:
            missing.append(sym)

    above.sort(key=lambda x: x[1])
    below.sort(key=lambda x: x[1])

    now = datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d  %H:%M")
    bar = "━━━━━━━━━━━━━━━━"

    lines = [f"📊 تقرير RSI — فريم {name}",
             f"🕐 {now}",
             "",
             f"🟢 فوق المتوسط 50 · عدد الأسهم: {len(above)}",
             f"🔴 تحت المتوسط 50 · عدد الأسهم: {len(below)}"]
    if missing:
        lines.append(f"⚪ بدون بيانات · عدد الأسهم: {len(missing)}")
    lines += ["",
              "MA50 و MA200 = المسافة بين السعر والمتوسط المتحرك",
              ""]

    def section(title, rows):
        out = [f"{bar}\n{title}\n{bar}", ""]
        if not rows:
            out += ["لا يوجد", ""]
        for r in rows:
            out += [_stock_block(r), ""]
        return out

    lines += section("🟢 فوق المتوسط 50", above)
    lines += section("🔴 تحت المتوسط 50", below)
    if missing:
        lines += [f"{bar}\n⚪ بدون بيانات\n{bar}", "", LRM + ", ".join(missing), ""]
    return "\n".join(lines)
# ---------- نقرا الأوامر الجديدة ----------
state = load_dict(STATE_FILE)

set_menu()

last = int(state.get("_last_update_id", 0))
r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                 params={"offset": last + 1, "timeout": 0}, timeout=20).json()

wanted = []
closed_wanted = []
want_open = False
want_help = False
want_report = False


def add_closed(period):
    if period not in closed_wanted:
        closed_wanted.append(period)


for u in r.get("result", []):
    last = max(last, u["update_id"])
    msg = u.get("message")
    if not msg or str(msg["chat"]["id"]) != str(CHAT_ID):
        continue
    parts = msg.get("text", "").strip().split()
    if not parts:
        continue
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1].lower() if len(parts) > 1 else ""

    if cmd in ("/rsi", "/rsih", "/rsim"):
        if cmd == "/rsih":
            key = "h"
        elif cmd == "/rsim":
            key = "m"
        else:
            key = arg if arg in FRAMES else "d"
        if key not in wanted:
            wanted.append(key)
    elif cmd in ("/open", "/trades"):
        want_open = True
    elif cmd in ("/help", "/start"):
        want_help = True
    elif cmd == "/report":
        want_report = True
    elif cmd == "/closed":
        if arg in ("week", "w"):
            add_closed("week")
        elif arg in ("all", "a"):
            add_closed("all")
        else:
            add_closed("today")
    elif cmd == "/closedweek":
        add_closed("week")
    elif cmd == "/closedall":
        add_closed("all")

if want_help:
    send(build_help())
if want_report:
    send(build_today_report(state))
for period in closed_wanted:
    send(build_closed(period))
if wanted or want_open:
    tv = TvDatafeed()
    for key in wanted:
                send_blocks(build_report3(tv, key))
    if want_open:
        send(build_open_report(tv, state))

state["_last_update_id"] = last
with open(STATE_FILE, "w") as f:
    json.dump(state, f)
