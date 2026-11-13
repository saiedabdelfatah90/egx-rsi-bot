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
STOP_BUFFER = 1.0         # الوقف تحت ذيل القاع بنسبة %
AFTER_CLOSE = (14, 45)    # الفحص اليومي بيبدأ بعد الوقت ده بتوقيت القاهرة
MAX_CHARTS = 8            # أقصى عدد صور في المرة الواحدة
N_BARS = 300
MANUAL_RECENT = 5         # في الفحص اليدوي (/reversal): نعرض الإشارات اللي ظهرت في آخر كام شمعة

# الفريم: الفريم، بحث عن القاع الأول A في آخر كام شمعة، الهبوط قبل A بكام شمعة،
#         أقل هبوط قبل A %، أقل ارتفاع A→B %، أقل وأقصى تصحيح من الموجة،
#         أقصى مدة متابعة بالشموع، التوقيت (close = مرة يومياً بعد القفل، أو كل كام دقيقة)
CFG = {
    "يومي": {"interval": Interval.in_daily, "look": 90, "pre": 60, "drop_min": 15.0, "leg_min": 8.0,
             "retr_min": 0.2, "retr_max": 0.85, "max_bars": 40, "gate": "close"},
    "ساعة": {"interval": Interval.in_1_hour, "look": 120, "pre": 100, "drop_min": 8.0, "leg_min": 4.0,
             "retr_min": 0.2, "retr_max": 0.85, "max_bars": 80, "gate": 55},
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


def detect(df, cfg, shift=0):
    """
    بيدور على: قاع A ← قمة صغيرة B ← قاع أعلى C (شمعة القاع j) ← شمعة تأكيد i (التالية لشمعة القاع).
    shift=0 يعني آخر شمعة مقفولة هي شمعة التأكيد. (آخر شمعة في البيانات ممكن تكون لسه بتتكوّن، فبنتجاهلها.)
    """
    closed = df.iloc[:len(df) - shift]         # df: الشموع المقفولة بس (شوف closed_df تحت)
    m = len(closed)
    if m < cfg["look"] + 20:
        return None
    H = closed["high"].to_numpy(dtype=float)
    L = closed["low"].to_numpy(dtype=float)
    C = closed["close"].to_numpy(dtype=float)

    i = m - 1                      # شمعة التأكيد (التالية لشمعة القاع)
    j = i - 1                      # شمعة القاع
    if L[i] < L[j]:                # الشمعة التالية كسرت القاع، يبقى مش قاع
        return None

    w0 = max(0, j - cfg["look"])
    a = w0 + int(np.argmin(L[w0:j]))           # A: أدنى قاع قبل القاع j
    if w0 > 0 and a < w0 + 3:                  # A على طرف النافذة، يعني الهبوط لسه مكمّل
        return None
    if L[j] <= L[a]:                           # لازم القاع C يكون أعلى من A
        return None
    if j - a < 6:
        return None
    b = a + 1 + int(np.argmax(H[a + 1:j]))     # B: أعلى قمة بين A و C
    if b >= j - 1:                             # القمة لازم تسبق القاع بشمعتين على الأقل
        return None
    if L[j] > L[b + 1:j + 1].min():            # j لازم يكون أدنى قاع بعد القمة
        return None
    if H[i] > H[b]:                            # القمة الصغيرة اتخترقت بالفعل
        return None

    pre0 = max(0, a - cfg["pre"])
    if a - pre0 < 10:
        return None
    drop = (H[pre0:a + 1].max() / L[a] - 1) * 100      # كان فيه هبوط قبل القاع A
    if drop < cfg["drop_min"]:
        return None
    leg = (H[b] / L[a] - 1) * 100
    if leg < cfg["leg_min"]:
        return None
    retr = (H[b] - L[j]) / (H[b] - L[a])
    if not (cfg["retr_min"] <= retr <= cfg["retr_max"]):
        return None

    a_low, b_high, c_low = float(L[a]), float(H[b]), float(L[j])
    entry = float(C[i])
    stop = c_low * (1 - STOP_BUFFER / 100)
    target = c_low + (b_high - a_low)          # فيبو 1: صفر عند القاع C، وارتفاع الموجة A→B
    if not (stop < entry < target and entry < b_high):
        return None
    risk = (entry - stop) / entry * 100
    reward = (target - entry) / entry * 100
    return {
        "a": a, "b": b, "j": j, "i": i,
        "a_low": a_low, "b_high": b_high, "c_low": c_low,
        "entry": entry, "stop": stop, "target": target,
        "risk": risk, "reward": reward, "rr": reward / risk if risk > 0 else 0.0,
        "drop": float(drop), "leg": float(leg), "retr": float(retr),
        "entry_bar": str(closed.index[i]), "c_bar": str(closed.index[j]),
        "a_bar": str(closed.index[a]), "b_bar": str(closed.index[b]),
    }


def evaluate(setup, df):
    """
    بيمشي على الشموع اللي بعد شمعة الدخول، ويرجع الأحداث بالترتيب:
    ("entry2", سعر)، ("stop", سعر)، ("target", سعر)، ("timeout", سعر)
    لو الوقف والهدف في نفس الشمعة بنحسب الوقف.
    """
    events = []
    e2 = setup.get("e2")
    nb = 0
    last_close = None
    for k in range(len(df)):
        if str(df.index[k]) <= setup["entry_bar"]:
            continue
        nb += 1
        o = float(df["open"].iloc[k])
        h = float(df["high"].iloc[k])
        l = float(df["low"].iloc[k])
        last_close = float(df["close"].iloc[k])
        if l <= setup["stop"]:
            events.append(("stop", min(o, setup["stop"])))
            return events, e2
        if e2 is None and h > setup["b_high"]:
            e2 = max(o, setup["b_high"])
            events.append(("entry2", e2))
        if h >= setup["target"]:
            events.append(("target", max(o, setup["target"])))
            return events, e2
    if nb >= setup.get("max_bars", 40) and last_close is not None:
        events.append(("timeout", last_close))
    return events, e2


def make_chart(df, r, sym, frame, path):
    try:
        import matplotlib
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "matplotlib"], check=False)
        import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    start = max(0, r["a"] - 12)
    d = df.iloc[start:]
    O = d["open"].to_numpy(dtype=float)
    H = d["high"].to_numpy(dtype=float)
    L = d["low"].to_numpy(dtype=float)
    C = d["close"].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(9, 5.5))
    for k in range(len(d)):
        col = "#2e7d32" if C[k] >= O[k] else "#c62828"
        ax.vlines(k, L[k], H[k], color=col, linewidth=1)
        body_h = max(abs(C[k] - O[k]), (H[k] - L[k]) * 0.03, 1e-9)
        ax.add_patch(Rectangle((k - 0.3, min(O[k], C[k])), 0.6, body_h, color=col))

    ax_i, bx_i, cx_i = r["a"] - start, r["b"] - start, r["j"] - start
    end = len(d) + 1
    # A → B → C بخط متقطع زي الرسم بتاعك
    ax.plot([ax_i, bx_i, cx_i], [r["a_low"], r["b_high"], r["c_low"]], "k--", linewidth=1.5)
    ax.hlines(r["b_high"], bx_i, end, colors="#8e24aa", linewidth=1.3)      # القمة الصغيرة
    ax.hlines(r["c_low"], cx_i, end, colors="#8e24aa", linewidth=1.3)       # قاع الذيل
    ax.hlines(r["stop"], cx_i, end, colors="#c62828", linestyles=":", linewidth=1.3)
    unit = r["b_high"] - r["a_low"]
    for ratio in (0.5, 0.618, 1.0, 1.618):
        y = r["c_low"] + ratio * unit
        ax.hlines(y, cx_i, end, colors="#1565c0" if ratio != 1.0 else "#2e7d32",
                  linewidth=1.8 if ratio == 1.0 else 0.9)
        ax.text(end + 0.3, y, f"{ratio:g}", va="center", fontsize=8)
    ax.text(end + 0.3, r["stop"], "SL", va="center", fontsize=8, color="#c62828")
    try:
        import posbox
        posbox.draw_position(ax, cx_i + 1, end, r["entry"], r["stop"], r["target"])
    except Exception as e:
        print("posbox:", e)
    ax.set_title(f"{sym} | {FRAME_EN.get(frame, frame)} | Higher-low reversal | RR {r['rr']:.1f}")
    ax.set_xlim(-1, end + 4)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def entry_caption(frame, sym, r, note=""):
    lines = [f"📐 قاع صاعد — إشارة دخول أولى {note}".strip(),
             f"السهم: {sym}",
             f"الفريم: {frame}",
             f"الدخول الأول (سعر التنبيه): {r['entry']:.2f}",
             f"قاع الذيل: {r['c_low']:.2f} | الوقف: {r['stop']:.2f} (-{r['risk']:.1f}% من الدخول)",
             f"القمة الصغيرة (الدخول التاني عند اختراقها): {r['b_high']:.2f}",
             f"الهدف (فيبو 1): {r['target']:.2f} (+{r['reward']:.1f}%)",
             f"عائد ÷ مخاطرة: {r['rr']:.1f}",
             f"الموجة الأولى: +{r['leg']:.1f}% | التصحيح: {r['retr'] * 100:.0f}% | الهبوط قبلها: {r['drop']:.0f}%",
             "⚠️ اكتشاف آلي، راجع الشارت بنفسك"]
    return "\n".join(lines)


