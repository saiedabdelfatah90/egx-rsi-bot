import ast
import json
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import requests
from tvDatafeed import TvDatafeed, Interval

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["CHAT_ID"].split(",") if c.strip()]
STATE_FILE = "state.json"
CAIRO = ZoneInfo("Africa/Cairo")
LRM = "\u200e"

# نقرا قايمة الأسهم من bot.py
SYMBOLS = []
with open("bot.py", encoding="utf-8") as f:
    for node in ast.parse(f.read()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "SYMBOLS":
            SYMBOLS = ast.literal_eval(node.value)

# ---------------- الإعدادات ----------------
AFTER_CLOSE = (14, 45)    # التقرير التلقائي بيتبعت بعد الوقت ده بتوقيت القاهرة
HIGH_LOOKBACK = 40        # أعلى قمة خلال آخر كام شمعة يومية
LOW_LOOKBACK = 40         # قاع الموجة قبل القمة بكام شمعة
MIN_IMPULSE = 8.0         # أقل ارتفاع للموجة الصاعدة %
FIB_LO, FIB_HI = 0.5, 0.618
FIB_TOL = 0.03            # سماحية حوالين منطقة فيبو
VOL_MAX = 0.85            # أقصى نسبة: حجم التصحيح ÷ متوسط الحجم
TOP_A = 10                # أقصى عدد أسهم مطابقة في التقرير
TOP_B = 10                # أقصى عدد أسهم ناقصها شرط
MAX_CHARTS = 3            # عدد صور الشارت
N_BARS = 300
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


def _post(text, chat):
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      data={"chat_id": chat, "text": text}, timeout=20)
    except Exception as e:
        print("خطأ في الإرسال:", e)


def send_blocks(text, chats):
    for chat in chats:
        chunk = ""
        for block in text.split("\n\n"):
            if chunk and len(chunk) + len(block) + 2 > 3500:
                _post(chunk, chat)
                chunk = ""
            chunk += block + "\n\n"
        if chunk.strip():
            _post(chunk, chat)


def send_photo(path, caption, chats):
    for chat in chats:
        try:
            with open(path, "rb") as f:
                requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                              data={"chat_id": chat, "caption": caption},
                              files={"photo": f}, timeout=60)
        except Exception as e:
            print("خطأ في إرسال الصورة:", e)


def find_hammer(df, k):
    O = df["open"].to_numpy(dtype=float)
    H = df["high"].to_numpy(dtype=float)
    L = df["low"].to_numpy(dtype=float)
    C = df["close"].to_numpy(dtype=float)
    n = len(df)
    for i in range(n - 1, max(n - 1 - k, 2), -1):
        rng = H[i] - L[i]
        if rng <= 0:
            continue
        body = abs(C[i] - O[i])
        lower = min(O[i], C[i]) - L[i]
        upper = H[i] - max(O[i], C[i])
        if (lower >= 2 * body and lower >= 0.6 * rng and upper <= 0.25 * rng
                and C[i - 3] > C[i - 1]):
            return True
    return False


