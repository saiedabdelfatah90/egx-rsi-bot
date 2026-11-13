import ast
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["CHAT_ID"].split(",") if c.strip()]
STATE_FILE = "state.json"
CAIRO = ZoneInfo("Africa/Cairo")

# نقرا قايمة الأسهم من bot.py
SYMBOLS = []
with open("bot.py", encoding="utf-8") as f:
    for node in ast.parse(f.read()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "SYMBOLS":
            SYMBOLS = ast.literal_eval(node.value)

# ---------------- الإعدادات ----------------
# الفريم: (الفريم, أقل ارتفاع للسارية %, أقصى طول للسارية بالشموع,
#          (أقل, أقصى) طول العلم بالشموع, نفحصه كل كام دقيقة)
CFG = {
    "15 دقيقة": (Interval.in_15_minute, 3.0, 20, (5, 16), 14),
    "ساعة": (Interval.in_1_hour, 5.0, 15, (4, 12), 55),
}
FRAME_EN = {"15 دقيقة": "15m", "ساعة": "1h"}
MAX_RETRACE = 0.5      # أقصى تصحيح من ارتفاع السارية
MAX_CHARTS = 8         # أقصى عدد صور في المرة الواحدة
N_BARS = 200
# --------------------------------------------


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def send(text, chats):
    for chat in chats:
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        except Exception as e:
            print("خطأ في الإرسال:", e)


def send_photo(path, caption, chats):
    for chat in chats:
        try:
            with open(path, "rb") as f:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                              data={"chat_id": chat, "caption": caption},
                              files={"photo": f}, timeout=60)
        except Exception as e:
            print("خطأ في إرسال الصورة:", e)


def detect(df, min_pole, pole_max, flag_rng):
    df = df.dropna(subset=["open", "high", "low", "close"])
    n = len(df)
    if n < 60:
        return None
    H = df["high"].to_numpy(dtype=float)
    L = df["low"].to_numpy(dtype=float)
    C = df["close"].to_numpy(dtype=float)
    V = df["volume"].to_numpy(dtype=float) if "volume" in df.columns else None
    fmin, fmax = flag_rng

    lo = max(pole_max + 1, n - 1 - fmax)
    hi = n - 1 - fmin
    if hi < lo:
        return None
    p = lo + int(np.argmax(H[lo:hi + 1]))      # قمة السارية
    c = n - 1 - p                              # عدد شموع العلم
    if c < fmin or c > fmax:
        return None
    if H[p + 1:].max() > H[p] * 1.002:         # فيه قمة أعلى بعدها
        return None

    s_lo = max(0, p - pole_max)
    s = s_lo + int(np.argmin(L[s_lo:p]))       # بداية السارية
    if p - s < 3 or L[s] <= 0:
        return None
    pole_h = H[p] - L[s]
    gain = pole_h / L[s] * 100
    if gain < min_pole:
        return None

    retr = (H[p] - L[p + 1:].min()) / pole_h
    if retr > MAX_RETRACE:
        return None

    cf = n - 2 - p                             # الشموع اللي بنرسم عليها الخطوط (من غير آخر شمعة)
    if cf < 4:
        return None
    x = np.arange(cf)
    hk, lk = H[p + 1:n - 1], L[p + 1:n - 1]
    ah, bh = np.polyfit(x, hk, 1)
    al, bl = np.polyfit(x, lk, 1)
    bh = float(np.max(hk - ah * x))            # الخط العلوي بيلمس أعلى القمم
    bl = float(np.min(lk - al * x))            # الخط السفلي بيلمس أدنى القيعان
    u_last = float(ah * cf + bh)
    l_last = float(al * cf + bl)
    w0 = bh - bl
    w1 = (ah * (cf - 1) + bh) - (al * (cf - 1) + bl)
    if w0 <= 0 or w1 <= 0:
        return None
    drift_h = ah * (cf - 1) / pole_h
    drift_l = al * (cf - 1) / pole_h

    pole_avg = float(np.mean(H[s:p + 1] - L[s:p + 1]))
    flag_avg = float(np.mean(H[p + 1:] - L[p + 1:]))
    if flag_avg > 0.7 * pole_avg:              # الحركة لازم تهدى
        return None

    ratio = w1 / w0
    if drift_h <= -0.05 and drift_l >= 0.0 and ratio <= 0.7:
        kind = "pennant"                       # راية: خطوط متقاربة
    elif (drift_h <= 0.1 and drift_l <= 0.1 and abs(drift_h - drift_l) <= 0.15
          and 0.7 < ratio <= 1.4 and (drift_h + drift_l) / 2 >= -0.5):
        kind = "flag"                          # علم: خطوط متوازية
    else:
        return None

    close = float(C[-1])
    if close < l_last:                         # كسر لأسفل
        return None
    status = "breakout" if close > u_last else "forming"

    vr = None
    if V is not None and V[s:p + 1].mean() > 0:
        vr = float(V[p + 1:n - 1].mean() / V[s:p + 1].mean())

    return {
        "kind": kind, "status": status, "p": p, "s": s,
        "low_s": float(L[s]), "high_p": float(H[p]),
        "gain": float(gain), "retr": float(retr), "c": int(c),
        "u0": bh, "l0": bl, "u_last": u_last, "l_last": l_last,
        "pole_h": float(pole_h), "target": u_last + float(pole_h),
        "score": float(gain * (1 - retr) / (1 + w1 / pole_h)),
        "vr": vr, "price": close, "pole_time": str(df.index[p]),
        "df": df,
    }


