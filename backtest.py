import os
from datetime import datetime
from statistics import mean, median

import requests
from openpyxl import Workbook
from openpyxl.styles import Font
from tvDatafeed import TvDatafeed, Interval

import excel_report as X

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_IDS = [c.strip() for c in os.environ["CHAT_ID"].split(",") if c.strip()]


def env_f(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


# ---------------- الإعدادات ----------------
BUY1_RSI = env_f("BUY1_RSI", 20)      # الشراء الأول عند RSI
BUY2_DROP = env_f("BUY2_DROP", 5)     # الشراء التاني بعد نزول % من الأول
STOP_PCT = env_f("STOP_PCT", 7)       # وقف الخسارة % تحت الشراء الأول
FEE_PCT = env_f("FEE_PCT", 0.15)      # العمولة % لكل جانب (شراء أو بيع)
FRAMES_WANTED = (os.environ.get("FRAMES") or "d,h,m").replace(" ", "").split(",")
N_BARS = 5000

# الفريم: (الاسم, الفريم, الهدف الأول %, الهدف الثاني %)
FR = {
    "d": ("يومي", Interval.in_daily, 5, 10),
    "h": ("ساعة", Interval.in_1_hour, 5, 10),
    "m": ("15 دقيقة", Interval.in_15_minute, 3, 6),
}
# --------------------------------------------


def send(text):
    for chat in CHAT_IDS:
        try:
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        except Exception as e:
            print("خطأ في الإرسال:", e)


def backtest(sym, name, df, t1p, t2p):
    df = df.dropna(subset=["open", "high", "low", "close"])
    if len(df) < 250:
        return [], 0
    O = df["open"].to_numpy(dtype=float)
    H = df["high"].to_numpy(dtype=float)
    L = df["low"].to_numpy(dtype=float)
    C = df["close"].to_numpy(dtype=float)
    R = X._rsi(df["close"]).to_numpy(dtype=float)
    S200 = df["close"].rolling(200).mean().to_numpy(dtype=float)
    idx = df.index
    fmt = "%Y-%m-%d" if name == "يومي" else "%Y-%m-%d %H:%M"

    trades = []
    status = "NONE"
    pos = None

    def record(i, reason, gross):
        trades.append({
            "symbol": sym,
            "frame": name,
            "entry_date": idx[pos["i"]].strftime(fmt),
            "exit_date": idx[i].strftime(fmt),
            "entry": pos["first"],
            "avg": pos["avg"],
            "buys": pos["buys"],
            "gross": gross,
            "pnl": gross - 2 * FEE_PCT,
            "reason": reason,
            "above200": pos["above200"],
            "days": (idx[i] - idx[pos["i"]]).total_seconds() / 86400,
        })

    for i in range(len(df)):
        r = R[i]
        if r != r:
            continue
        o, h, l, c = O[i], H[i], L[i], C[i]

        if status == "WAIT":
            if r > BUY1_RSI:
                status = "NONE"
            else:
                continue

        if status == "NONE":
            if r <= BUY1_RSI:
                s200 = S200[i]
                pos = {"i": i, "first": c, "avg": c, "buys": 1, "t1": False,
                       "realized": 0.0,
                       "above200": (bool(c > s200) if s200 == s200 else None)}
                status = "OPEN"
            continue

        first = pos["first"]

        # 1) الشراء التاني
        if pos["buys"] == 1 and not pos["t1"]:
            lvl = first * (1 - BUY2_DROP / 100)
            if l <= lvl:
                fill = min(o, lvl)
                pos["avg"] = (first + fill) / 2
                pos["buys"] = 2
        avg = pos["avg"]
        remaining = 0.5 if pos["t1"] else 1.0

        # 2) وقف الخسارة
        stop = first * (1 - STOP_PCT / 100)
        if l <= stop:
            fill = min(o, stop)
            gross = pos["realized"] + remaining * (fill - avg) / avg * 100
            record(i, "t1+stop" if pos["t1"] else "stop", gross)
            status = "WAIT"
            continue

        # 3) الأهداف
        t1 = avg * (1 + t1p / 100)
        t2 = avg * (1 + t2p / 100)
        if h >= t2:
            f2 = max(o, t2)
            if pos["t1"]:
                gross = pos["realized"] + 0.5 * (f2 - avg) / avg * 100
            else:
                f1 = max(o, t1)
                gross = 0.5 * (f1 - avg) / avg * 100 + 0.5 * (f2 - avg) / avg * 100
            record(i, "t1+t2", gross)
            status = "WAIT"
            continue
        if (not pos["t1"]) and h >= t1:
            f1 = max(o, t1)
            pos["t1"] = True
            pos["realized"] = 0.5 * (f1 - avg) / avg * 100

    return trades, (1 if status == "OPEN" else 0)


def stats(ts):
    n = len(ts)
    if n == 0:
        return None
    pn = [t["pnl"] for t in ts]
    wins = [p for p in pn if p > 0]
    losses = [p for p in pn if p <= 0]
    gl = -sum(losses)
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "avg_win": mean(wins) if wins else 0.0,
        "avg_loss": mean(losses) if losses else 0.0,
        "pf": (sum(wins) / gl) if gl > 0 else None,
        "exp": sum(pn) / n,
        "total": sum(pn),
        "worst": min(pn),
        "median": median(pn),
        "days": mean([t["days"] for t in ts]),
    }


