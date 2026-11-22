# -*- coding: utf-8 -*-
"""
فحص واختبار استراتيجية الشراء الأسبوعي (ميراج) على قايمة أسهمك.
- 5 أوامر شراء عند: أعلى / إغلاق / وسط / افتتاح / أدنى شمعة الأسبوع اللي فات
- الخروج عند الهدف (+4% من متوسط الشراء) أو مع إغلاق آخر يوم في الأسبوع
- بيطلع ملف Excel مرتب من الأنسب للأضعف

التشغيل في Google Colab:
  !pip install yfinance openpyxl
  ثم الصق الملف كله في خلية وشغّله، وحمّل الملف weekly_scan.xlsx
"""
import warnings
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

SYMBOLS = """
EGAL EGAS ICFC MPCO MTIE SKPC AMOC ARCC ATQA BIOC
COSG CRST IEEC IFAP ISMA MBSC MOED PRCL SPMD ZEOT
AJWA CIRA CLHO EFID ETRS ISPH JUFO MCQE ORWE RACC
SPIN ALUM AMES APSW AXPH CAED CPCI ETEL INFI KABO
KRDI LCSW MILS MOSC NIPH ORAS RMDA RUBX SIPC SVCE
UEFM AALR AIFI CERA EALR GGRN GOUR MCRO OBRI OCPH
SCEM SMFR SWDY TALM MBEG ACGC ADCI AMII BIGP BONY
DGTZ EEII EFIC EPCO FCMD FNAR INEG LUTS MPCI OLFI
ROTO VERT
""".split()

TARGET = 0.04          # هدف 4% من متوسط الشراء
PERIOD = "2y"          # مدة الاختبار
MIN_VALUE = 1_000_000  # أقل متوسط قيمة تداول يومية بالجنيه


def simulate_week(days, prev):
    """days: شموع يومي لأسبوع واحد، prev: شمعة الأسبوع اللي فات (O,H,L,C)"""
    o, h, l, c = prev
    levels = sorted({h, c, (h + l) / 2, o, l}, reverse=True)
    filled = []           # أسعار التنفيذ
    done = set()          # المستويات اللي اتنفذت
    for i, (_, d) in enumerate(days.iterrows()):
        # هل الهدف اتحقق النهارده لمراكز كانت مفتوحة قبل النهارده؟
        if filled:
            avg = sum(filled) / len(filled)
            tgt = avg * (1 + TARGET)
            if d["High"] >= tgt:
                price = max(tgt, d["Open"])
                return len(filled), price / avg - 1, True
        # تنفيذ أوامر الشراء
        for lv in levels:
            if lv not in done and d["Low"] <= lv:
                filled.append(min(lv, d["Open"]))
                done.add(lv)
        if i == len(days) - 1 and filled:
            avg = sum(filled) / len(filled)
            return len(filled), d["Close"] / avg - 1, False
    return 0, 0.0, False




def backtest(sym):
    df = yf.download(f"{sym}.CA", period=PERIOD, interval="1d",
                     progress=False, auto_adjust=False)
    if df is None or df.empty or len(df) < 120:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Volume"] > 0]

    # أسبوع البورصة المصرية: الأحد → الخميس
    wk = df.index.to_period("W-SAT")
    weeks = list(df.groupby(wk))

    results = []
    for k in range(1, len(weeks)):
        p = weeks[k - 1][1]
        prev = (p["Open"].iloc[0], p["High"].max(),
                p["Low"].min(), p["Close"].iloc[-1])
        n, ret, hit = simulate_week(weeks[k][1], prev)
        rng = (prev[1] - prev[2]) / prev[3]
        # العائد على كامل رأس المال (5 أجزاء) = عائد المتوسط × الجزء المستثمر
        results.append({"filled": n, "ret": ret, "hit": hit,
                        "cap_ret": ret * n / 5, "range": rng})

    r = pd.DataFrame(results)
    traded = r[r["filled"] > 0]
    if traded.empty:
        return None

    value = (df["Close"] * df["Volume"]).tail(60).mean()
    wclose = df["Close"].groupby(wk).last()
    ma20 = wclose.rolling(20).mean().iloc[-1]
    last = df["Close"].iloc[-1]

    return {
        "السهم": sym,
        "عدد الأسابيع": len(traded),
        "نسبة تحقيق الهدف %": round(traded["hit"].mean() * 100, 1),
        "متوسط عائد الأسبوع على رأس المال %": round(traded["cap_ret"].mean() * 100, 2),
        "إجمالي العائد %": round(traded["cap_ret"].sum() * 100, 1),
        "أسوأ أسبوع %": round(traded["cap_ret"].min() * 100, 1),
        "متوسط الأجزاء المنفذة": round(traded["filled"].mean(), 1),
        "متوسط مدى الأسبوع %": round(r["range"].mean() * 100, 1),
        "متوسط قيمة التداول اليومية (مليون)": round(value / 1e6, 2),
        "فوق متوسط 20 أسبوع": "نعم" if last > ma20 else "لا",
        "آخر سعر": round(last, 2),
    }


def main():
    rows = []
    for i, s in enumerate(SYMBOLS, 1):
        try:
            res = backtest(s)
            print(f"{i}/{len(SYMBOLS)} {s}: {'تم' if res else 'لا توجد بيانات كافية'}")
            if res:
                rows.append(res)
        except Exception as e:
            print(f"{s}: خطأ {e}")

    out = pd.DataFrame(rows)
    liquid = out["متوسط قيمة التداول اليومية (مليون)"] >= MIN_VALUE / 1e6
    out["مناسب؟"] = "لا (سيولة ضعيفة)"
    out.loc[liquid, "مناسب؟"] = "ممكن"
    good = liquid & (out["متوسط عائد الأسبوع على رأس المال %"] > 0) & \
        (out["نسبة تحقيق الهدف %"] >= 50) & (out["أسوأ أسبوع %"] > -8)
    out.loc[good, "مناسب؟"] = "مناسب"
    out.loc[good & (out["فوق متوسط 20 أسبوع"] == "نعم"), "مناسب؟"] = "الأنسب الآن"

    order = {"الأنسب الآن": 0, "مناسب": 1, "ممكن": 2, "لا (سيولة ضعيفة)": 3}
    out["_o"] = out["مناسب؟"].map(order)
    out = out.sort_values(["_o", "متوسط عائد الأسبوع على رأس المال %"],
                          ascending=[True, False]).drop(columns="_o")

    with pd.ExcelWriter("weekly_scan.xlsx", engine="openpyxl") as w:
        out.to_excel(w, index=False, sheet_name="النتائج")
        ws = w.sheets["النتائج"]
        ws.sheet_view.rightToLeft = True
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 18

    print("\nأفضل 10 أسهم:")
    print(out.head(10)[["السهم", "مناسب؟", "نسبة تحقيق الهدف %",
                        "متوسط عائد الأسبوع على رأس المال %"]].to_string(index=False))
    print("\nتم حفظ الملف: weekly_scan.xlsx")


if __name__ == "__main__":
    main()
