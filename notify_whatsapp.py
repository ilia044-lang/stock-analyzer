"""
notify_whatsapp.py — שליחת הודעות וואטסאפ דרך CallMeBot (חינמי)

הגדרה חד-פעמית:
1. שמור את המספר +34 644 71 47 14 באנשי קשר
2. שלח הודעה לאותו מספר: I allow callmebot to send me messages
3. תקבל API KEY בחזרה — הכנס אותו למטה
"""

import urllib.request
import urllib.parse
import datetime

# ── הגדרות ──────────────────────────────────────────────────────────────────
PHONE_NUMBER = "972XXXXXXXXX"   # מספר טלפון עם קידומת מדינה (ללא +), לדוג': 972501234567
CALLMEBOT_APIKEY = "YOUR_API_KEY"   # המפתח שתקבל מ-CallMeBot


def send_whatsapp(message: str) -> bool:
    """שולח הודעת וואטסאפ דרך CallMeBot"""
    if PHONE_NUMBER == "972XXXXXXXXX" or CALLMEBOT_APIKEY == "YOUR_API_KEY":
        print("⚠️  עדיין לא הוגדרו פרטי CallMeBot — הדפסה למסוף בלבד:")
        print(message)
        return False
    try:
        encoded = urllib.parse.quote(message)
        url = f"https://api.callmebot.com/whatsapp.php?phone={PHONE_NUMBER}&text={encoded}&apikey={CALLMEBOT_APIKEY}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read().decode()
            success = 'Message queued' in body or '200' in body or resp.status == 200
            if success:
                print(f"✅ הודעה נשלחה בהצלחה ({datetime.datetime.now().strftime('%H:%M:%S')})")
            else:
                print(f"⚠️  תגובה מ-CallMeBot: {body[:100]}")
            return success
    except Exception as e:
        print(f"❌ שגיאה בשליחה: {e}")
        return False


def format_scan_message(results, scan_time: str) -> str:
    """מעצב הודעת וואטסאפ מתוצאות הסריקה"""
    strong_buy  = [r for r in results if r['rec'] == 'strong-buy']
    buy         = [r for r in results if r['rec'] == 'buy']
    neutral     = [r for r in results if r['rec'] == 'neutral']
    sell        = [r for r in results if r['rec'] == 'sell']
    strong_sell = [r for r in results if r['rec'] == 'strong-sell']

    lines = [
        f"📊 סריקת מניות — {scan_time}",
        f"━━━━━━━━━━━━━━━━━━",
    ]

    if strong_buy:
        tickers = ', '.join([f"{r['ticker']}({r['change_pct']:+.1f}%)" for r in strong_buy[:8]])
        lines.append(f"🟢🟢 כניסה חזקה ({len(strong_buy)}): {tickers}")

    if buy:
        tickers = ', '.join([f"{r['ticker']}({r['change_pct']:+.1f}%)" for r in buy[:8]])
        lines.append(f"🟢 כניסה חלקית ({len(buy)}): {tickers}")

    if neutral:
        tickers = ', '.join([r['ticker'] for r in neutral[:6]])
        lines.append(f"⏸️ המתן ({len(neutral)}): {tickers}")

    if sell or strong_sell:
        avoid = sell + strong_sell
        tickers = ', '.join([r['ticker'] for r in avoid[:6]])
        lines.append(f"🔴 הימנע ({len(avoid)}): {tickers}")

    lines.append(f"━━━━━━━━━━━━━━━━━━")

    # מניה מעניינת מיוחדת (הכי גבוה בדירוג + כי מהממוצע 20)
    best = [r for r in results if r['rec'] in ('strong-buy','buy') and r['dist_ma'] < -5]
    if best:
        b = best[0]
        lines.append(f"⭐ מניה מעניינת: {b['ticker']} | {b['dist_ma']:+.1f}% מ-MA20 | CCI:{b['cci_val']:.0f}")

    lines.append(f"📈 נסרקו {len(results)} מניות")
    lines.append(f"🌐 http://localhost:5001")

    return '\n'.join(lines)


if __name__ == '__main__':
    # בדיקה
    test_msg = format_scan_message([], datetime.datetime.now().strftime('%H:%M'))
    print(test_msg)
    send_whatsapp(test_msg)
