#!/usr/bin/env python3
"""
בוט סקרינר לייב 20 — שיטת מיכה סטוק
סורק מניות פעמיים ביום (16:31 ו-22:45 שעון ישראל) ושולח מייל
"""

import requests
import smtplib
import schedule
import time
import datetime
import sys
import os

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── הגדרות — נקראות מ-Environment Variables של Railway ──
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "d743jf9r01qno4q0bmugd743jf9r01qno4q0bmv0")
TO_EMAIL    = os.environ.get("TO_EMAIL",    "ILIA044@GMAIL.COM")
GMAIL_USER  = os.environ.get("GMAIL_USER",  "ILIA044@GMAIL.COM")
GMAIL_PASS  = os.environ.get("GMAIL_PASS",  "")   # ← חובה להגדיר ב-Railway Variables

# ─── רשימת המניות ─────────────────────────────────────────
WATCHLIST = [
    "ASTS","IREN","NNE","MSTR","QQQ","TSLA","NFLX","AAPL","GE","BA",
    "LUNR","AFRM","CRCL","CVNA","NOW","ONDS","RKLB","PL","IGV","KTOS",
    "NASA","CODA","SIDU","GEV","OKLO","IONQ","QBTS","RGTI","QUBT",
    "JOBY","ACHR","RDW","EOSE","CIFR","NBIS","BITF","HUT","WULF",
    "RIOT","MARA","SOUN","BBAI","PONY","OPEN","HOOD","ZIM","BETR",
    "IRDM","VSAT","OSS","SATL","BKSY","SPCE","FLY","SATS","HEI",
    "ARKX","LHX","SPIR","LMT","NOC","RTX","BTQ","GLXY","UFO",
    "SMR","CCJ","PLTR","COIN","AMZN","SHOP","NVDA","AMD","SOFI",
    "RGTI","QUBT","IONQ","QBTS"
]
# הסר כפילויות
WATCHLIST = list(dict.fromkeys(WATCHLIST))

# ─── שליפת נתונים ─────────────────────────────────────────
def get_quote(ticker):
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_KEY}"
        r = requests.get(url, timeout=8)
        d = r.json()
        if not d or d.get("c", 0) == 0:
            return None
        return {
            "price":      d["c"],
            "open":       d["o"],
            "high":       d["h"],
            "low":        d["l"],
            "prev":       d["pc"],
            "change_pct": round((d["c"] - d["pc"]) / d["pc"] * 100, 2) if d["pc"] else 0
        }
    except:
        return None

def get_candles(ticker, days=55):
    try:
        end   = int(time.time())
        start = end - days * 86400 * 2
        url   = (f"https://finnhub.io/api/v1/stock/candle"
                 f"?symbol={ticker}&resolution=D&from={start}&to={end}&token={FINNHUB_KEY}")
        r = requests.get(url, timeout=10)
        d = r.json()
        if d.get("s") != "ok" or not d.get("c") or len(d["c"]) < 21:
            return None
        return {
            "closes":  d["c"],
            "highs":   d["h"],
            "lows":    d["l"],
            "volumes": d["v"]
        }
    except:
        return None

# ─── אינדיקטורים ──────────────────────────────────────────
def calc_ma(values, period):
    if len(values) < period:
        return None
    return round(sum(values[-period:]) / period, 4)

def calc_cci(highs, lows, closes, period=20):
    if len(closes) < period:
        return None
    tp_list   = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    tp_window = tp_list[-period:]
    tp_mean   = sum(tp_window) / period
    mean_dev  = sum(abs(tp - tp_mean) for tp in tp_window) / period
    if mean_dev == 0:
        return 0
    return round((tp_window[-1] - tp_mean) / (0.015 * mean_dev), 1)

def calc_cci_prev(highs, lows, closes, period=20):
    if len(closes) < period + 1:
        return None
    return calc_cci(highs[:-1], lows[:-1], closes[:-1], period)

def volume_trend(volumes):
    if len(volumes) < 4:
        return "unknown"
    v = volumes[-4:]
    avg_prev = sum(v[:3]) / 3
    if v[-1] < avg_prev * 0.75:
        return "falling_strong"
    elif v[-1] < avg_prev * 0.90:
        return "falling"
    elif v[-1] > avg_prev * 1.20:
        return "rising"
    return "flat"