HEAD = ["التصنيف", "عدد الصفقات", "نسبة النجاح", "متوسط الربح", "متوسط الخسارة",
        "معامل الربح", "متوسط الصفقة", "المجموع", "أسوأ صفقة", "متوسط المدة (يوم)"]


def stat_values(label, ts):
    s = stats(ts)
    if s is None:
        return [label, 0] + ["—"] * 8
    pf = "∞" if s["pf"] is None else round(s["pf"], 2)
    return [label, s["n"], s["win_rate"], s["avg_win"], s["avg_loss"], pf,
            s["exp"], s["total"], s["worst"], s["days"]]


def write_rows(ws, start, rows):
    r = start
    for row in rows:
        for col, v in enumerate(row, start=1):
            c = ws.cell(row=r, column=col, value=v)
            c.border = X.BORDER
            c.alignment = X.CENTER
        ws.cell(row=r, column=3).number_format = '0.0"%"'
        for col in (4, 5, 7, 8, 9):
            ws.cell(row=r, column=col).number_format = X.PCT_SIGNED
        ws.cell(row=r, column=6).number_format = "0.00"
        ws.cell(row=r, column=10).number_format = "0.0"
        exp = row[6]
        if isinstance(exp, (int, float)):
            ws.cell(row=r, column=7).fill = X.GREEN if exp > 0 else X.RED
        r += 1
    return r


def table_sheet(wb, title, rows):
    ws = wb.create_sheet(title)
    ws.sheet_view.rightToLeft = True
    X._header(ws, 1, HEAD)
    write_rows(ws, 2, rows)
    X._widths(ws, [34, 12, 13, 13, 14, 12, 14, 12, 12, 16])
    ws.freeze_panes = "A2"
    return ws


