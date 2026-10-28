import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import excel_report as X

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["CHAT_ID"].split(",") if c.strip()]
FRAME_ORDER = X.FRAME_ORDER


def send(text):
    for chat in CHAT_IDS:
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        except Exception as e:
            print("خطأ في الإرسال:", e)


def block(title, items):
    w, l, rate, sw, sl = X.stats(items)
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
state = X.load_json("state.json", {})
if not isinstance(state, dict):
    state = {}
trades = X.load_trades()
todays = [t for t in trades if t.get("date") == today]

parts = [f"📅 تقرير يوم {today}", ""]
for fr in FRAME_ORDER:
    parts.append(block(f"فريم {fr}", [t for t in todays if t.get("frame") == fr]))
    parts.append("")
parts.append(block("الإجمالي", todays))

open_counts = {fr: 0 for fr in FRAME_ORDER}
for key, pos in state.items():
    if isinstance(pos, dict) and pos.get("status") in ("HALF", "FULL") and "|" in key:
        fr = key.split("|", 1)[-1]
        if fr in open_counts:
            open_counts[fr] += 1
parts.append("")
parts.append("🔓 صفقات لسه مفتوحة:")
parts += [f"{fr}: {n}" for fr, n in open_counts.items()]

send("\n".join(parts))

# ملف Excel
path, title, n = X.make_report("today", trades, state)
for chat in CHAT_IDS:
    X.send_document(path, chat, f"📊 تقرير Excel — {title} ({n} صفقة)")