def count_consecutive(closes, color):
    count = 0
    for i in range(len(closes)-1, 0, -1):
        if color == "red"   and closes[i] < closes[i-1]:
            count += 1
        elif color == "green" and closes[i] > closes[i-1]:
            count += 1
        else:
            break
    return count

def detect_candle(o, h, l, c):
    body        = abs(c - o)
    total_range = h - l
    if total_range == 0:
        return "נר ללא מנעד"
    body_pct    = body / total_range
    upper_wick  = h - max(c, o)
    lower_wick  = min(c, o) - l

    if body_pct < 0.2:
        return "דוג׳י / סביבון 🔄"
    if c < o and lower_wick > body * 1.5:
        return "פטיש 🔨"
    if c < o and upper_wick > body * 1.5:
        return "שוטינג סטאר ⭐"
    if c > o:
        return "נר קונים 🟢"
    return "נר מוכרים 🔴"

# ─── ניתוח לייב 20 ────────────────────────────────────────
def analyze_live20(ticker):
    candles = get_candles(ticker)
    quote   = get_quote(ticker)

    if not candles or not quote:
        return {"ticker": ticker, "error": True, "msg": "אין נתונים"}

    closes  = candles["closes"]
    highs   = candles["highs"]
    lows    = candles["lows"]
    volumes = candles["volumes"]

    price    = quote["price"]
    chg_pct  = quote["change_pct"]
    ma20     = calc_ma(closes, 20)
    cci_now  = calc_cci(highs, lows, closes)
    cci_prev = calc_cci_prev(highs, lows, closes)
    vol_tr   = volume_trend(volumes)
    cons_red = count_consecutive(closes, "red")
    cons_grn = count_consecutive(closes, "green")

    if ma20 is None or cci_now is None:
        return {"ticker": ticker, "error": True, "msg": "חישוב נכשל"}

    dist_pct  = round((price - ma20) / ma20 * 100, 2)
    above_ma  = price > ma20
    candle    = detect_candle(quote["open"], quote["high"], quote["low"], price)

    # ─── ציון לונג ───
    long_score = 0
    long_notes = []

    if not above_ma and dist_pct <= -3:
        long_score += 2
        long_notes.append(f"✅ מתחת MA20 ב-{abs(dist_pct):.1f}% — אפקט גומייה")
    elif not above_ma and dist_pct < -1:
        long_score += 1
        long_notes.append(f"✅ מתחת MA20 ב-{abs(dist_pct):.1f}%")

    if cons_red >= 4:
        long_score += 3
        long_notes.append(f"✅ {cons_red} ימים אדומים רצופים — בשל לשינוי כיוון")
    elif cons_red == 3:
        long_score += 2
        long_notes.append(f"✅ {cons_red} ימים אדומים — מתקרב לשינוי")

    if vol_tr == "falling_strong":
        long_score += 2
        long_notes.append("✅ ווליום יורד בחדות — מוכרים מתעייפים")
    elif vol_tr == "falling":
        long_score += 1
        long_notes.append("✅ ווליום יורד — מוכרים נחלשים")

    if cci_prev is not None and cci_prev < -100 and cci_now > -100:
        long_score += 3
        long_notes.append("✅ CCI פרץ מעל -100 — איתות קנייה חזק!")
    elif cci_now is not None and -20 < cci_now < 20:
        long_score += 1
        long_notes.append("✅ CCI ליד 0 — נייטרל, מוכן לפריצה")

    if "פטיש" in candle or "דוג׳י" in candle:
        long_score += 2
        long_notes.append(f"✅ נר שינוי כיוון: {candle}")

    # ─── ציון שורט ───
    short_score = 0
    short_notes = []

    if above_ma and dist_pct >= 5:
        short_score += 2
        short_notes.append(f"⚠️ מעל MA20 ב-{dist_pct:.1f}% — מתוח מאוד")
    if cons_grn >= 4:
        short_score += 2
        short_notes.append(f"⚠️ {cons_grn} ימים ירוקים רצופים — עייפות")
    if cci_now is not None and cci_now > 100:
        short_score += 2
        short_notes.append("⚠️ CCI מעל +100 — אובר-בוט")
    if vol_tr == "rising" and above_ma:
        short_score += 1
        short_notes.append("⚠️ ווליום עולה מעל MA20")

    # ─── המלצה ───
    if long_score >= 7:
        verdict       = "🟢🟢 כניסה לונג חזקה — חכה לנר ירוק אישור"
        verdict_level = "LONG_STRONG"
    elif long_score >= 5:
        verdict       = "🟢 כניסה לונג — תחכה לנר אישור ירוק"
        verdict_level = "LONG"
    elif long_score == 4:
        verdict       = "🟡 מעניין ללונג — עקוב מחר"
        verdict_level = "WATCH_LONG"
    elif short_score >= 4:
        verdict       = "🔴 פוטנציאל שורט (⚠️ בשוק עולה — זהירות!)"
        verdict_level = "SHORT"
    else:
        verdict       = "⚪ לא רלוונטי כרגע"
        verdict_level = "SKIP"

    return {
        "ticker":        ticker,
        "error":         False,
        "price":         price,
        "change_pct":    chg_pct,
        "ma20":          ma20,
        "dist_pct":      dist_pct,
        "above_ma":      above_ma,
        "cci":           cci_now,
        "candle":        candle,
        "vol_trend":     vol_tr,
        "cons_red":      cons_red,
        "cons_green":    cons_grn,
        "long_score":    long_score,
        "short_score":   short_score,
        "long_notes":    long_notes,
        "short_notes":   short_notes,
        "verdict":       verdict,
        "verdict_level": verdict_level,
    }

