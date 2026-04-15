#!/usr/bin/env python3
"""
בוט סקרינר לייב 20 — שיטת מיכה סטוק
רץ כ-web server ב-Railway + סורק פעמיים ביום
"""

import requests
import smtplib
import schedule
import time
import datetime
import sys
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "d743jf9r01qno4q0bmugd743jf9r01qno4q0bmv0")
TO_EMAIL    = os.environ.get("TO_EMAIL",    "ILIA044@GMAIL.COM")
GMAIL_USER  = os.environ.get("GMAIL_USER",  "ILIA044@GMAIL.COM")
GMAIL_PASS  = os.environ.get("GMAIL_PASS",  "")
PORT        = int(os.environ.get("PORT", 8080))

WATCHLIST = list(dict.fromkeys([
    "ASTS","IREN","NNE","MSTR","QQQ","TSLA","NFLX","AAPL","GE","BA",
    "LUNR","AFRM","CRCL","CVNA","NOW","ONDS","RKLB","PL","IGV","KTOS",
    "NASA","CODA","SIDU","GEV","OKLO","IONQ","QBTS","RGTI","QUBT",
    "JOBY","ACHR","RDW","EOSE","CIFR","NBIS","BITF","HUT","WULF",
    "RIOT","MARA","SOUN","BBAI","PONY","OPEN","HOOD","ZIM","BETR",
    "IRDM","VSAT","OSS","SATL","BKSY","SPCE","FLY","SATS","HEI",
    "ARKX","LHX","SPIR","LMT","NOC","RTX","BTQ","GLXY","UFO",
    "SMR","CCJ","PLTR","COIN","AMZN","SHOP","NVDA","AMD","SOFI"
]))

last_scan_result = {"time": "טרם בוצעה סריקה", "longs": [], "watches": [], "shorts": []}

# ── Web Server קטן כדי ש-Railway יהיה מרוצה ──────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        r = last_scan_result
        body = f"""<html dir="rtl"><body style="font-family:Arial;padding:20px;background:#07111d;color:#cdd9e5">
        <h2>📊 בוט לייב 20 — מיכה סטוק</h2>
        <p>סריקה אחרונה: <b>{r['time']}</b></p>
        <p>🟢 לונג: {', '.join(r['longs']) or 'אין'}</p>
        <p>🟡 עקוב: {', '.join(r['watches']) or 'אין'}</p>
        <p>🔴 שורט: {', '.join(r['shorts']) or 'אין'}</p>
        <p style="color:#555;font-size:12px">סריקות אוטומטיות: 16:31 ו-22:45 שעון ישראל</p>
        </body></html>""".encode("utf-8")
        self.wfile.write(body)
    def log_message(self, *args):
        pass

def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"🌐 Web server פועל על פורט {PORT}")
    server.serve_forever()

# ── Finnhub ───────────────────────────────────────────────
def get_quote(ticker):
    try:
        r = requests.get(f"https://finnhub.io/api/v1/quote?symbol={ticker}&token={FINNHUB_KEY}", timeout=8)
        d = r.json()
        if not d or d.get("c", 0) == 0:
            return None
        return {"price": d["c"], "open": d["o"], "high": d["h"], "low": d["l"],
                "prev": d["pc"], "change_pct": round((d["c"]-d["pc"])/d["pc"]*100, 2) if d["pc"] else 0}
    except:
        return None

def get_candles(ticker):
    try:
        end = int(time.time())
        start = end - 110 * 86400
        r = requests.get(f"https://finnhub.io/api/v1/stock/candle?symbol={ticker}&resolution=D&from={start}&to={end}&token={FINNHUB_KEY}", timeout=10)
        d = r.json()
        if d.get("s") != "ok" or len(d.get("c", [])) < 21:
            return None
        return {"closes": d["c"], "highs": d["h"], "lows": d["l"], "volumes": d["v"]}
    except:
        return None

def calc_ma(v, p):
    return round(sum(v[-p:])/p, 4) if len(v) >= p else None

def calc_cci(highs, lows, closes, p=20):
    if len(closes) < p:
        return None
    tp = [(highs[i]+lows[i]+closes[i])/3 for i in range(len(closes))]
    w = tp[-p:]
    m = sum(w)/p
    d = sum(abs(x-m) for x in w)/p
    return round((w[-1]-m)/(0.015*d), 1) if d else 0

def calc_cci_prev(h, l, c, p=20):
    return calc_cci(h[:-1], l[:-1], c[:-1], p) if len(c) > p else None

def vol_trend(vols):
    if len(vols) < 4:
        return "unknown"
    avg = sum(vols[-4:-1])/3
    if vols[-1] < avg*0.75: return "falling_strong"
    if vols[-1] < avg*0.90: return "falling"
    if vols[-1] > avg*1.20: return "rising"
    return "flat"

def cons_days(closes, color):
    c = 0
    for i in range(len(closes)-1, 0, -1):
        if (color=="red" and closes[i]<closes[i-1]) or (color=="green" and closes[i]>closes[i-1]):
            c += 1
        else:
            break
    return c