def in_session(now):
    return now.weekday() in (6, 0, 1, 2, 3) and (10, 0) <= (now.hour, now.minute) <= (14, 35)


# ---------------- التشغيل ----------------
state = load_json(STATE_FILE, {})
if not isinstance(state, dict):
    state = {}
rec = state.get("_reversal")
if not isinstance(rec, dict):
    rec = {}
rec.setdefault("open", {})
rec.setdefault("seen", {})
rec.setdefault("last", {})
dirty = False
nowts = time.time()
now = datetime.now(CAIRO)
today = now.strftime("%Y-%m-%d")

after_close = now.weekday() not in (6, 0, 1, 2, 3) or (now.hour, now.minute) >= (14, 40)


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
    if parts and parts[0].split("@")[0].lower() in ("/reversal", "/higherlow"):
        manual_chats.add(chat)

frames = []
if manual_chats:
    frames = [f for f in ENABLED if f in CFG]
else:
    for name in ENABLED:
        cfg = CFG.get(name)
        if not cfg:
            continue
        trading_day = now.weekday() in (6, 0, 1, 2, 3)
        if cfg["gate"] == "close":
            if trading_day and (now.hour, now.minute) >= AFTER_CLOSE and rec["last"].get(name) != today:
                frames.append(name)
        elif in_session(now) and nowts - float(rec["last"].get(name, 0)) >= cfg["gate"] * 60:
            frames.append(name)

