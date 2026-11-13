"""الصفقات الافتراضية لتنبيهات الاستراتيجيات (علم/راية، قاع صاعد، ابتلاعية، دعم/مقاومة، أفضل الأسهم).
كل تنبيه بيتسجل هنا بدخول ووقف وهدف، وvtrack.py بيتابعها ويقفلها في trades.json
عشان تدخل في الإحصائيات والإكسيل والداشبورد.
أي خطأ هنا مبيوقفش التنبيه نفسه."""
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

FILE = "vtrades.json"
CAIRO = ZoneInfo("Africa/Cairo")

# أسماء الفريمات في الإحصائيات
LABELS = ["علم/راية 15 دقيقة", "علم/راية ساعة", "قاع صاعد", "ابتلاعية", "دعم/مقاومة", "أفضل الأسهم"]
SHORT = {"علم/راية 15 دقيقة": "علم 15د", "علم/راية ساعة": "علم ساعة", "قاع صاعد": "قاع صاعد",
         "ابتلاعية": "ابتلاعية", "دعم/مقاومة": "دعم/مقاومة", "أفضل الأسهم": "أفضل الأسهم"}
# أقصى عدد شموع للمتابعة قبل ما الصفقة تتقفل بالسعر الحالي
MAX_BARS = {"يومي": 30, "ساعة": 70, "15 دقيقة": 100}


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("open", {})
            d.setdefault("seen", {})
            return d
    except Exception:
        pass
    return {"open": {}, "seen": {}}


def save(d):
    # الاحتفاظ بآخر 90 يوم في "seen" بس
    cutoff = datetime.now(CAIRO).timestamp() - 90 * 86400
    d["seen"] = {k: v for k, v in d["seen"].items() if v >= cutoff}
    with open(FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)


def register(label, sym, tf, entry, stop, target, key, bar):
    """بيسجل صفقة افتراضية جديدة. tf = فريم المتابعة (يومي / ساعة / 15 دقيقة).
    bar = آخر شمعة وقت التنبيه (بنتابع الشموع اللي بعدها بس). بيرجع True لو اتسجلت."""
    try:
        entry, stop, target = float(entry), float(stop), float(target)
        if not (0 < stop < entry < target):
            return False
        d = load()
        k = f"{label}|{sym}|{key}"
        if k in d["seen"]:
            return False
        now = datetime.now(CAIRO)
        d["seen"][k] = now.timestamp()
        d["open"][k] = {"label": label, "sym": sym, "tf": tf, "entry": round(entry, 3),
                        "stop": round(stop, 3), "target": round(target, 3), "bar": str(bar),
                        "t": now.strftime("%Y-%m-%d %H:%M")}
        save(d)
        return True
    except Exception as e:
        print("vtrades:", e)
        return False


def open_items():
    return list(load()["open"].values())