def build_excel(all_trades, frame_names, open_counts, missing, path):
    wb = Workbook()
    ws = wb.active
    ws.title = "الملخص"
    ws.sheet_view.rightToLeft = True
    ws["A1"] = "نتيجة الاختبار التاريخي لقواعد البوت"
    ws["A1"].font = X.TITLE_FONT
    ws["A2"] = "تم الإنشاء: " + datetime.now(X.CAIRO).strftime("%Y-%m-%d %H:%M")
    ws["A3"] = (f"الإعدادات: RSI الشراء ≤ {BUY1_RSI:g} | الشراء التاني بعد -{BUY2_DROP:g}% | "
                f"الوقف -{STOP_PCT:g}% | العمولة {FEE_PCT:g}% لكل جانب")

    X._header(ws, 5, HEAD)
    rows = [stat_values(n, [t for t in all_trades if t["frame"] == n]) for n in frame_names]
    rows.append(stat_values("الإجمالي", all_trades))
    end = write_rows(ws, 6, rows)
    for row_i in range(6, end):
        pass
    X._widths(ws, [34, 12, 13, 13, 14, 12, 14, 12, 12, 16])

    notes = ["",
             "ملاحظات مهمة:",
             "• النتايج على بيانات الماضي، وما بتضمنش المستقبل.",
             "• الأسهم هي القايمة الحالية بس (الأسهم اللي اتشطبت أو اختفت مش ظاهرة).",
             "• المجموع % هو مجموع نسب الصفقات، مش عائد محفظة حقيقي (الصفقات بتتزامن وبتتداخل).",
             "• التنفيذ مفترض على سعر الوقف أو الهدف، والواقع ممكن يبقى أسوأ (سيولة، وفجوات).",
             "• معامل الربح = مجموع الأرباح ÷ مجموع الخسائر. أكبر من 1 يعني رابح.",
             "• متوسط الصفقة بعد العمولة هو أهم رقم: لو سالب، القواعد دي خسرانة على المدى الطويل."]
    r = end
    for line in notes:
        ws.cell(row=r, column=1, value=line)
        r += 1
    for name in frame_names:
        ws.cell(row=r, column=1,
                value=f"فريم {name}: صفقات لسه مفتوحة في آخر البيانات: {open_counts.get(name, 0)}"
                      f" | أسهم بدون بيانات: {len(missing.get(name, []))}")
        r += 1

    # حسب MA200 عند الدخول
    rows = []
    for n in frame_names:
        ft = [t for t in all_trades if t["frame"] == n]
        for cond, label in ((True, "الدخول فوق متوسط 200"), (False, "الدخول تحت متوسط 200"),
                            (None, "متوسط 200 غير متاح")):
            sub = [t for t in ft if t["above200"] is cond]
            if sub:
                rows.append(stat_values(f"{n} | {label}", sub))
    table_sheet(wb, "حسب متوسط 200", rows)

    # حسب سبب الخروج وعدد أجزاء الشراء
    rows = []
    for n in frame_names:
        ft = [t for t in all_trades if t["frame"] == n]
        for reason in sorted({t["reason"] for t in ft}):
            sub = [t for t in ft if t["reason"] == reason]
            rows.append(stat_values(f"{n} | {X.REASONS.get(reason, reason)}", sub))
        for b in (1, 2):
            sub = [t for t in ft if t["buys"] == b]
            if sub:
                rows.append(stat_values(f"{n} | شراء {b} جزء", sub))
    table_sheet(wb, "حسب الخروج والشراء", rows)

    # حسب السنة
    rows = []
    for n in frame_names:
        ft = [t for t in all_trades if t["frame"] == n]
        for y in sorted({t["exit_date"][:4] for t in ft}):
            sub = [t for t in ft if t["exit_date"][:4] == y]
            rows.append(stat_values(f"{n} | {y}", sub))
    table_sheet(wb, "حسب السنة", rows)

    # حسب السهم
    rows = []
    for sym in sorted({t["symbol"] for t in all_trades}):
        rows.append(stat_values(sym, [t for t in all_trades if t["symbol"] == sym]))
    rows.sort(key=lambda r: r[7] if isinstance(r[7], (int, float)) else -1e9, reverse=True)
    table_sheet(wb, "حسب السهم", rows)

    # كل الصفقات
    ws = wb.create_sheet("كل الصفقات")
    ws.sheet_view.rightToLeft = True
    heads = ["السهم", "الفريم", "تاريخ الدخول", "تاريخ الخروج", "سعر الدخول", "متوسط الشراء",
             "أجزاء الشراء", "النتيجة قبل العمولة", "النتيجة بعد العمولة", "سبب الخروج",
             "المدة (يوم)", "فوق م200 عند الدخول"]
    X._header(ws, 1, heads)
    ordered = sorted(all_trades, key=lambda t: t["exit_date"], reverse=True)
    for i, t in enumerate(ordered, start=2):
        a200 = "نعم" if t["above200"] is True else ("لا" if t["above200"] is False else "—")
        values = [t["symbol"], t["frame"], t["entry_date"], t["exit_date"], t["entry"], t["avg"],
                  t["buys"], t["gross"], t["pnl"], X.REASONS.get(t["reason"], t["reason"]),
                  t["days"], a200]
        for col, v in enumerate(values, start=1):
            c = ws.cell(row=i, column=col, value=v)
            c.border = X.BORDER
            c.alignment = X.CENTER
        ws.cell(row=i, column=5).number_format = "0.00"
        ws.cell(row=i, column=6).number_format = "0.00"
        ws.cell(row=i, column=8).number_format = X.PCT_SIGNED
        ws.cell(row=i, column=11).number_format = "0.0"
        res = ws.cell(row=i, column=9)
        res.number_format = X.PCT_SIGNED
        res.fill = X.GREEN if t["pnl"] > 0 else X.RED
    X._widths(ws, [10, 11, 17, 17, 12, 13, 12, 18, 18, 18, 11, 18])
    ws.freeze_panes = "A2"
    if ordered:
        ws.auto_filter.ref = f"A1:L{len(ordered) + 1}"

    wb.save(path)


