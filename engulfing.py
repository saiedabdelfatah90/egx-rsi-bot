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
ENABLED = ["يومي"]        # الفريمات المفعّلة. لتفعيل الساعة: ["يومي", "ساعة"]
AFTER_CLOSE = (14, 45)    # الفحص اليومي بيبدأ بعد الوقت ده بتوقيت القاهرة
VOL_MULT = 1.2            # حجم الابتلاعية ÷ متوسط حجم التجميع. 0 = من غير شرط حجم
MAX_CHARTS = 8
MANUAL_RECENT = 3         # في الفحص اليدوي (/engulf): آخر كام شمعة
N_BARS = 200

# الفريم: الفريم، أطوال التجميع بالشموع (بنجرّب الأطول الأول)،
#         أقصى مدى للتجميع %، أقصى ميل من أول التجميع لآخره %، أقل حجم للجسم ÷ ATR،
#         التوقيت (close = مرة يومياً بعد القفل، أو كل كام دقيقة)
CFG = {
    "يومي": {"interval": Interval.in_daily, "bases": [10, 15, 20], "range_max": 15.0,
             "drift_max": 6.0, "min_body_atr": 0.7, "gate": "close"},
    "ساعة": {"interval": Interval.in_1_hour, "bases": [12, 20, 30], "range_max": 6.0,
             "drift_max": 2.5, "min_body_atr": 0.7, "gate": 55},
}
FRAME_EN = {"يومي": "Daily", "ساعة": "1h"}
# --------------------------------------------


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


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


def detect(closed, cfg, shift=0):
    """
    شمعة ابتلاعية صاعدة (جسمها بيبلع جسم الشمعة الهابطة اللي قبلها)
    بعد تجميع: حركة عرضية ضيقة، من غير اتجاه واضح، وحجم الابتلاعية أعلى من متوسط التجميع.
    closed: الشموع المقفولة بس.
    """
    closed = closed.iloc[:len(closed) - shift]
    m = len(closed)
    if m < max(cfg["bases"]) + 20:
        return None
    O = closed["open"].to_numpy(dtype=float)
    H = closed["high"].to_numpy(dtype=float)
    L = closed["low"].to_numpy(dtype=float)
    C = closed["close"].to_numpy(dtype=float)
    V = closed["volume"].to_numpy(dtype=float) if "volume" in closed.columns else None

    i = m - 1          # الشمعة الابتلاعية
    p = i - 1          # الشمعة الهابطة اللي قبلها
    if not (C[p] < O[p] and C[i] > O[i]):
        return None
    body_i = C[i] - O[i]
    body_p = O[p] - C[p]
    if not (O[i] <= C[p] and C[i] >= O[p] and body_i > body_p):
        return None

    # جسم الابتلاعية لازم يكون واضح مقارنة بالتذبذب العادي (ATR)
    trs = [max(H[k] - L[k], abs(H[k] - C[k - 1]), abs(L[k] - C[k - 1])) for k in range(i - 14, i)]
    atr = float(np.mean(trs))
    if atr <= 0 or body_i < cfg["min_body_atr"] * atr:
        return None

    for n in sorted(cfg["bases"], reverse=True):
        s = i - n
        if s < 1:
            continue
        lo = float(L[s:i].min())
        hi = float(H[s:i].max())
        if lo <= 0:
            continue
        rng = (hi / lo - 1) * 100
        drift = abs(C[i - 1] - C[s]) / C[s] * 100
        if rng > cfg["range_max"] or drift > cfg["drift_max"]:
            continue
        vm = None
        if V is not None and V[s:i].mean() > 0:
            vm = float(V[i] / V[s:i].mean())
        if VOL_MULT > 0 and (vm is None or vm < VOL_MULT):
            continue
        return {
            "i": i, "p": p, "s": s, "n": n,
            "rng": float(rng), "drift": float(drift), "vm": vm,
            "base_low": lo, "base_high": hi, "breakout": bool(C[i] > hi),
            "o_i": float(O[i]), "c_i": float(C[i]), "o_p": float(O[p]), "c_p": float(C[p]),
            "low_eng": float(L[i]), "chg": float((C[i] / C[p] - 1) * 100),
            "risk": float((C[i] - L[i]) / C[i] * 100),
            "bar": str(closed.index[i]),
        }
    return None