# ─── HTML ─────────────────────────────────────────────────
def build_html(results, label):
    now_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    cats = {
        "LONG_STRONG": ("🟢🟢 כניסה לונג חזקה",  "#00e5a0"),
        "LONG":        ("🟢 כניסה לונג",           "#00c880"),
        "WATCH_LONG":  ("🟡 לעקוב — כמעט בשל",    "#ffb800"),
        "SHORT":       ("🔴 פוטנציאל שורט",         "#ff4757"),
    }

    def make_rows(items):
        rows = ""
        for r in items:
            dc = "#00e5a0" if r["dist_pct"] < -2 else ("#ff4757" if r["dist_pct"] > 4 else "#aaa")
            cc = "#00e5a0" if r["change_pct"] >= 0 else "#ff4757"
            notes_html = " &nbsp;|&nbsp; ".join(r.get("long_notes", []) + r.get("short_notes", []))
            rows += f"""
            <tr>
              <td><strong style="color:#4a9eff">{r['ticker']}</strong></td>
              <td>${r['price']:.2f}</td>
              <td style="color:{cc}">{r['change_pct']:+.2f}%</td>
              <td>${r['ma20']:.2f}</td>
              <td style="color:{dc}">{r['dist_pct']:+.1f}%</td>
              <td>{r['cci']:.0f}</td>
              <td>{r['candle']}</td>
              <td>{r['vol_trend']}</td>
              <td style="font-weight:bold">{r['verdict']}</td>
            </tr>
            <tr style="background:#080f1a">
              <td colspan="9" style="padding:3px 10px 10px;font-size:11px;color:#7a8fa3">{notes_html}</td>
            </tr>"""
        return rows or "<tr><td colspan='9' style='text-align:center;color:#555;padding:12px'>אין מניות</td></tr>"

    sections = ""
    for level, (title, color) in cats.items():
        items = [r for r in results if not r.get("error") and r.get("verdict_level") == level]
        if not items:
            continue
        sections += f"""
        <h2 style="color:{color};margin-top:28px;margin-bottom:6px">{title} ({len(items)})</h2>
        <table>
          <tr style="background:#0a2240">
            <th>מניה</th><th>מחיר</th><th>שינוי%</th><th>MA20</th>
            <th>מרחק</th><th>CCI</th><th>נר</th><th>ווליום</th><th>המלצה</th>
          </tr>
          {make_rows(items)}
        </table>"""

    longs  = len([r for r in results if not r.get("error") and "LONG" in r.get("verdict_level","")])
    watches= len([r for r in results if not r.get("error") and r.get("verdict_level")=="WATCH_LONG"])
    shorts = len([r for r in results if not r.get("error") and r.get("verdict_level")=="SHORT"])
    errors = len([r for r in results if r.get("error")])

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="UTF-8">
<style>
  body  {{ background:#07111d;color:#cdd9e5;font-family:'Segoe UI',Arial,sans-serif;padding:20px;margin:0 }}
  h1    {{ color:#4a9eff }}
  table {{ width:100%;border-collapse:collapse;background:#0c1929;margin-bottom:10px }}
  th,td {{ padding:9px 11px;text-align:right;border-bottom:1px solid #1a3050;font-size:13px }}
  .sb   {{ display:inline-block;background:#0c1929;border:1px solid #1e3a5f;border-radius:8px;padding:12px 18px;margin:0 8px 12px 0 }}
</style>
</head>
<body>
<h1>📊 סקרינר לייב 20 — שיטת מיכה סטוק</h1>
<p style="color:#8899aa">סריקה: <strong style="color:#eee">{now_str}</strong> &nbsp;|&nbsp; {label}</p>

<div style="margin:16px 0">
  <div class="sb"><span style="color:#8899aa;font-size:12px">נסרקו</span><br><strong style="font-size:20px;color:#4a9eff">{len(results)}</strong></div>
  <div class="sb"><span style="color:#8899aa;font-size:12px">🟢 לונג</span><br><strong style="font-size:20px;color:#00e5a0">{longs}</strong></div>
  <div class="sb"><span style="color:#8899aa;font-size:12px">🟡 עקוב</span><br><strong style="font-size:20px;color:#ffb800">{watches}</strong></div>
  <div class="sb"><span style="color:#8899aa;font-size:12px">🔴 שורט</span><br><strong style="font-size:20px;color:#ff4757">{shorts}</strong></div>
</div>

{sections if sections else "<p style='color:#555'>אין מניות עם סיגנל כרגע</p>"}

<div style="margin-top:30px;padding:14px;background:#0c1929;border-radius:8px;border:1px solid #1e3a5f;font-size:12px;color:#7a8fa3">
  <strong style="color:#aaa">📋 צ׳קליסט לייב 20:</strong>
  מגמה כללית | מיקום vs MA20 | נר שינוי כיוון (דוג׳י/פטיש/הראמי) | ווליום יורד בירידה | CCI פריצת -100 | 4+ ימים אדומים רצופים
  <br><br>⚠️ <em>אין ייעוץ פיננסי. לצורך לימוד בלבד.</em>
</div>
</body></html>"""

# ─── שליחת מייל ───────────────────────────────────────────
def send_email(html, label):
    if not GMAIL_PASS:
        print("❌ GMAIL_PASS לא מוגדר — מייל לא נשלח")
        return False

    subject = f"📊 לייב 20 | {label} | {datetime.datetime.now().strftime('%d/%m/%Y')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())
        print(f"✅ מייל נשלח → {TO_EMAIL}")
        return True
    except Exception as e:
        print(f"❌ שגיאת מייל: {e}")
        return False

# ─── סריקה ────────────────────────────────────────────────
def run_scan(label):
    print(f"\n{'='*55}")
    print(f"🔍 {label} | {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*55}")

    results = []
    for i, ticker in enumerate(WATCHLIST):
        sys.stdout.write(f"\r   {i+1}/{len(WATCHLIST)}: {ticker}          ")
        sys.stdout.flush()
        results.append(analyze_live20(ticker))
        time.sleep(0.35)   # ~60 req/min limit

    print(f"\n✅ הסתיים")

    longs  = [r for r in results if not r.get("error") and "LONG" in r.get("verdict_level","")]
    watches= [r for r in results if not r.get("error") and r.get("verdict_level")=="WATCH_LONG"]
    shorts = [r for r in results if not r.get("error") and r.get("verdict_level")=="SHORT"]

    print(f"   🟢 לונג:  {[r['ticker'] for r in longs]}")
    print(f"   🟡 עקוב:  {[r['ticker'] for r in watches]}")
    print(f"   🔴 שורט:  {[r['ticker'] for r in shorts]}")

    html = build_html(results, label)
    send_email(html, label)

# ─── main ─────────────────────────────────────────────────
if __name__ == "__main__":
    print("🚀 בוט לייב 20 — מיכה סטוק")
    print(f"   מניות: {len(WATCHLIST)} | מייל: {TO_EMAIL}")

    if "--now" in sys.argv:
        run_scan("סריקה ידנית")
        sys.exit(0)

    # UTC times (Israel = UTC+3)
    # 16:31 Israel = 13:31 UTC
    # 22:45 Israel = 19:45 UTC
    schedule.every().day.at("13:31").do(run_scan, "16:31 — פתיחת מסחר")
    schedule.every().day.at("19:45").do(run_scan, "22:45 — לפני סגירה")

    print("⏰ בוט פעיל — מחכה ל-16:31 ו-22:45 (שעון ישראל)")
    while True:
        schedule.run_pending()
        time.sleep(30)
