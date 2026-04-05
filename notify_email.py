"""
notify_email.py — שליחת עדכוני סריקה במייל דרך Gmail
"""

import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from market_data import (get_vix, get_fear_greed, get_dxy, get_us10y,
                         get_sector_performance, get_upcoming_events, get_52w_movers)

# ── הגדרות ────────────────────────────────────────────────────────────────────
GMAIL_ADDRESS  = "ilia044@gmail.com"
GMAIL_APP_PASS = "utxn eqph nwsb msei"
TO_ADDRESS     = "ilia044@gmail.com"
# ─────────────────────────────────────────────────────────────────────────────


def send_email(subject: str, body_html: str, body_text: str) -> bool:
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = GMAIL_ADDRESS
        msg['To']      = TO_ADDRESS
        msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASS)
            server.sendmail(GMAIL_ADDRESS, TO_ADDRESS, msg.as_string())
        print(f"✅ מייל נשלח ({datetime.datetime.now().strftime('%H:%M:%S')})")
        return True
    except Exception as e:
        print(f"❌ שגיאה: {e}")
        return False


# ── עזרי HTML ─────────────────────────────────────────────────────────────────

def _card(title, content, border='#30363d', bg='#161b22'):
    return f"""
    <div style="background:{bg};border:1px solid {border};border-radius:12px;
                padding:16px 20px;margin-bottom:16px;">
      <div style="font-size:15px;font-weight:700;color:#58a6ff;margin-bottom:12px;">{title}</div>
      {content}
    </div>"""

def _metric(label, value, color, sub='', explain=''):
    return f"""
    <div style="background:#0d1117;border-radius:8px;padding:12px 14px;flex:1;min-width:120px;">
      <div style="font-size:11px;color:#8b949e;margin-bottom:4px;">{label}</div>
      <div style="font-size:20px;font-weight:800;color:{color};">{value}</div>
      {f'<div style="font-size:11px;color:{color};margin-top:2px;">{sub}</div>' if sub else ''}
      {f'<div style="font-size:11px;color:#6e7681;margin-top:4px;line-height:1.4;">{explain}</div>' if explain else ''}
    </div>"""


# ── סקשן: מצב השוק (VIX, F&G, DXY, 10Y) ─────────────────────────────────────

def _market_overview_html():
    vix = get_vix()
    fg  = get_fear_greed()
    dxy = get_dxy()
    t10 = get_us10y()

    metrics = []

    if vix:
        sign = '+' if vix['change'] >= 0 else ''
        metrics.append(_metric(
            "😱 VIX — מדד הפחד",
            f"{vix['value']:.1f}",
            vix['color'],
            f"{sign}{vix['change']:.1f} | {vix['level']}",
            "מתחת 15 = שוק רגוע · 15–25 = חשש · מעל 25 = פחד · מעל 40 = פאניקה"
        ))

    if fg:
        sign = '+' if fg['change'] >= 0 else ''
        metrics.append(_metric(
            "🎭 Fear & Greed (CNN)",
            f"{fg['score']:.0f}/100",
            fg['color'],
            f"{sign}{fg['change']:.1f} | {fg['label']}",
            "0–25 פחד קיצוני (הזדמנות?) · 75–100 חמדנות קיצונית (זהירות)"
        ))

    if dxy:
        sign = '+' if dxy['change'] >= 0 else ''
        metrics.append(_metric(
            "💵 DXY — מדד הדולר",
            f"{dxy['value']:.2f}",
            dxy['color'],
            f"{sign}{dxy['change_pct']:.2f}% | {dxy['level']}",
            "דולר חזק = לחץ על מניות · דולר חלש = תמיכה בשוק"
        ))

    if t10:
        sign = '+' if t10['change'] >= 0 else ''
        metrics.append(_metric(
            "📈 ריבית 10Y (US10Y)",
            f"{t10['value']:.3f}%",
            t10['color'],
            f"{sign}{t10['change']:.3f}% | {t10['level']}",
            "מעל 4.5% = לחץ כבד על טק · ריבית יורדת = תמיכה בצמיחה"
        ))

    if not metrics:
        return ''

    grid = f'<div style="display:flex;gap:10px;flex-wrap:wrap;">{"".join(metrics)}</div>'
    return _card("🌍 מצב השוק הכללי", grid)


