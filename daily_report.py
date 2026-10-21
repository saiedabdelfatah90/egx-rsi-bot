import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
STOCH_NAME = "30 دقيقة استوكاستيك"
FRAME_ORDER = ["15 دقيقة", "ساعة", "يومي", STOCH_NAME]


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def send(text):
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                  data={"chat_id": CHAT_ID, "text": text}, timeout=20)


def stats(items):
    wins = [t["pnl"] for t in items if t["pnl"] > 0]
    losses = [t["pnl"] for t in items if t["pnl"] <= 0]
    total = len(items)
    rate = (len(wins) / total * 100) if total else 0
    return len(wins), len(losses), rate, sum(wins), sum(losses)


def block(title, items):
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


today = datetime.now(ZoneInfo("Africa/Cairo")).strftime("%Y-%m-%d")
trades = load_json("trades.json", [])
state = load_json("state.json", {})
stoch_state = load_json("stoch_state.json", {})
todays = [t for t in trades if t.get("date") == today]

parts = [f"📅 تقرير يوم {today}", ""]
for fr in FRAME_ORDER:
    parts.append(block(f"فريم {fr}", [t for t in todays if t["frame"] == fr]))
    parts.append("")
parts.append(block("الإجمالي", todays))

open_counts = {fr: 0 for fr in FRAME_ORDER}
for key, pos in state.items():
    if isinstance(pos, dict) and pos.get("status") in ("HALF", "FULL") and "|" in key:
        fr = key.split("|", 1)[-1]
        if fr in open_counts:
            open_counts[fr] += 1
open_counts[STOCH_NAME] = sum(1 for p in stoch_state.values()
                              if isinstance(p, dict) and p.get("status") == "OPEN")
parts.append("")
parts.append("🔓 صفقات لسه مفتوحة:")
parts += [f"{fr}: {n}" for fr, n in open_counts.items()]

send("\n".join(parts))