def analyze(sym, dfd, dfh, last_daily_complete):
    dfd = dfd.dropna(subset=["open", "high", "low", "close"])
    dfh = dfh.dropna(subset=["open", "high", "low", "close"])
    n = len(dfd)
    if n < 100 or len(dfh) < 40:
        return None
    cd = dfd["close"]
    price = float(dfh["close"].iloc[-1])

    # 1) اتجاه يومي صاعد
    sma50 = cd.rolling(50).mean()
    s50 = float(sma50.iloc[-1])
    s50_prev = float(sma50.iloc[-11])
    if s50 != s50 or s50_prev != s50_prev:
        return None
    s200 = float(cd.rolling(200).mean().iloc[-1]) if n >= 200 else None
    up = s50 > s50_prev
    if s200 is not None:
        up = up and s50 > s200 and price > s200
    else:
        up = up and price > s50 * 0.95

    # 2) تصحيح (هبوط) على الساعة
    h20 = dfh["close"].rolling(20).mean()
    down = bool(price < float(h20.iloc[-1]) and float(h20.iloc[-1]) < float(h20.iloc[-6]))

    # 3) فيبو من أعلى قمة
    H = dfd["high"].to_numpy(dtype=float)
    L = dfd["low"].to_numpy(dtype=float)
    V = dfd["volume"].to_numpy(dtype=float) if "volume" in dfd.columns else None
    look = min(HIGH_LOOKBACK, n)
    hi_i = n - look + int(np.argmax(H[n - look:]))
    lo_start = max(0, hi_i - LOW_LOOKBACK)
    lo_i = swing_high = swing_low = retr = None
    fib_ok = False
    if hi_i - lo_start >= 5 and n - 1 - hi_i >= 2:
        lo_i = lo_start + int(np.argmin(L[lo_start:hi_i]))
        swing_high = float(H[hi_i])
        swing_low = float(L[lo_i])
        if swing_low > 0 and swing_high > swing_low:
            impulse = (swing_high / swing_low - 1) * 100
            retr = (swing_high - price) / (swing_high - swing_low)
            fib_ok = (impulse >= MIN_IMPULSE
                      and (FIB_LO - FIB_TOL) <= retr <= (FIB_HI + FIB_TOL))

    # 4) حجم التصحيح
    vol_ok = False
    vr = None
    if V is not None and swing_high is not None:
        Vx = V if last_daily_complete else V[:-1]
        corr = Vx[hi_i + 1:]
        base = float(np.mean(Vx[-50:])) if len(Vx) >= 20 else 0.0
        if len(corr) >= 1 and base > 0:
            vr = float(np.mean(corr) / base)
            vol_ok = vr <= VOL_MAX

    # 5) هامر
    ham_d = find_hammer(dfd, 2)
    ham_h = find_hammer(dfh, 3)

    crit = [("D-Trend", up), ("1H-Down", down), ("Fib", fib_ok), ("Volume", vol_ok)]
    fails = [k for k, v in crit if not v]
    score = 25 * (4 - len(fails))
    if retr is not None:
        score += 10 * max(0.0, 1 - min(abs(retr - 0.559) / 0.15, 1))
    if vr is not None:
        score += 10 * max(0.0, (VOL_MAX - vr) / VOL_MAX)
    if ham_d:
        score += 15
    if ham_h:
        score += 10
    if price > s50:
        score += 5

    return {"sym": sym, "price": price, "fails": fails, "score": float(score),
            "retr": retr, "vr": vr, "ham_d": ham_d, "ham_h": ham_h,
            "hi_i": hi_i, "lo_i": lo_i, "high": swing_high, "low": swing_low,
            "df": dfd}


def make_chart(r, path):
    try:
        import matplotlib
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "matplotlib"], check=False)
        import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    df = r["df"]
    start = max(0, r["lo_i"] - 5)
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

    hi_x = r["hi_i"] - start
    lo_x = r["lo_i"] - start
    hi, lo = r["high"], r["low"]
    ax.plot([lo_x, hi_x], [lo, hi], color="#1565c0", linewidth=2)
    for lvl, colr in ((0.382, "#9e9e9e"), (0.5, "#ef6c00"), (0.618, "#ef6c00"), (0.786, "#9e9e9e")):
        y = hi - lvl * (hi - lo)
        ax.hlines(y, hi_x, len(d) + 1, colors=colr, linestyles="--", linewidth=1)
        ax.text(len(d) + 1.2, y, f"{lvl}", va="center", fontsize=8)
    ax.axhline(r["price"], color="#6a1b9a", linestyle=":", linewidth=1)
    ax.set_title(f"{r['sym']} | Daily | Fib {r['retr'] * 100:.0f}% | Score {r['score']:.0f}")
    ax.set_xlim(-1, len(d) + 4)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def block(r, with_missing=False):
    ham = " + ".join(x for x, f in (("Daily", r["ham_d"]), ("1H", r["ham_h"])) if f) or "—"
    fib = f"{r['retr'] * 100:.0f}%" if r["retr"] is not None else "—"
    vol = f"{r['vr']:.2f}x" if r["vr"] is not None else "—"
    lines = [f"{LRM}🔹 {r['sym']}  ·  {r['price']:.2f}  ·  Score {r['score']:.0f}",
             f"{LRM}Fib {fib}  |  Vol {vol}  |  Hammer {ham}"]
    if r["high"] is not None:
        z50 = r["high"] - 0.5 * (r["high"] - r["low"])
        z62 = r["high"] - 0.618 * (r["high"] - r["low"])
        lines.append(f"{LRM}Zone {z62:.2f} – {z50:.2f}  |  High {r['high']:.2f}  |  Low {r['low']:.2f}")
    if with_missing:
        lines.append(f"{LRM}Missing: " + ", ".join(r["fails"]))
    return "\n".join(lines)


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
    if parts and parts[0].split("@")[0].lower() in ("/best", "/picks"):
        manual_chats.add(chat)