# ── סקשן: סקטורים ────────────────────────────────────────────────────────────

def _sectors_html():
    secs = get_sector_performance()
    if not secs:
        return ''
    rows = ''
    for s in secs:
        col  = '#3fb950' if s['change'] > 0 else '#f85149'
        sign = '+' if s['change'] >= 0 else ''
        bar_w = min(100, abs(s['change']) * 15)
        bar_col = col
        rows += f"""
        <tr style="border-bottom:1px solid #1a1f28;">
          <td style="padding:7px 10px;color:#c9d1d9;font-size:13px;">{s['name']}</td>
          <td style="padding:7px 10px;font-weight:700;color:{col};text-align:left;font-size:13px;">{sign}{s['change']:.2f}%</td>
          <td style="padding:7px 10px;width:120px;">
            <div style="background:#21262d;border-radius:3px;height:6px;">
              <div style="background:{bar_col};width:{bar_w}%;height:6px;border-radius:3px;{'margin-right:auto;' if s['change']<0 else ''}"></div>
            </div>
          </td>
        </tr>"""

    explain = '<div style="font-size:11px;color:#6e7681;margin-top:10px;">סקטור עולה = כסף נכנס לשם · סקטור יורד = יציאת כסף · עוזר לזהות אן הכסף החכם זורם היום</div>'
    content = f'<table style="width:100%;border-collapse:collapse;">{rows}</table>{explain}'
    return _card("📊 ביצועי סקטורים היום", content)


# ── סקשן: לוח אירועים ────────────────────────────────────────────────────────

def _calendar_html():
    events = get_upcoming_events(days_ahead=10)
    if not events:
        return _card("📅 לוח אירועים", '<div style="color:#6e7681;font-size:13px;">אין אירועים מרכזיים ב-10 הימים הקרובים</div>')

    type_colors = {
        'holiday':  ('#1a2535', '#58a6ff'),
        'fomc':     ('#2d1a00', '#e3b341'),
        'economic': ('#1a1f2e', '#9ec6f0'),
        'earnings': ('#0f2d1a', '#3fb950'),
    }
    type_explain = {
        'holiday':  'הבורסה סגורה — אין מסחר',
        'fomc':     'החלטת ריבית — תנודתיות גבוהה צפויה · קבל עמדה לפני',
        'economic': 'נתון כלכלי חשוב — עשוי להזיז את השוק',
        'earnings': 'עונת דוחות — ווליום גבוה, הזדמנויות רבות',
    }
    rows = ''
    for ev in events:
        bg, col = type_colors.get(ev['type'], ('#161b22', '#8b949e'))
        days_txt = 'היום!' if ev['days_until'] == 0 else f"בעוד {ev['days_until']} ימים"
        expl = type_explain.get(ev['type'], '')
        rows += f"""
        <div style="background:{bg};border-right:3px solid {col};border-radius:6px;
                    padding:10px 12px;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:13px;color:#e6edf3;">{ev['event']}</span>
            <span style="font-size:12px;color:{col};font-weight:700;white-space:nowrap;margin-right:10px;">
              {ev['date_fmt']} | {days_txt}
            </span>
          </div>
          <div style="font-size:11px;color:#6e7681;margin-top:4px;">{expl}</div>
        </div>"""

    return _card("📅 אירועים קרובים (10 ימים)", rows, border='#e3b341', bg='#111820')


# ── סקשן: 52W High/Low ───────────────────────────────────────────────────────