if not frames and not rec["open"]:
    print("مفيش فحص مطلوب ومفيش إشارات مفتوحة")
    sys.exit(0)

tv = TvDatafeed()


def fetch(sym, frame, n=N_BARS):
    try:
        return tv.get_hist(symbol=sym, exchange="EGX", interval=CFG[frame]["interval"], n_bars=n)
    except Exception as e:
        print(sym, "خطأ بيانات:", e)
        return None


# ---- 1) متابعة الإشارات المفتوحة ----
for key, st in list(rec["open"].items()):
    frame = st["tf"]
    if frame not in CFG:
        continue
    df = fetch(st["sym"], frame, 120)
    if df is None or df.empty:
        continue
    events, e2 = evaluate(st, df)
    avg = st["entry"] if e2 is None else (st["entry"] + e2) / 2
    for kind, price in events:
        if kind == "entry2":
            st["e2"] = price
            dirty = True
            risk = (avg - st["stop"]) / avg * 100
            rr = (st["target"] - avg) / avg * 100 / risk if risk > 0 else 0
            send(f"📐 اختراق القمة الصغيرة — دخول ثاني\n"
                 f"السهم: {st['sym']} | الفريم: {frame}\n"
                 f"الدخول الثاني: {price:.2f} (القمة {st['b_high']:.2f})\n"
                 f"متوسط الدخول (نصف ونصف): {avg:.2f}\n"
                 f"الوقف: {st['stop']:.2f} | الهدف: {st['target']:.2f}\n"
                 f"عائد ÷ مخاطرة من المتوسط: {rr:.1f}", CHAT_IDS)
        else:
            result = (price - avg) / avg * 100
            title = {"stop": "❌ ضرب الوقف", "target": "✅ تحقق الهدف (فيبو 1)",
                     "timeout": "⏱️ انتهت مدة المتابعة"}[kind]
            send(f"{title} — قاع صاعد\n"
                 f"السهم: {st['sym']} | الفريم: {frame}\n"
                 f"متوسط الدخول: {avg:.2f} ({'دخولين' if e2 is not None else 'دخول واحد'})\n"
                 f"الخروج: {price:.2f}\n"
                 f"النتيجة: {result:+.2f}%", CHAT_IDS)
            rec["open"].pop(key, None)
            dirty = True