def make_chart(df, r, sym, frame, path):
    try:
        import matplotlib
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "matplotlib"], check=False)
        import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    start = max(0, r["s"] - 10)
    d = df.iloc[start:]
    O = d["open"].to_numpy(dtype=float)
    H = d["high"].to_numpy(dtype=float)
    L = d["low"].to_numpy(dtype=float)
    C = d["close"].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(9, 5))
    s_x, i_x, p_x = r["s"] - start, r["i"] - start, r["p"] - start
    # مربع التجميع
    ax.add_patch(Rectangle((s_x - 0.5, r["base_low"]), i_x - s_x, r["base_high"] - r["base_low"],
                           facecolor="#1565c0", alpha=0.12, edgecolor="#1565c0", linewidth=1))
    for k in range(len(d)):
        col = "#2e7d32" if C[k] >= O[k] else "#c62828"
        ax.vlines(k, L[k], H[k], color=col, linewidth=1)
        body_h = max(abs(C[k] - O[k]), (H[k] - L[k]) * 0.03, 1e-9)
        edge = "#000000" if k in (i_x, p_x) else col
        ax.add_patch(Rectangle((k - 0.3, min(O[k], C[k])), 0.6, body_h, facecolor=col,
                               edgecolor=edge, linewidth=1.4 if k in (i_x, p_x) else 0.5))
    ax.annotate("Bullish engulfing", xy=(i_x, H[i_x]), xytext=(i_x - 6, H[i_x] + (r["base_high"] - r["base_low"]) * 0.8),
                arrowprops={"arrowstyle": "->"}, fontsize=9)
    ax.hlines(r["low_eng"], i_x, len(d) + 1, colors="#c62828", linestyles=":", linewidth=1.2)
    ax.set_title(f"{sym} | {FRAME_EN.get(frame, frame)} | Engulfing after base ({r['n']} bars)")
    ax.set_xlim(-1, len(d) + 2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def make_caption(frame, sym, r, note=""):
    lines = [f"🟢 شمعة ابتلاعية صاعدة بعد تجميع {note}".strip(),
             f"السهم: {sym}",
             f"الفريم: {frame}",
             f"الإغلاق: {r['c_i']:.2f} ({r['chg']:+.1f}% عن الشمعة السابقة)",
             f"جسم الابتلاعية: {r['o_i']:.2f} ← {r['c_i']:.2f} | الشمعة السابقة: {r['o_p']:.2f} ← {r['c_p']:.2f}",
             f"التجميع: {r['n']} شمعة | المدى: {r['rng']:.1f}% | الميل: {r['drift']:.1f}%"]
    if r["vm"] is not None:
        lines.append(f"حجم الابتلاعية: {r['vm']:.1f}x من متوسط التجميع")
    lines.append(f"منطقة التجميع: {r['base_low']:.2f} – {r['base_high']:.2f}")
    lines.append("الإغلاق فوق التجميع ✅ (اختراق)" if r["breakout"] else "الإغلاق لسه جوه التجميع")
    lines.append(f"الإبطال تحت أدنى الابتلاعية: {r['low_eng']:.2f} (-{r['risk']:.1f}%)")
    lines.append("⚠️ اكتشاف آلي، راجع الشارت بنفسك")
    return "\n".join(lines)


def in_session(now):
    return now.weekday() in (6, 0, 1, 2, 3) and (10, 0) <= (now.hour, now.minute) <= (14, 35)


# ---------------- التشغيل ----------------
state = load_json(STATE_FILE, {})
if not isinstance(state, dict):
    state = {}
rec = state.get("_engulf")
if not isinstance(rec, dict):
    rec = {}
rec.setdefault("seen", {})
rec.setdefault("last", {})
nowts = time.time()
now = datetime.now(CAIRO)
today = now.strftime("%Y-%m-%d")
trading_day = now.weekday() in (6, 0, 1, 2, 3)
after_close = (not trading_day) or (now.hour, now.minute) >= (14, 40)


def closed_df(df, frame):
    """الشموع المقفولة بس. اليومي: شمعة النهاردة بتتحسب بعد القفل. الساعة: بنتجاهل آخر شمعة (لسه بتتكوّن)."""
    df = df.dropna(subset=["open", "high", "low", "close"])
    if frame == "يومي" and (str(df.index[-1])[:10] != today or after_close):
        return df
    return df.iloc[:-1]


last_id = int(state.get("_last_update_id", 0))
manual_chats = set()
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
    if parts and parts[0].split("@")[0].lower() in ("/engulf", "/engulfing"):
        manual_chats.add(chat)

frames = []
if manual_chats:
    frames = [f for f in ENABLED if f in CFG]
else:
    for name in ENABLED:
        cfg = CFG.get(name)
        if not cfg:
            continue
        if cfg["gate"] == "close":
            if trading_day and (now.hour, now.minute) >= AFTER_CLOSE and rec["last"].get(name) != today:
                frames.append(name)
        elif in_session(now) and nowts - float(rec["last"].get(name, 0)) >= cfg["gate"] * 60:
            frames.append(name)

if not frames:
    print("مفيش فحص مطلوب دلوقتي")
    sys.exit(0)

targets = list(manual_chats) if manual_chats else CHAT_IDS
tv = TvDatafeed()
found = []

for name in frames:
    cfg = CFG[name]
    for sym in SYMBOLS:
        try:
            df = tv.get_hist(symbol=sym, exchange="EGX", interval=cfg["interval"], n_bars=N_BARS)
            if df is None or df.empty:
                continue
            cdf = closed_df(df, name)
            shifts = range(0, MANUAL_RECENT) if manual_chats else [0]
            for sh in shifts:
                res = detect(cdf, cfg, sh)
                if not res:
                    continue
                key = f"{name}|{sym}|{res['bar']}"
                if not manual_chats and key in rec["seen"]:
                    break
                found.append((name, sym, key, res, df, sh))
                break
        except Exception as e:
            print(name, sym, "خطأ:", e)
    if not manual_chats:
        rec["last"][name] = today if cfg["gate"] == "close" else nowts

found.sort(key=lambda x: (x[3]["breakout"], x[3]["vm"] or 0), reverse=True)
print("إشارات:", len(found))

for idx, (name, sym, key, res, df, sh) in enumerate(found):
    note = f"(من {sh} شمعة)" if sh else ""
    cap = make_caption(name, sym, res, note)
    if idx < MAX_CHARTS:
        path = f"engulf_{idx}.png"
        try:
            make_chart(df, res, sym, name, path)
            send_photo(path, cap, targets)
        except Exception as e:
            print("خطأ في الرسم:", e)
            send(cap, targets)
    else:
        send(cap, targets)
    if not manual_chats:
        rec["seen"][key] = nowts

if not found and manual_chats:
    send("🟢 مفيش شموع ابتلاعية صاعدة بعد تجميع دلوقتي.", targets)

if not manual_chats:
    cutoff = nowts - 10 * 86400
    rec["seen"] = {k: v for k, v in rec["seen"].items() if v >= cutoff}
    state["_engulf"] = rec
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)