now = datetime.now(CAIRO)
today = now.strftime("%Y-%m-%d")
trading_day = now.weekday() in (6, 0, 1, 2, 3)
after_close = (not trading_day) or (now.hour, now.minute) >= (14, 40)

auto = False
if not manual_chats and trading_day and (now.hour, now.minute) >= AFTER_CLOSE \
        and state.get("_best_date") != today:
    auto = True

if not manual_chats and not auto:
    print("مفيش تقرير مطلوب دلوقتي")
    raise SystemExit

targets = list(manual_chats) if manual_chats else CHAT_IDS
tv = TvDatafeed()
results = []

for sym in SYMBOLS:
    try:
        dfd = tv.get_hist(symbol=sym, exchange="EGX", interval=Interval.in_daily, n_bars=N_BARS)
        dfh = tv.get_hist(symbol=sym, exchange="EGX", interval=Interval.in_1_hour, n_bars=N_BARS)
        if dfd is None or dfd.empty or dfh is None or dfh.empty:
            continue
        complete = after_close or str(dfd.index[-1])[:10] != today
        res = analyze(sym, dfd, dfh, complete)
        if res:
            results.append(res)
    except Exception as e:
        print(sym, "خطأ:", e)

A = sorted([r for r in results if not r["fails"]], key=lambda r: r["score"], reverse=True)
B = sorted([r for r in results if len(r["fails"]) == 1], key=lambda r: r["score"], reverse=True)
print("مطابقة:", len(A), "| ناقصها شرط:", len(B))

bar = "━━━━━━━━━━━━━━━━"
lines = ["🎯 أفضل الأسهم للشراء — تصحيح فيبو",
         "🕐 " + now.strftime("%Y-%m-%d  %H:%M"),
         "",
         "الشروط: اتجاه يومي صاعد + تصحيح على الساعة + السعر في منطقة فيبو 0.5 إلى 0.618 + حجم تصحيح منخفض. الهامر بيزوّد النقاط.",
         "",
         f"✅ مطابقة لكل الشروط · عدد الأسهم: {len(A)}",
         f"🔸 ناقصها شرط واحد · عدد الأسهم: {len(B)}",
         ""]

lines += [f"{bar}\n✅ مطابقة لكل الشروط\n{bar}", ""]
if A:
    for r in A[:TOP_A]:
        lines += [block(r), ""]
else:
    lines += ["مفيش أسهم بتحقق كل الشروط دلوقتي.", ""]

if B:
    lines += [f"{bar}\n🔸 ناقصها شرط واحد\n{bar}", "",
              "D-Trend = الاتجاه اليومي | 1H-Down = تصحيح الساعة | Fib = منطقة فيبو | Volume = حجم التصحيح",
              ""]
    for r in B[:TOP_B]:
        lines += [block(r, with_missing=True), ""]

lines += ["⚠️ فلتر آلي لترتيب المرشحين، مش توصية شراء. راجع الشارت بنفسك."]
send_blocks("\n".join(lines), targets)

for i, r in enumerate(A[:MAX_CHARTS]):
    path = f"best_{i}.png"
    try:
        make_chart(r, path)
        send_photo(path, f"{r['sym']} — شارت يومي بمستويات فيبو", targets)
    except Exception as e:
        print("خطأ في الرسم:", e)

if auto:
    state["_best_date"] = today
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)