# ---- 2) فحص إشارات جديدة ----
if frames:
    targets = list(manual_chats) if manual_chats else CHAT_IDS
    found = []
    for name in frames:
        cfg = CFG[name]
        for sym in SYMBOLS:
            df = fetch(sym, name)
            if df is None or df.empty:
                continue
            try:
                cdf = closed_df(df, name)
                shifts = range(0, MANUAL_RECENT) if manual_chats else [0]
                res = None
                used = 0
                for sh in shifts:
                    res = detect(cdf, cfg, sh)
                    if res:
                        used = sh
                        break
                if not res:
                    continue
                key = f"{name}|{sym}|{res['a_bar']}|{res['c_bar']}"
                if manual_chats:
                    # بنعرض بس الإشارات اللي لسه صالحة
                    setup = {"entry_bar": res["entry_bar"], "stop": res["stop"], "b_high": res["b_high"],
                             "target": res["target"], "max_bars": cfg["max_bars"]}
                    events, _ = evaluate(setup, df)
                    if any(k in ("stop", "target", "timeout") for k, _ in events):
                        continue
                    found.append((name, sym, key, res, df, used))
                else:
                    if key in rec["seen"]:
                        continue
                    found.append((name, sym, key, res, df, 0))
            except Exception as e:
                print(name, sym, "خطأ:", e)
        if not manual_chats:
            rec["last"][name] = today if cfg["gate"] == "close" else nowts
            dirty = True

    found.sort(key=lambda x: x[3]["rr"], reverse=True)
    print("إشارات:", len(found))

    for idx, (name, sym, key, res, df, used) in enumerate(found):
        note = f"(من {used} شمعة)" if used else ""
        cap = entry_caption(name, sym, res, note)
        if idx < MAX_CHARTS:
            path = f"reversal_{idx}.png"
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
            rec["open"][key] = {
                "sym": sym, "tf": name, "entry": res["entry"], "stop": res["stop"], "target": res["target"],
                "b_high": res["b_high"], "c_low": res["c_low"], "a_low": res["a_low"],
                "entry_bar": res["entry_bar"], "entry_time": now.strftime("%Y-%m-%d %H:%M"),
                "max_bars": CFG[name]["max_bars"], "e2": None}
            dirty = True

    if not found and manual_chats:
        send("📐 مفيش إشارات قاع صاعد صالحة دلوقتي.", targets)

if not manual_chats:
    cutoff = nowts - 30 * 86400
    rec["seen"] = {k: v for k, v in rec["seen"].items() if v >= cutoff}

if dirty:
    state["_reversal"] = rec
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)
