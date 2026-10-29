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
RSI_CMDS = {"/excelrsi": "d", "/excelrsih": "h", "/excelrsim": "m"}
wanted = {}
open_wanted = set()
rsi_wanted = {}

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
    elif cmd == "/excelopen":
        open_wanted.add(chat)
    elif cmd in RSI_CMDS:
        rsi_wanted.setdefault(chat, [])
        if RSI_CMDS[cmd] not in rsi_wanted[chat]:
            rsi_wanted[chat].append(RSI_CMDS[cmd])

if wanted:
    trades = X.load_trades()
    for chat, periods in wanted.items():
        for period in periods:
            path, title, n = X.make_report(period, trades, state)
            X.send_document(path, chat, f"📊 تقرير Excel — {title} ({n} صفقة)")

if open_wanted or rsi_wanted:
    from tvDatafeed import TvDatafeed, Interval

    tv = TvDatafeed()
    FR = {"d": ("يومي", Interval.in_daily),
          "h": ("ساعة", Interval.in_1_hour),
          "m": ("15 دقيقة", Interval.in_15_minute)}

    if open_wanted:
        interval_map = {"15 دقيقة": Interval.in_15_minute,
                        "ساعة": Interval.in_1_hour,
                        "يومي": Interval.in_daily}
        prices = {}
        for key, pos in state.items():
            if isinstance(pos, dict) and pos.get("status") in ("HALF", "FULL") and "|" in key:
                sym, fr = key.split("|", 1)
                if fr in interval_map:
                    try:
                        df = tv.get_hist(symbol=sym, exchange="EGX",
                                         interval=interval_map[fr], n_bars=5)
                        if df is not None and not df.empty:
                            prices[key] = float(df["close"].iloc[-1])
                    except Exception as e:
                        print(sym, "خطأ:", e)
        path, n = X.make_open_report(state, prices)
        for chat in open_wanted:
            X.send_document(path, chat, f"📌 الصفقات المفتوحة الآن ({n})")

    if rsi_wanted:
        symbols = X.load_symbols()
        cache = {}
        for chat, keys in rsi_wanted.items():
            for key in keys:
                name, itv = FR[key]
                if key not in cache:
                    rows, missing = X.compute_rsi_rows(tv, symbols, itv)
                    cache[key] = X.make_rsi_report(rows, missing, name, key)
                path, n = cache[key]
                X.send_document(path, chat, f"📊 تقرير RSI — فريم {name} ({n} سهم)")