def pattern_levels(r):
    """دخول = مستوى الكسر (أو السعر الحالي لو الكسر حصل)، وقف = الخط السفلي، هدف = الدخول + طول السارية."""
    entry = r["price"] if r["status"] == "breakout" else r["u_last"]
    return entry, r["l_last"], entry + r["pole_h"]


def make_chart(r, sym, frame, path):
    try:
        import matplotlib
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "matplotlib"], check=False)
        import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    df = r["df"]
    start = max(0, r["s"] - 12)
    d = df.iloc[start:]
    O = d["open"].to_numpy(dtype=float)
    H = d["high"].to_numpy(dtype=float)
    L = d["low"].to_numpy(dtype=float)
    C = d["close"].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(9, 5))
    for i in range(len(d)):
        col = "#2e7d32" if C[i] >= O[i] else "#c62828"
        ax.vlines(i, L[i], H[i], color=col, linewidth=1)
        body_h = max(abs(C[i] - O[i]), (H[i] - L[i]) * 0.03, 1e-9)
        ax.add_patch(Rectangle((i - 0.3, min(O[i], C[i])), 0.6, body_h, color=col))

    s_i = r["s"] - start
    p_i = r["p"] - start
    n_i = len(d) - 1
    ax.plot([s_i, p_i], [r["low_s"], r["high_p"]], color="#1565c0", linewidth=2)
    ax.plot([p_i + 1, n_i], [r["u0"], r["u_last"]], "--", color="#ef6c00", linewidth=1.5)
    ax.plot([p_i + 1, n_i], [r["l0"], r["l_last"]], "--", color="#ef6c00", linewidth=1.5)

    try:
        import posbox
        e_, s_, t_ = pattern_levels(r)
        posbox.draw_position(ax, n_i, n_i + 8, e_, s_, t_)
    except Exception as ex:
        print("posbox:", ex)
    name = "Bull Flag" if r["kind"] == "flag" else "Bull Pennant"
    st = "Breakout" if r["status"] == "breakout" else "Forming"
    ax.set_title(f"{sym} | {FRAME_EN.get(frame, frame)} | {name} | {st}")
    ax.set_xlim(-1, n_i + 10)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def make_caption(frame, sym, r):
    is_flag = r["kind"] == "flag"
    title = "علم صاعد 🚩" if is_flag else "راية صاعدة 🚩"
    status = "كسر لأعلى ✅" if r["status"] == "breakout" else "قيد التكوّن ⏳"
    lines = [f"{title} — {status}",
             f"السهم: {sym}",
             f"الفريم: {frame}",
             f"السعر: {r['price']:.2f}",
             f"السارية: +{r['gain']:.1f}% | التصحيح: {r['retr'] * 100:.0f}% من السارية",
             f"عدد شموع {'العلم' if is_flag else 'الراية'}: {r['c']}",
             f"مستوى الكسر: {r['u_last']:.2f}",
             f"الهدف التقديري: {r['target']:.2f}",
             f"الإبطال تحت: {r['l_last']:.2f}"]
    if r["vr"] is not None:
        lines.append(f"حجم تداول النموذج ÷ السارية: {r['vr']:.2f} (أقل من 1 أفضل)")
    try:
        import posbox
        pl = posbox.caption_line(*pattern_levels(r))
        if pl:
            lines.append(pl)
    except Exception:
        pass
    lines.append("⚠️ اكتشاف آلي تقريبي، راجع الشارت بنفسك")
    return "\n".join(lines)