def candle_type(o, h, l, c):
    body = abs(c-o); r = h-l
    if r == 0: return "נר ללא מנעד"
    bp = body/r
    if bp < 0.2: return "דוג׳י / סביבון 🔄"
    if c < o and (min(c,o)-l) > body*1.5: return "פטיש 🔨"
    if c > o: return "נר קונים 🟢"
    return "נר מוכרים 🔴"

def analyze(ticker):
    candles = get_candles(ticker)
    quote   = get_quote(ticker)
    if not candles or not quote:
        return {"ticker": ticker, "error": True}

    closes = candles["closes"]; highs = candles["highs"]
    lows   = candles["lows"];   vols  = candles["volumes"]
    price  = quote["price"]

    ma20     = calc_ma(closes, 20)
    cci_now  = calc_cci(highs, lows, closes)
    cci_prev = calc_cci_prev(highs, lows, closes)
    vt       = vol_trend(vols)
    cr       = cons_days(closes, "red")
    cg       = cons_days(closes, "green")

    if not ma20 or cci_now is None:
        return {"ticker": ticker, "error": True}

    dist     = round((price - ma20)/ma20*100, 2)
    above    = price > ma20
    cndle    = candle_type(quote["open"], quote["high"], quote["low"], price)

    ls, ln = 0, []
    if not above and dist <= -3:  ls += 2; ln.append(f"✅ מתחת MA20 ב-{abs(dist):.1f}%")
    elif not above and dist < -1: ls += 1; ln.append(f"✅ מתחת MA20 ב-{abs(dist):.1f}%")
    if cr >= 4:   ls += 3; ln.append(f"✅ {cr} ימים אדומים — בשל לשינוי")
    elif cr == 3: ls += 2; ln.append(f"✅ {cr} ימים אדומים")
    if vt == "falling_strong": ls += 2; ln.append("✅ ווליום יורד חזק")
    elif vt == "falling":      ls += 1; ln.append("✅ ווליום יורד")
    if cci_prev is not None and cci_prev < -100 and cci_now > -100:
        ls += 3; ln.append("✅ CCI פרץ מעל -100!")
    if "פטיש" in cndle or "דוג׳י" in cndle:
        ls += 2; ln.append(f"✅ {cndle}")

    ss, sn = 0, []
    if above and dist >= 5:  ss += 2; sn.append(f"⚠️ מעל MA20 ב-{dist:.1f}%")
    if cg >= 4:              ss += 2; sn.append(f"⚠️ {cg} ימים ירוקים")
    if cci_now and cci_now > 100: ss += 2; sn.append("⚠️ CCI אובר-בוט")

    if ls >= 7:   verd, vl = "🟢🟢 לונג חזק", "LONG_STRONG"
    elif ls >= 5: verd, vl = "🟢 לונג",        "LONG"
    elif ls == 4: verd, vl = "🟡 עקוב",         "WATCH_LONG"
    elif ss >= 4: verd, vl = "🔴 שורט",          "SHORT"
    else:         verd, vl = "⚪ לא רלוונטי",    "SKIP"

    return {"ticker": ticker, "error": False, "price": price,
            "change_pct": quote["change_pct"], "ma20": ma20, "dist_pct": dist,
            "cci": cci_now, "candle": cndle, "vol_trend": vt,
            "long_score": ls, "short_score": ss,
            "long_notes": ln, "short_notes": sn,
            "verdict": verd, "verdict_level": vl}

