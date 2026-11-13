"""بيتابع الصفقات الافتراضية المفتوحة (vtrades.json) ويقفلها في trades.json لما توصل الوقف أو الهدف
أو تعدي مدة المتابعة. بيشتغل في كل جولة بعد سكريبتات التنبيهات."""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import vtrades as V

CAIRO = ZoneInfo("Africa/Cairo")
TRADES_FILE = "trades.json"


def load_trades():
    try:
        with open(TRADES_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def fetch(tv, Interval, sym, tf):
    iv = {"يومي": Interval.in_daily, "ساعة": Interval.in_1_hour, "15 دقيقة": Interval.in_15_minute}[tf]
    try:
        df = tv.get_hist(symbol=sym, exchange="EGX", interval=iv, n_bars=150)
        return None if df is None or df.empty else df.dropna(subset=["open", "high", "low", "close"])
    except Exception as e:
        print(sym, tf, "خطأ بيانات:", e)
        return None


def evaluate(t, df):
    """بيرجع (reason, exit_price) لو الصفقة اتقفلت، أو None. الوقف الأول لو الاتنين في نفس الشمعة."""
    stop, target = t["stop"], t["target"]
    n = len(df)
    held = 0
    for k in range(n):
        if str(df.index[k]) <= t["bar"]:
            continue
        o, h, l = (float(df[c].iloc[k]) for c in ("open", "high", "low"))
        if l <= stop:
            return "stop", min(o, stop)
        if h >= target:
            return "target", max(o, target)
        if k < n - 1:          # آخر شمعة لسه بتتكوّن، مبتتحسبش في المدة
            held += 1
            if held >= V.MAX_BARS.get(t["tf"], 30):
                return "timeout", float(df["close"].iloc[k])
    return None


def main():
    d = V.load()
    if not d["open"]:
        print("مفيش صفقات افتراضية مفتوحة")
        return
    fake = os.environ.get("FAKE_DATA")
    tv = Interval = None
    if not fake:
        from tvDatafeed import TvDatafeed, Interval
        tv = TvDatafeed()
    trades = load_trades()
    now = datetime.now(CAIRO)
    closed = 0
    cache = {}
    for k, t in list(d["open"].items()):
        ck = (t["sym"], t["tf"])
        if ck not in cache:
            if fake:
                import pickle
                p = os.path.join(fake, f"{t['sym']}_{t['tf']}.pkl")
                cache[ck] = pickle.load(open(p, "rb")) if os.path.exists(p) else None
            else:
                cache[ck] = fetch(tv, Interval, t["sym"], t["tf"])
        df = cache[ck]
        if df is None:
            continue
        res = evaluate(t, df)
        if not res:
            continue
        reason, px = res
        trades.append({"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M"),
                       "symbol": t["sym"], "frame": t["label"], "entry": t["entry"],
                       "exit": round(px, 3), "pnl": round((px / t["entry"] - 1) * 100, 2),
                       "reason": reason})
        d["open"].pop(k)
        closed += 1
    if closed:
        with open(TRADES_FILE, "w", encoding="utf-8") as f:
            json.dump(trades, f, ensure_ascii=False)
    V.save(d)
    print(f"صفقات افتراضية اتقفلت: {closed} | لسه مفتوحة: {len(d['open'])}")


if __name__ == "__main__":
    main()
