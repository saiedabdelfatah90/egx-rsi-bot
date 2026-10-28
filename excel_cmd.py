import os

import requests

import excel_report as X

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["CHAT_ID"].split(",") if c.strip()]

state = X.load_json("state.json", {})
if not isinstance(state, dict):
    state = {}

# بنقرا الرسايل من غير ما نأكدها، عشان commands.py يقراها بعدنا
last = int(state.get("_last_update_id", 0))
try:
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                     params={"offset": last + 1, "timeout": 0}, timeout=20).json()
except Exception as e:
    print("getUpdates:", e)
    r = {}

PERIODS = {"/excel": "today", "/excelweek": "week", "/excelall": "all"}
wanted = {}
for u in r.get("result", []):
    msg = u.get("message")
    if not msg:
        continue
    chat = str(msg["chat"]["id"])
    if chat not in CHAT_IDS:
        continue
    parts = msg.get("text", "").strip().split()
    if not parts:
        continue
    cmd = parts[0].split("@")[0].lower()
    if cmd in PERIODS:
        wanted.setdefault(chat, [])
        if PERIODS[cmd] not in wanted[chat]:
            wanted[chat].append(PERIODS[cmd])

if wanted:
    trades = X.load_trades()
    for chat, periods in wanted.items():
        for period in periods:
            path, title, n = X.make_report(period, trades, state)
            X.send_document(path, chat, f"📊 تقرير Excel — {title} ({n} صفقة)")