def in_session(now):
    return now.weekday() in (6, 0, 1, 2, 3) and (10, 0) <= (now.hour, now.minute) <= (14, 35)


# ---------------- التشغيل ----------------
state = load_state()
last_id = int(state.get("_last_update_id", 0))
manual_chats = set()

# بنقرا الأوامر من غير ما نأكدها (commands.py هو اللي بيأكدها)
try:
    upd = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                       params={"offset": last_id + 1, "timeout": 0}, timeout=20).json()
except Exception as e:
    print("getUpdates:", e)
    upd = {}
for u in upd.get("result", []):
    msg = u.get("message")
    if not msg:
        continue
    chat = str(msg["chat"]["id"])
    if chat not in CHAT_IDS:
        continue
    parts = msg.get("text", "").strip().split()
    if parts and parts[0].split("@")[0].lower() in ("/flags", "/patterns"):
        manual_chats.add(chat)

rec = state.get("_patterns")
if not isinstance(rec, dict):
    rec = {}
rec.setdefault("last", {})
rec.setdefault("alerted", {})

now = datetime.now(CAIRO)
nowts = time.time()
frames = []
if manual_chats:
    frames = list(CFG.keys())
elif in_session(now):
    for name, cfg in CFG.items():
        if nowts - float(rec["last"].get(name, 0)) >= cfg[4] * 60:
            frames.append(name)

if not frames:
    print("مفيش فريمات محتاجة فحص دلوقتي")
    raise SystemExit

targets = list(manual_chats) if manual_chats else CHAT_IDS
tv = TvDatafeed()
found = []

for name in frames:
    interval, min_pole, pole_max, flag_rng, _ = CFG[name]
    for sym in SYMBOLS:
        try:
            df = tv.get_hist(symbol=sym, exchange="EGX", interval=interval, n_bars=N_BARS)
            if df is None or df.empty:
                continue
            res = detect(df, min_pole, pole_max, flag_rng)
            if res is None:
                continue
            key = f"{name}|{sym}|{res['kind']}|{res['status']}|{res['pole_time']}"
            if not manual_chats:
                if key in rec["alerted"]:
                    continue
                rec["alerted"][key] = nowts
            found.append((name, sym, res))
        except Exception as e:
            print(name, sym, "خطأ:", e)
    if not manual_chats:
        rec["last"][name] = nowts

found.sort(key=lambda x: x[2]["score"], reverse=True)
print("نماذج:", len(found))

if not manual_chats:                           # تسجيل صفقة افتراضية لكل تنبيه جديد
    try:
        import vtrades
        for name, sym, res in found:
            e_, s_, t_ = pattern_levels(res)
            vtrades.register(f"علم/راية {name}", sym, name, e_, s_, t_,
                             f"{res['kind']}|{res['status']}|{res['pole_time']}", res["df"].index[-1])
    except Exception as ex:
        print("vtrades:", ex)

if found:
    for i, (name, sym, res) in enumerate(found[:MAX_CHARTS]):
        path = f"flag_{i}.png"
        try:
            make_chart(res, sym, name, path)
            send_photo(path, make_caption(name, sym, res), targets)
        except Exception as e:
            print("خطأ في الرسم:", e)
            send(make_caption(name, sym, res), targets)
    if len(found) > MAX_CHARTS:
        rest = ", ".join(s for _, s, _ in found[MAX_CHARTS:])
        send(f"وكمان نماذج تانية (من غير صور): {rest}", targets)
elif manual_chats:
    send("🚩 مفيش نماذج علم أو راية واضحة دلوقتي على الفريمات الصغيرة.", targets)

if not manual_chats:
    cutoff = nowts - 3 * 86400
    rec["alerted"] = {k: v for k, v in rec["alerted"].items() if v >= cutoff}
    state["_patterns"] = rec
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)