def build_html(results, label):
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    cats = [("LONG_STRONG","🟢🟢 לונג חזק","#00e5a0"),("LONG","🟢 לונג","#00c880"),
            ("WATCH_LONG","🟡 עקוב","#ffb800"),("SHORT","🔴 שורט","#ff4757")]

    def rows(items):
        if not items: return "<tr><td colspan='8' style='text-align:center;color:#555;padding:12px'>אין</td></tr>"
        out = ""
        for r in items:
            dc = "#00e5a0" if r["dist_pct"]<-2 else ("#ff4757" if r["dist_pct"]>4 else "#aaa")
            cc = "#00e5a0" if r["change_pct"]>=0 else "#ff4757"
            notes = " | ".join(r.get("long_notes",[])+r.get("short_notes",[]))
            out += f"""<tr>
              <td><b style="color:#4a9eff">{r['ticker']}</b></td>
              <td>${r['price']:.2f}</td>
              <td style="color:{cc}">{r['change_pct']:+.2f}%</td>
              <td>${r['ma20']:.2f}</td>
              <td style="color:{dc}">{r['dist_pct']:+.1f}%</td>
              <td>{r['cci']:.0f}</td>
              <td>{r['candle']}</td>
              <td><b>{r['verdict']}</b></td>
            </tr>
            <tr style="background:#080f1a"><td colspan="8" style="padding:3px 10px 10px;font-size:11px;color:#7a8fa3">{notes}</td></tr>"""
        return out

    secs = ""
    for lv, title, color in cats:
        items = [r for r in results if not r.get("error") and r.get("verdict_level")==lv]
        if items:
            secs += f'<h2 style="color:{color};margin-top:24px">{title} ({len(items)})</h2>'
            secs += f'<table><tr style="background:#0a2240"><th>מניה</th><th>מחיר</th><th>שינוי%</th><th>MA20</th><th>מרחק</th><th>CCI</th><th>נר</th><th>המלצה</th></tr>{rows(items)}</table>'

    lc = len([r for r in results if not r.get("error") and "LONG" in r.get("verdict_level","")])
    wc = len([r for r in results if not r.get("error") and r.get("verdict_level")=="WATCH_LONG"])
    sc = len([r for r in results if not r.get("error") and r.get("verdict_level")=="SHORT"])

    return f"""<!DOCTYPE html><html dir="rtl" lang="he"><head><meta charset="UTF-8">
<style>body{{background:#07111d;color:#cdd9e5;font-family:'Segoe UI',Arial,sans-serif;padding:20px}}
h1{{color:#4a9eff}}table{{width:100%;border-collapse:collapse;background:#0c1929;margin-bottom:10px}}
th,td{{padding:9px 11px;text-align:right;border-bottom:1px solid #1a3050;font-size:13px}}
.sb{{display:inline-block;background:#0c1929;border:1px solid #1e3a5f;border-radius:8px;padding:12px 18px;margin:0 8px 12px 0}}</style>
</head><body>
<h1>📊 סקרינר לייב 20 — מיכה סטוק</h1>
<p style="color:#8899aa">סריקה: <b style="color:#eee">{now}</b> | {label}</p>
<div style="margin:16px 0">
<div class="sb"><span style="color:#8899aa;font-size:12px">נסרקו</span><br><b style="font-size:20px;color:#4a9eff">{len(results)}</b></div>
<div class="sb"><span style="color:#8899aa;font-size:12px">🟢 לונג</span><br><b style="font-size:20px;color:#00e5a0">{lc}</b></div>
<div class="sb"><span style="color:#8899aa;font-size:12px">🟡 עקוב</span><br><b style="font-size:20px;color:#ffb800">{wc}</b></div>
<div class="sb"><span style="color:#8899aa;font-size:12px">🔴 שורט</span><br><b style="font-size:20px;color:#ff4757">{sc}</b></div>
</div>
{secs or "<p style='color:#555'>אין סיגנלים כרגע</p>"}
<div style="margin-top:24px;padding:12px;background:#0c1929;border-radius:8px;font-size:12px;color:#7a8fa3">
⚠️ אין ייעוץ פיננסי. לצורך לימוד בלבד.
</div></body></html>"""

def send_email(html, label):
    if not GMAIL_PASS:
        print("❌ GMAIL_PASS חסר"); return
    subject = f"📊 לייב 20 | {label} | {datetime.datetime.now().strftime('%d/%m/%Y')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject; msg["From"] = GMAIL_USER; msg["To"] = TO_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            s.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())
        print(f"✅ מייל נשלח → {TO_EMAIL}")
    except Exception as e:
        print(f"❌ שגיאת מייל: {e}")

def run_scan(label):
    global last_scan_result
    print(f"\n🔍 {label} | {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")
    results = []
    for i, t in enumerate(WATCHLIST):
        print(f"\r   {i+1}/{len(WATCHLIST)}: {t}     ", end="", flush=True)
        results.append(analyze(t))
        time.sleep(0.35)
    print("\n✅ סריקה הושלמה")
    longs  = [r["ticker"] for r in results if not r.get("error") and "LONG" in r.get("verdict_level","")]
    watches= [r["ticker"] for r in results if not r.get("error") and r.get("verdict_level")=="WATCH_LONG"]
    shorts = [r["ticker"] for r in results if not r.get("error") and r.get("verdict_level")=="SHORT"]
    last_scan_result = {"time": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                        "longs": longs, "watches": watches, "shorts": shorts}
    print(f"🟢 {longs}\n🟡 {watches}\n🔴 {shorts}")
    send_email(build_html(results, label), label)

if __name__ == "__main__":
    print(f"🚀 בוט לייב 20 | מניות: {len(WATCHLIST)} | מייל: {TO_EMAIL}")

    # הפעל web server בthread נפרד
    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()

    if "--now" in sys.argv:
        run_scan("סריקה ידנית"); sys.exit(0)

    # UTC: 16:31 Israel = 13:31 UTC | 22:45 Israel = 19:45 UTC
    schedule.every().day.at("13:31").do(run_scan, "16:31 פתיחת מסחר")
    schedule.every().day.at("19:45").do(run_scan, "22:45 לפני סגירה")

    print("⏰ מחכה לסריקות 16:31 ו-22:45 (שעון ישראל)")
    while True:
        schedule.run_pending()
        time.sleep(30)
