#!/usr/bin/env python3
"""
בוט סקרינר לייב 20 — שיטת מיכה סטוק
שולח הודעת Telegram פעמיים ביום: 16:31 ו-22:45 שעון ישראל
"""

import requests
import schedule
import time
import datetime
import sys
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

FINNHUB_KEY  = os.environ.get("FINNHUB_KEY", "d743jf9r01qno4q0bmugd743jf9r01qno4q0bmv0")
TG_TOKEN     = os.environ.get("TG_TOKEN",    "")
TG_CHAT_ID   = os.environ.get("TG_CHAT_ID",  "391244768")
PORT         = int(os.environ.get("PORT", 8080))

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

last_scan = {"time": "טרם בוצעה", "longs": [], "watches": [], "shorts": []}

# ── Web Server ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        r = last_scan
        body = f"""<html dir="rtl"><body style="font-family:Arial;padding:20px;background:#07111d;color:#cdd9e5">
        <h2>📊 בוט לייב 20 — מיכה סטוק</h2>
        <p>סריקה אחרונה: <b>{r['time']}</b></p>
        <p>🟢 לונג: {', '.join(r['longs']) or 'אין'}</p>
        <p>🟡 עקוב: {', '.join(r['watches']) or 'אין'}</p>
        <p>🔴 שורט: {', '.join(r['shorts']) or 'אין'}</p>
        <p style="color:#555;font-size:12px">סריקות: 16:31 ו-22:45 שעון ישראל</p>
        </body></html>""".encode("utf-8")
        self.wfile.write(body)
    def log_message(self, *args): pass

def start_web():
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

# ── Finnhub ────────────────────────────────────────────────
def get_quote(t):
    try:
        r = requests.get(f"https://finnhub.io/api/v1/quote?symbol={t}&token={FINNHUB_KEY}", timeout=8).json()
        if not r or r.get("c",0)==0: return None
        return {"price":r["c"],"open":r["o"],"high":r["h"],"low":r["l"],"prev":r["pc"],
                "change_pct":round((r["c"]-r["pc"])/r["pc"]*100,2) if r["pc"] else 0}
    except: return None

def get_candles(t):
    try:
        end=int(time.time()); start=end-110*86400
        r=requests.get(f"https://finnhub.io/api/v1/stock/candle?symbol={t}&resolution=D&from={start}&to={end}&token={FINNHUB_KEY}",timeout=10).json()
        if r.get("s")!="ok" or len(r.get("c",[]))<21: return None
        return {"closes":r["c"],"highs":r["h"],"lows":r["l"],"volumes":r["v"]}
    except: return None

def ma(v,p): return round(sum(v[-p:])/p,4) if len(v)>=p else None

def cci(H,L,C,p=20):
    if len(C)<p: return None
    tp=[(H[i]+L[i]+C[i])/3 for i in range(len(C))]
    w=tp[-p:]; m=sum(w)/p; d=sum(abs(x-m) for x in w)/p
    return round((w[-1]-m)/(0.015*d),1) if d else 0

def cci_prev(H,L,C,p=20): return cci(H[:-1],L[:-1],C[:-1],p) if len(C)>p else None

def vol_tr(v):
    if len(v)<4: return "?"
    avg=sum(v[-4:-1])/3
    if v[-1]<avg*0.75: return "⬇️⬇️"
    if v[-1]<avg*0.90: return "⬇️"
    if v[-1]>avg*1.20: return "⬆️"
    return "➡️"

def cons(C,color):
    c=0
    for i in range(len(C)-1,0,-1):
        if (color=="red" and C[i]<C[i-1]) or (color=="green" and C[i]>C[i-1]): c+=1
        else: break
    return c

def candle(o,h,l,c):
    b=abs(c-o); r=h-l
    if r==0: return "➡️"
    if b/r<0.2: return "🔄 דוג׳י"
    if c<o and (min(c,o)-l)>b*1.5: return "🔨 פטיש"
    if c>o: return "🟢"
    return "🔴"