def _52w_html(results):
    near_high, near_low = get_52w_movers(results)
    if not near_high and not near_low:
        return ''
    content = ''
    if near_high:
        content += '<div style="font-size:12px;color:#3fb950;font-weight:700;margin-bottom:6px;">📈 קרוב לשיא 52 שבועות — מומנטום חזק</div>'
        for r in near_high:
            content += f'<div style="font-size:13px;padding:4px 0;border-bottom:1px solid #1a1f28;"><strong style="color:#58a6ff;">{r["ticker"]}</strong> ${r["price"]:.1f} | {r["pct_from_high"]:+.1f}% משיא ({r["w52_high"]:.1f})</div>'
    if near_low:
        content += '<div style="font-size:12px;color:#f85149;font-weight:700;margin:10px 0 6px;">📉 קרוב לשפל 52 שבועות — ריקושט פוטנציאלי?</div>'
        for r in near_low:
            content += f'<div style="font-size:13px;padding:4px 0;border-bottom:1px solid #1a1f28;"><strong style="color:#58a6ff;">{r["ticker"]}</strong> ${r["price"]:.1f} | +{r["pct_from_low"]:.1f}% משפל ({r["w52_low"]:.1f})</div>'
    content += '<div style="font-size:11px;color:#6e7681;margin-top:8px;">קרוב לשיא = כסף נכנס, מומנטום · קרוב לשפל = גומיה מתוחה, בדוק אם יש ריקושט</div>'
    return _card("📐 52 שבועות — שיאים ושפלים", content)


# ── טבלת מניות ───────────────────────────────────────────────────────────────

def _stock_rows(lst, limit=20):
    if not lst:
        return '<tr><td colspan="6" style="color:#666;padding:8px;text-align:center;">—</td></tr>'
    html = ''
    for r in lst[:limit]:
        chg_col  = '#3fb950' if r['change_pct'] >= 0 else '#f85149'
        sign     = '+' if r['change_pct'] >= 0 else ''
        dist_col = '#3fb950' if r['dist_ma'] <= -5 else ('#e3b341' if r['dist_ma'] <= 0 else '#6e7681')
        html += f"""<tr style="border-bottom:1px solid #1a1f28;">
          <td style="padding:8px 12px;font-weight:700;color:#58a6ff;">{r['ticker']}</td>
          <td style="padding:8px 12px;">${r['price']:.1f}</td>
          <td style="padding:8px 12px;color:{chg_col};font-weight:700;">{sign}{r['change_pct']:.1f}%</td>
          <td style="padding:8px 12px;color:{dist_col};">{r['dist_ma']:+.1f}% MA20</td>
          <td style="padding:8px 12px;color:#8b949e;">CCI {r['cci_val']:.0f}</td>
          <td style="padding:8px 12px;">{'🐂'*r['bullish']}{'🐻'*r['bearish']}</td>
        </tr>"""
    return html

def _stock_section(title, color, lst, limit=20):
    if not lst:
        return ''
    return f"""
    <div style="margin-bottom:20px;">
      <div style="background:{color};color:#fff;padding:8px 14px;border-radius:8px 8px 0 0;
                  font-weight:700;font-size:15px;">{title} ({len(lst)})</div>
      <table style="width:100%;border-collapse:collapse;background:#161b22;border-radius:0 0 8px 8px;">
        <thead><tr style="color:#8b949e;font-size:12px;border-bottom:1px solid #30363d;">
          <th style="padding:6px 12px;text-align:right;">טיקר</th>
          <th style="padding:6px 12px;text-align:right;">מחיר</th>
          <th style="padding:6px 12px;text-align:right;">שינוי</th>
          <th style="padding:6px 12px;text-align:right;">מרחק MA20</th>
          <th style="padding:6px 12px;text-align:right;">CCI</th>
          <th style="padding:6px 12px;text-align:right;">אינדיקטורים</th>
        </tr></thead>
        <tbody>{_stock_rows(lst, limit)}</tbody>
      </table>
    </div>"""


# ── הרכבת המייל המלא ─────────────────────────────────────────────────────────

