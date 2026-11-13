"""رسم مربع صفقة شراء زي أداة Long Position في TradingView:
مربع أخضر من الدخول للهدف، ومربع أحمر من الدخول للوقف، مع النسب ونسبة العائد للمخاطرة.
أي خطأ هنا مبيوقفش التنبيه: الصورة بتتبعت عادي من غير المربع."""


def calc(entry, stop, target):
    try:
        entry, stop, target = float(entry), float(stop), float(target)
    except (TypeError, ValueError):
        return None
    if not (0 < stop < entry < target):
        return None
    risk = (entry - stop) / entry * 100
    reward = (target - entry) / entry * 100
    return {"entry": entry, "stop": stop, "target": target,
            "risk": risk, "reward": reward, "rr": reward / risk}


def caption_line(entry, stop, target):
    p = calc(entry, stop, target)
    if not p:
        return None
    return (f"📦 دخول {p['entry']:.2f} | وقف {p['stop']:.2f} (-{p['risk']:.1f}%) | "
            f"هدف {p['target']:.2f} (+{p['reward']:.1f}%) | عائد÷مخاطرة {p['rr']:.1f}")


def draw_position(ax, x0, x1, entry, stop, target):
    try:
        p = calc(entry, stop, target)
        if not p:
            return False
        from matplotlib.patches import Rectangle
        w = x1 - x0
        green, red = "#26a69a", "#ef5350"
        ax.add_patch(Rectangle((x0, p["entry"]), w, p["target"] - p["entry"], facecolor=green,
                               alpha=0.25, edgecolor=green, linewidth=1, zorder=1))
        ax.add_patch(Rectangle((x0, p["stop"]), w, p["entry"] - p["stop"], facecolor=red,
                               alpha=0.25, edgecolor=red, linewidth=1, zorder=1))
        ax.hlines(p["entry"], x0, x1, colors="#37474f", linewidth=1.4, zorder=3)
        xc = x0 + w / 2
        box = {"boxstyle": "round,pad=0.25", "linewidth": 0}
        ax.text(xc, p["target"], f"Target {p['target']:.2f}  (+{p['reward']:.1f}%)", ha="center",
                va="top", fontsize=8, color="white", zorder=5, bbox={**box, "facecolor": green})
        ax.text(xc, p["stop"], f"Stop {p['stop']:.2f}  (-{p['risk']:.1f}%)", ha="center",
                va="bottom", fontsize=8, color="white", zorder=5, bbox={**box, "facecolor": red})
        ax.text(xc, p["entry"], f"Entry {p['entry']:.2f}  |  R/R {p['rr']:.1f}", ha="center",
                va="center", fontsize=8, color="white", zorder=5, bbox={**box, "facecolor": "#37474f"})
        lo, hi = ax.get_ylim()
        pad = (p["target"] - p["stop"]) * 0.06
        ax.set_ylim(min(lo, p["stop"] - pad), max(hi, p["target"] + pad))
        return True
    except Exception as e:
        print("posbox:", e)
        return False