def analyze(ticker):
    cn=get_candles(ticker); q=get_quote(ticker)
    if not cn or not q: return {"ticker":ticker,"error":True}
    C=cn["closes"]; H=cn["highs"]; L=cn["lows"]; V=cn["volumes"]
    price=q["price"]
    m20=ma(C,20); cn_now=cci(H,L,C); cn_prev=cci_prev(H,L,C)
    vt=vol_tr(V); cr=cons(C,"red"); cg=cons(C,"green")
    if not m20 or cn_now is None: return {"ticker":ticker,"error":True}
    dist=round((price-m20)/m20*100,2); above=price>m20
    cdl=candle(q["open"],q["high"],q["low"],price)

    ls,ln=0,[]
    if not above and dist<=-3: ls+=2; ln.append(f"מתחת MA20 ב-{abs(dist):.1f}%")
    elif not above and dist<-1: ls+=1; ln.append(f"מתחת MA20 ב-{abs(dist):.1f}%")
    if cr>=4: ls+=3; ln.append(f"{cr} ימים אדומים")
    elif cr==3: ls+=2; ln.append(f"{cr} ימים אדומים")
    if vt=="⬇️⬇️": ls+=2; ln.append("ווליום יורד חזק")
    elif vt=="⬇️": ls+=1; ln.append("ווליום יורד")
    if cn_prev is not None and cn_prev<-100 and cn_now>-100: ls+=3; ln.append("CCI פרץ מעל -100!")
    if "פטיש" in cdl or "דוג׳י" in cdl: ls+=2; ln.append(f"נר: {cdl}")

    ss,sn=0,[]
    if above and dist>=5: ss+=2; sn.append(f"מעל MA20 ב-{dist:.1f}%")
    if cg>=4: ss+=2; sn.append(f"{cg} ימים ירוקים")
    if cn_now and cn_now>100: ss+=2; sn.append("CCI אובר-בוט")

    if ls>=7:   vl="LONG_STRONG"
    elif ls>=5: vl="LONG"
    elif ls==4: vl="WATCH"
    elif ss>=4: vl="SHORT"
    else:       vl="SKIP"

    return {"ticker":ticker,"error":False,"price":price,"chg":q["change_pct"],
            "ma20":m20,"dist":dist,"cci":cn_now,"candle":cdl,"vol":vt,
            "ls":ls,"ss":ss,"notes":ln+sn,"vl":vl}

# ── Telegram ───────────────────────────────────────────────
def send_telegram(text):
    if not TG_TOKEN:
        print("❌ TG_TOKEN חסר"); return False
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        if r.status_code == 200:
            print("✅ Telegram נשלח!")
            return True
        else:
            print(f"❌ Telegram error: {r.text}")
            return False
    except Exception as e:
        print(f"❌ Telegram exception: {e}")
        return False

def build_tg_message(results, label):
    now = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    longs  = [r for r in results if not r.get("error") and r.get("vl") in ("LONG","LONG_STRONG")]
    watches= [r for r in results if not r.get("error") and r.get("vl")=="WATCH"]
    shorts = [r for r in results if not r.get("error") and r.get("vl")=="SHORT"]

    msg = f"📊 <b>סקרינר לייב 20 — מיכה סטוק</b>\n"
    msg += f"🕐 {now} | {label}\n"
    msg += f"━━━━━━━━━━━━━━━━━━\n"

    if longs:
        msg += f"\n🟢 <b>כניסה לונג ({len(longs)} מניות):</b>\n"
        for r in longs:
            notes = " | ".join(r.get("notes",[])[:2])
            msg += f"  • <b>{r['ticker']}</b> ${r['price']:.2f} ({r['chg']:+.1f}%) — {notes}\n"
    else:
        msg += "\n🟢 לונג: אין כרגע\n"

    if watches:
        msg += f"\n🟡 <b>לעקוב ({len(watches)}):</b>\n"
        for r in watches:
            msg += f"  • <b>{r['ticker']}</b> ${r['price']:.2f} ({r['chg']:+.1f}%)\n"

    if shorts:
        msg += f"\n🔴 <b>שורט — זהירות ({len(shorts)}):</b>\n"
        for r in shorts:
            msg += f"  • <b>{r['ticker']}</b> ${r['price']:.2f} ({r['chg']:+.1f}%)\n"

    msg += f"\n━━━━━━━━━━━━━━━━━━\n"
    msg += f"⚠️ לא ייעוץ פיננסי"
    return msg

# ── סריקה ─────────────────────────────────────────────────
def run_scan(label):
    global last_scan
    print(f"\n🔍 {label} | {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}")
    results = []
    for i,t in enumerate(WATCHLIST):
        print(f"\r   {i+1}/{len(WATCHLIST)}: {t}     ", end="", flush=True)
        results.append(analyze(t))
        time.sleep(0.35)
    print("\n✅ הסתיים")

    longs  = [r["ticker"] for r in results if not r.get("error") and r.get("vl") in ("LONG","LONG_STRONG")]
    watches= [r["ticker"] for r in results if not r.get("error") and r.get("vl")=="WATCH"]
    shorts = [r["ticker"] for r in results if not r.get("error") and r.get("vl")=="SHORT"]

    last_scan = {"time": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                 "longs": longs, "watches": watches, "shorts": shorts}

    print(f"🟢 {longs}\n🟡 {watches}\n🔴 {shorts}")

    msg = build_tg_message(results, label)
    send_telegram(msg)

# ── Main ───────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🚀 בוט לייב 20 | מניות: {len(WATCHLIST)} | Chat: {TG_CHAT_ID}")

    threading.Thread(target=start_web, daemon=True).start()
    print(f"🌐 Web server על פורט {PORT}")

    if "--now" in sys.argv:
        run_scan("סריקה ידנית")
        sys.exit(0)

    # UTC: 16:31 IL = 13:31 UTC | 22:45 IL = 19:45 UTC
    schedule.every().day.at("13:31").do(run_scan, "16:31 פתיחת מסחר")
    schedule.every().day.at("19:45").do(run_scan, "22:45 לפני סגירה")

    print("⏰ מחכה ל-16:31 ו-22:45 (שעון ישראל)")
    while True:
        schedule.run_pending()
        time.sleep(30)
