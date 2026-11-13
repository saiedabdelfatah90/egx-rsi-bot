"""مساعد الدعوم والمقاومات.
بيقرا levels.json (اللي بتحفظه من صفحة levels.html في الداشبورد)،
وبعد ما شمعة تقفل فوق الدعم بتاع أي فريم بيبعت تنبيه "دخول"
والهدف = مقاومة نفس الفريم.
"""
import json
import os
import pickle
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["CHAT_ID"].split(",") if c.strip()]
CAIRO = ZoneInfo("Africa/Cairo")

FRAMES = [("weekly", "أسبوعي"), ("daily", "يومي"), ("hourly", "ساعة"), ("m15", "ربع ساعة")]
FRAME_NAMES = dict(FRAMES)
CLOSE_MIN = 14 * 60 + 45  # بعد إغلاق السوق (دقائق من منتصف الليل)


def now_cairo():
    fake = os.environ.get("FAKE_NOW")
    if fake:
        return datetime.fromisoformat(fake).replace(tzinfo=CAIRO)
    return datetime.now(CAIRO)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def send(text):
    for chat in CHAT_IDS:
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        except Exception as e:
            print("خطأ في الإرسال:", e)


_tv = None


def fetch(sym, frame):
    """بيرجع DataFrame فيه عمود close (آخر صف هو آخر شمعة)."""
    fake = os.environ.get("FAKE_DATA")
    if fake:
        p = os.path.join(fake, f"{sym}_{frame}.pkl")
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return pickle.load(f)
    global _tv
    from tvDatafeed import TvDatafeed, Interval
    if _tv is None:
        _tv = TvDatafeed()
    iv = {
        "weekly": getattr(Interval, "in_weekly", None),
        "daily": Interval.in_daily,
        "hourly": Interval.in_1_hour,
        "m15": Interval.in_15_minute,
    }[frame]
    if iv is None:
        return None
    try:
        return _tv.get_hist(sym, exchange="EGX", interval=iv, n_bars=60)
    except Exception as e:
        print("فشل جلب", sym, frame, e)
        return None


def closed_closes(df, frame, now):
    """قائمة أسعار إغلاق الشموع المقفولة فقط."""
    if df is None or len(df) < 3:
        return None
    closes = [float(x) for x in df["close"].tolist()]
    bars = [str(x) for x in df.index.tolist()]
    mins = now.hour * 60 + now.minute
    keep_last = False
    if frame == "daily":
        keep_last = mins >= CLOSE_MIN or now.weekday() in (4, 5)
    elif frame == "weekly":
        keep_last = (now.weekday() == 3 and mins >= CLOSE_MIN) or now.weekday() in (4, 5)
    # الساعة والربع ساعة: آخر شمعة دايماً بتتكون
    if not keep_last:
        closes = closes[:-1]
        bars = bars[:-1]
    return (closes, bars[-1]) if len(closes) >= 2 else None


def due(frame, now, state_meta):
    """هل الفريم ده يتفحص في الجولة دي؟"""
    mins = now.hour * 60 + now.minute
    if frame == "m15":
        return True
    if frame == "hourly":
        return (now.minute // 5) % 3 == 0
    # يومي وأسبوعي: بعد الإغلاق مرة في اليوم
    if mins < CLOSE_MIN:
        return False
    return state_meta.get(frame) != now.strftime("%Y-%m-%d")


def fmt(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def build_alert(sym, frame, sup, res, last):
    name = FRAME_NAMES[frame]
    lines = [f"✅ إغلاق شمعة {name} فوق الدعم — دخول", f"📌 السهم: {sym}",
             f"🟢 الدعم: {fmt(sup)}", f"💰 إغلاق الشمعة: {fmt(last)}"]
    if res and res > last:
        up = (res / last - 1) * 100
        lines.append(f"🎯 الهدف (مقاومة {name}): {fmt(res)} ({up:+.1f}%)")
        risk = (last / sup - 1) * 100
        if risk > 0:
            lines.append(f"🛑 المخاطرة لحد الدعم: {risk:.1f}% | عائد/مخاطرة: {up / risk:.1f}")
    elif res:
        lines.append(f"⚠️ السعر عدّى مقاومة {name} ({fmt(res)}) بالفعل")
    return "\n".join(lines)


TRACK_TF = {"weekly": "يومي", "daily": "يومي", "hourly": "ساعة", "m15": "15 دقيقة"}


def register_trade(sym, frame, sup, res, last, last_bar, key):
    """صفقة افتراضية: دخول = إغلاق الشمعة، وقف = 1% تحت الدعم، هدف = مقاومة الفريم."""
    try:
        import vtrades
        if not res or res <= last:
            return
        bar = last_bar
        if frame == "weekly":          # بنتابع الأسبوعي بالشموع اليومية بعد آخر يوم مقفول
            d = fetch(sym, "daily")
            if d is None or len(d) < 2:
                return
            bar = str(d.index[-1])
        vtrades.register("دعم/مقاومة", sym, TRACK_TF[frame], last, sup * 0.99, res, key, bar)
    except Exception as e:
        print("vtrades:", e)


def main():
    cfg = load_json("levels.json", {})
    stocks = cfg.get("stocks", {}) if isinstance(cfg, dict) else {}
    if not stocks:
        print("مفيش مستويات في levels.json")
        return
    state = load_json("state.json", {})
    if not isinstance(state, dict):
        state = {}
    st = state.setdefault("_levels", {})
    seen = st.setdefault("seen", {})
    log = st.setdefault("log", [])
    meta = st.setdefault("done", {})
    now = now_cairo()
    today = now.strftime("%Y-%m-%d")
    sent = 0

    for sym, data in stocks.items():
        for frame, _ in FRAMES:
            lv = data.get(frame) or {}
            try:
                sup = float(lv.get("support") or 0)
            except (TypeError, ValueError):
                sup = 0
            try:
                res = float(lv.get("resistance") or 0)
            except (TypeError, ValueError):
                res = 0
            if sup <= 0:
                continue
            key_done = f"{sym}|{frame}"
            if not due(frame, now, {frame: meta.get(key_done)}):
                continue
            cc = closed_closes(fetch(sym, frame), frame, now)
            if frame in ("daily", "weekly"):
                meta[key_done] = today
            if not cc:
                continue
            closes, last_bar = cc
            last, prev = closes[-1], closes[-2]
            if last > sup and prev <= sup:
                bar = f"{today}|{len(closes)}|{fmt(last)}" if frame in ("hourly", "m15") else f"{today}|{frame}"
                k = f"{sym}|{frame}|{fmt(sup)}|{bar}"
                if k in seen:
                    continue
                seen[k] = today
                msg = build_alert(sym, frame, sup, res, last)
                send(msg)
                sent += 1
                log.append({"t": now.strftime("%Y-%m-%d %H:%M"), "sym": sym, "frame": frame,
                            "support": sup, "resistance": res, "close": last})
                register_trade(sym, frame, sup, res, last, last_bar, k)
    st["log"] = log[-60:]
    if len(seen) > 400:
        for k in list(seen)[:len(seen) - 400]:
            seen.pop(k, None)
    with open("state.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print("تنبيهات اتبعتت:", sent)


if __name__ == "__main__":
    main()