def main():
    symbols = X.load_symbols()
    tv = TvDatafeed()
    all_trades, open_counts, missing, frame_names = [], {}, {}, []

    for key in FRAMES_WANTED:
        if key not in FR:
            continue
        name, itv, t1p, t2p = FR[key]
        frame_names.append(name)
        miss, open_n = [], 0
        for k, sym in enumerate(symbols, start=1):
            df = None
            for _ in range(2):
                try:
                    df = tv.get_hist(symbol=sym, exchange="EGX", interval=itv, n_bars=N_BARS)
                except Exception as e:
                    print(sym, "خطأ:", e)
                if df is not None and not df.empty:
                    break
            if df is None or df.empty:
                miss.append(sym)
                continue
            ts, o = backtest(sym, name, df, t1p, t2p)
            all_trades += ts
            open_n += o
            print(f"[{name}] {k}/{len(symbols)} {sym}: شموع={len(df)} صفقات={len(ts)}")
        open_counts[name] = open_n
        missing[name] = miss

    path = "backtest_result.xlsx"
    build_excel(all_trades, frame_names, open_counts, missing, path)

    lines = ["🧪 نتيجة الاختبار التاريخي",
             f"RSI ≤ {BUY1_RSI:g} | شراء تاني -{BUY2_DROP:g}% | وقف -{STOP_PCT:g}% | عمولة {FEE_PCT:g}%/جانب",
             ""]
    for name in frame_names + ["الإجمالي"]:
        ts = all_trades if name == "الإجمالي" else [t for t in all_trades if t["frame"] == name]
        s = stats(ts)
        lines.append(f"━━ {name} ━━")
        if s is None:
            lines += ["مفيش صفقات", ""]
            continue
        pf = "∞" if s["pf"] is None else f"{s['pf']:.2f}"
        lines += [f"الصفقات: {s['n']} | النجاح: {s['win_rate']:.1f}%",
                  f"متوسط الصفقة: {s['exp']:+.2f}% | معامل الربح: {pf}",
                  f"أسوأ صفقة: {s['worst']:+.1f}% | المدة: {s['days']:.1f} يوم", ""]
    lines.append("التفاصيل في ملف Excel المرفق.")
    send("\n".join(lines))
    for chat in CHAT_IDS:
        X.send_document(path, chat, "🧪 الاختبار التاريخي — التفاصيل الكاملة")


main()