def format_email(results, scan_time: str):
    strong_buy = [r for r in results if r['rec'] == 'strong-buy']
    buy        = [r for r in results if r['rec'] == 'buy']
    neutral    = [r for r in results if r['rec'] == 'neutral']
    sell       = [r for r in results if r['rec'] in ('sell', 'strong-sell')]

    opportunities = len(strong_buy) + len(buy)
    subject = f"📊 סריקת מניות {scan_time} — {opportunities} הזדמנות{'ות' if opportunities!=1 else ''}"

    # מניה מעניינת — גומיה מתוחה
    rubber = [r for r in results if r['rec'] in ('strong-buy','buy') and r['dist_ma'] < -5]
    spotlight = ''
    if rubber:
        b = rubber[0]
        spotlight = f"""
        <div style="background:#0f2d1a;border:2px solid #3fb950;border-radius:10px;
                    padding:14px 18px;margin-bottom:16px;">
          ⭐ <strong style="color:#3fb950;">גומיה מתוחה — הזדמנות לייב 20:</strong>
          <strong style="color:#e6edf3;font-size:16px;margin-right:8px;"> {b['ticker']}</strong>
          &nbsp;·&nbsp; ${b['price']:.1f}
          &nbsp;·&nbsp; <span style="color:#f85149;">{b['dist_ma']:+.1f}% מ-MA20</span>
          &nbsp;·&nbsp; CCI {b['cci_val']:.0f}
          &nbsp;·&nbsp; {b['bullish']}/6 בוליש
        </div>"""

    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head><meta charset="UTF-8">
<style>
  body {{ font-family: Arial, sans-serif; background:#0d1117; color:#e6edf3; margin:0; padding:0; direction:rtl; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:20px 14px; }}
</style>
</head>
<body><div class="wrap">

  <!-- כותרת -->
  <div style="background:linear-gradient(135deg,#1a1f2e,#0d1117);border:1px solid #30363d;
              border-radius:12px;padding:18px 22px;margin-bottom:16px;text-align:center;">
    <div style="font-size:24px;font-weight:900;color:#58a6ff;">📊 סריקת מניות — {scan_time}</div>
    <div style="color:#8b949e;font-size:13px;margin-top:4px;">
      נסרקו {len(results)} מניות | שיטת מיכה סטוקס (לייב 20) | © רייזמן איליה
    </div>
  </div>

  {_market_overview_html()}
  {_calendar_html()}
  {spotlight}
  {_stock_section('🟢🟢 כניסה חזקה', '#1a7f37', strong_buy)}
  {_stock_section('🟢 כניסה חלקית', '#2ea043', buy)}
  {_stock_section('⏸️ המתן', '#444c56', neutral, 10)}
  {_stock_section('🔴 הימנע', '#a40e26', sell, 8)}
  {_sectors_html()}
  {_52w_html(results)}

  <div style="text-align:center;color:#484f58;font-size:11px;margin-top:24px;
              border-top:1px solid #21262d;padding-top:12px;">
    © רייזמן איליה · שיטת מיכה סטוקס · כל הזכויות שמורות
  </div>
</div></body></html>"""

    # טקסט גיבוי (plain text)
    lines = [f"📊 סריקת מניות — {scan_time}", "="*36]
    for label, lst in [("🟢🟢 כניסה חזקה", strong_buy), ("🟢 כניסה חלקית", buy),
                        ("⏸️ המתן", neutral[:5]), ("🔴 הימנע", sell)]:
        if lst:
            t = ', '.join([f"{r['ticker']}(${r['price']:.1f}, {r['change_pct']:+.1f}%)" for r in lst[:6]])
            lines.append(f"{label} ({len(lst)}): {t}")
    if rubber:
        b = rubber[0]
        lines.append(f"\n⭐ גומיה: {b['ticker']} {b['dist_ma']:+.1f}% מ-MA20 | CCI {b['cci_val']:.0f}")
    events = get_upcoming_events(3)
    if events:
        lines.append("\n📅 אירועים קרובים:")
        for ev in events:
            lines.append(f"  {ev['date_fmt']} — {ev['event']}")
    lines.append(f"\n© רייזמן איליה")

    return subject, html, '\n'.join(lines)


def send_scan_email(results, scan_time: str) -> bool:
    subject, html, text = format_email(results, scan_time)
    return send_email(subject, html, text)


if __name__ == '__main__':
    send_scan_email([], datetime.datetime.now().strftime('%H:%M'))
