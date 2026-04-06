"""
market_data.py — מאסף נתוני שוק רחבים לדו"ח היומי
"""

import urllib.request
import json
import datetime
import yfinance as yf
def _ticker(symbol):
    return yf.Ticker(symbol)


# ── מצב שוק + שעות מסחר ──────────────────────────────────────────────────────

# חגים שבהם הבורסה סגורה (YYYY-MM-DD)
MARKET_HOLIDAYS = {
    "2025-01-20","2025-02-17","2025-04-18","2025-05-26","2025-06-19",
    "2025-07-04","2025-09-01","2025-11-27","2025-12-25",
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03",
    "2026-05-25","2026-06-19","2026-07-03",
}

def get_market_status():
    """
    מצב שוק המניות האמריקאי עכשיו (שעון מזרח ארה"ב).
    פרה-מרקט:  04:00–09:30
    שוק פתוח:  09:30–16:00
    אפטר-מרקט: 16:00–20:00
    סגור:       20:00–04:00
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        try:
            from backports.zoneinfo import ZoneInfo
        except ImportError:
            ZoneInfo = None

    try:
        if ZoneInfo:
            now_et = datetime.datetime.now(ZoneInfo('America/New_York'))
        else:
            # UTC-4 (EDT summer) / UTC-5 (EST winter) — קירוב
            import time as _time
            offset = -4 if _time.daylight else -5
            now_et = datetime.datetime.utcnow() + datetime.timedelta(hours=offset)

        weekday   = now_et.weekday()   # 0=Mon … 6=Sun
        date_str  = now_et.strftime('%Y-%m-%d')
        time_str  = now_et.strftime('%H:%M')
        hour, minute = now_et.hour, now_et.minute
        t = hour * 60 + minute          # דקות מחצות

        PRE_OPEN  = 4  * 60            # 04:00
        REG_OPEN  = 9  * 60 + 30       # 09:30
        REG_CLOSE = 16 * 60            # 16:00
        AH_CLOSE  = 20 * 60            # 20:00

        is_holiday = date_str in MARKET_HOLIDAYS
        is_weekend = weekday >= 5

        if is_holiday:
            status, label, color = 'closed', 'חג — בורסה סגורה', '#58a6ff'
            next_ev = 'הבורסה תפתח ביום המסחר הבא 09:30 ET'
        elif is_weekend:
            days_to_mon = 7 - weekday  # Sat=5→2, Sun=6→1
            status, label, color = 'closed', 'סוף שבוע — סגור', '#f85149'
            next_ev = f'פתיחה ביום שני 09:30 ET'
        elif t < PRE_OPEN:
            mins = PRE_OPEN - t
            status, label, color = 'closed', 'שוק סגור', '#f85149'
            next_ev = f'פרה-מרקט מתחיל בעוד {mins//60}:{mins%60:02d} (04:00 ET)'
        elif t < REG_OPEN:
            mins = REG_OPEN - t
            status, label, color = 'premarket', 'פרה-מרקט', '#e3b341'
            next_ev = f'פתיחה רגילה בעוד {mins//60}:{mins%60:02d} (09:30 ET)'
        elif t < REG_CLOSE:
            mins = REG_CLOSE - t
            status, label, color = 'open', 'שוק פתוח', '#3fb950'
            next_ev = f'סגירה בעוד {mins//60}:{mins%60:02d} (16:00 ET)'
        elif t < AH_CLOSE:
            mins = AH_CLOSE - t
            status, label, color = 'afterhours', 'אפטר-מרקט', '#f0883e'
            next_ev = f'סיום אחרי שעות בעוד {mins//60}:{mins%60:02d} (20:00 ET)'
        else:
            status, label, color = 'closed', 'שוק סגור', '#f85149'
            mins = (24*60 - t) + PRE_OPEN
            next_ev = f'פרה-מרקט מחר 04:00 ET'

        hours_desc = {
            'closed':     'שעות מסחר: פרה-מרקט 04:00–09:30 · רגיל 09:30–16:00 · אפטר 16:00–20:00 ET',
            'premarket':  '📊 ניתן לסחור — נזילות נמוכה, מרווחים רחבים, מחירים תנודתיים',
            'open':       '📊 מסחר רגיל — נזילות מלאה, 09:30–16:00 ET',
            'afterhours': '📊 ניתן לסחור — נזילות נמוכה, 16:00–20:00 ET',
        }

        return {
            'status':    status,
            'label':     label,
            'color':     color,
            'time_et':   time_str,
            'next_event': next_ev,
            'hours_desc': hours_desc.get(status, ''),
        }
    except Exception as e:
        return None


# ── פרה-מרקט / אפטר-מרקט ─────────────────────────────────────────────────────

EXTENDED_TICKERS = [
    ('SPY',  'S&P 500'),
    ('QQQ',  'נאסד"ק 100'),
    ('IWM',  'ראסל 2000'),
    ('DIA',  'דאו ג\'ונס'),
]

def get_extended_hours():
    """
    מחירי פרה-מרקט ואפטר-מרקט של ה-ETFs הגדולים.
    מגיע מ-yfinance.Ticker.info — מתעדכן בזמן אמת.
    """
    results = []
    for ticker, name in EXTENDED_TICKERS:
        try:
            info = _ticker(ticker).info
            reg  = info.get('regularMarketPrice') or info.get('previousClose')
            pre  = info.get('preMarketPrice')
            post = info.get('postMarketPrice')

            entry = {'ticker': ticker, 'name': name, 'regular': reg}

            if pre and reg:
                chg = round((pre - reg) / reg * 100, 2)
                entry['pre_price']  = round(pre, 2)
                entry['pre_change'] = chg

            if post and reg:
                chg = round((post - reg) / reg * 100, 2)
                entry['post_price']  = round(post, 2)
                entry['post_change'] = chg

            if reg:
                entry['regular'] = round(reg, 2)
                results.append(entry)
        except Exception:
            pass
    return results


# ── VIX ───────────────────────────────────────────────────────────────────────
def get_vix():
    """
    מדד הפחד של וול סטריט (Volatility Index).
    מתחת 15 = שוק רגוע ואופטימי.
    15–25 = חשש בינוני.
    מעל 25 = פחד, תנודתיות גבוהה.
    מעל 40 = פאניקה.
    """
    try:
        vix = _ticker("^VIX")
        df  = vix.history(period="2d")
        if df.empty:
            return None
        val  = round(df['Close'].iloc[-1], 1)
        prev = round(df['Close'].iloc[-2], 1) if len(df) > 1 else val
        chg  = round(val - prev, 1)
        if val < 15:
            level, color = "רגוע", "#3fb950"
        elif val < 25:
            level, color = "חשש בינוני", "#e3b341"
        elif val < 40:
            level, color = "פחד", "#f0883e"
        else:
            level, color = "פאניקה!", "#f85149"
        return {'value': val, 'change': chg, 'level': level, 'color': color}
    except Exception:
        return None


# ── Fear & Greed Index (CNN) ──────────────────────────────────────────────────
def get_fear_greed():
    """
    מדד פחד וחמדנות של CNN (0–100).
    0–25   = פחד קיצוני → לעיתים הזדמנות קנייה.
    25–45  = פחד.
    45–55  = ניטרלי.
    55–75  = חמדנות.
    75–100 = חמדנות קיצונית → שוק מחומם, זהירות.
    """
    try:
        # נסה CNN קודם
        score, rating, chg = None, None, 0
        for url in [
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
        ]:
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
                    'Referer':    'https://edition.cnn.com/markets/fear-and-greed',
                    'Origin':     'https://edition.cnn.com',
                })
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read())
                if 'fear_and_greed' in data:
                    score  = round(data['fear_and_greed']['score'], 1)
                    rating = data['fear_and_greed']['rating']
                    try:
                        prev = round(data['fear_and_greed_historical']['previous_1_week']['score'], 1)
                        chg  = round(score - prev, 1)
                    except Exception:
                        chg = 0
                    break
            except Exception:
                continue

        # fallback: חשב Fear & Greed מ-VIX + ביצועי שוק
        if score is None:
            vix_data = get_vix()
            spy = _ticker("SPY").history(period="5d")
            spy_5d = round((spy['Close'].iloc[-1] - spy['Close'].iloc[0]) / spy['Close'].iloc[0] * 100, 1) if not spy.empty else 0
            if vix_data:
                v = vix_data['value']
                # VIX גבוה + שוק יורד = פחד; VIX נמוך + שוק עולה = חמדנות
                raw = 100 - min(100, max(0, (v - 10) * 4)) + max(-20, min(20, spy_5d * 2))
                score = round(max(0, min(100, raw)), 1)
            else:
                score = 50
            rating = None
            chg = 0
        if score <= 25:
            color = "#f85149"
        elif score <= 45:
            color = "#f0883e"
        elif score <= 55:
            color = "#8b949e"
        elif score <= 75:
            color = "#e3b341"
        else:
            color = "#3fb950"
        labels = {
            'extreme fear':  'פחד קיצוני',
            'fear':          'פחד',
            'neutral':       'ניטרלי',
            'greed':         'חמדנות',
            'extreme greed': 'חמדנות קיצונית',
            'Extreme Fear':  'פחד קיצוני',
            'Fear':          'פחד',
            'Neutral':       'ניטרלי',
            'Greed':         'חמדנות',
            'Extreme Greed': 'חמדנות קיצונית',
        }
        return {
            'score': score, 'change': chg,
            'label': labels.get(rating, rating),
            'color': color
        }
    except Exception:
        return None


# ── DXY — מדד הדולר ──────────────────────────────────────────────────────────
def get_dxy():
    """
    מדד כוח הדולר מול סל מטבעות.
    דולר חזק (DXY עולה) → לחץ על מניות, סחורות וריביות.
    דולר חלש (DXY יורד) → תמיכה במניות ובסחורות.
    """
    try:
        t  = _ticker("DX-Y.NYB")
        df = t.history(period="2d")
        if df.empty:
            return None
        val  = round(df['Close'].iloc[-1], 2)
        prev = round(df['Close'].iloc[-2], 2) if len(df) > 1 else val
        chg  = round(val - prev, 2)
        chg_pct = round(chg / prev * 100, 2) if prev else 0
        if val > 105:
            level, color = "דולר חזק מאוד", "#f85149"
        elif val > 101:
            level, color = "דולר חזק", "#e3b341"
        elif val > 97:
            level, color = "ניטרלי", "#8b949e"
        else:
            level, color = "דולר חלש", "#3fb950"
        return {'value': val, 'change': chg, 'change_pct': chg_pct, 'level': level, 'color': color}
    except Exception:
        return None


# ── ריבית 10 שנים (US10Y) ─────────────────────────────────────────────────────
def get_us10y():
    """
    תשואת אגרות חוב אמריקאיות ל-10 שנים.
    ריבית גבוהה → לחץ על מניות צמיחה (tech), יקר ללוות.
    ריבית יורדת → תמיכה במניות, הקלה על חברות ממונפות.
    מעל 4.5% → לחץ כבד על שוק המניות.
    """
    try:
        t  = _ticker("^TNX")
        df = t.history(period="2d")
        if df.empty:
            return None
        val  = round(df['Close'].iloc[-1], 3)
        prev = round(df['Close'].iloc[-2], 3) if len(df) > 1 else val
        chg  = round(val - prev, 3)
        if val > 4.5:
            level, color = "לחץ כבד על מניות", "#f85149"
        elif val > 4.0:
            level, color = "לחץ בינוני", "#e3b341"
        elif val > 3.5:
            level, color = "ניטרלי", "#8b949e"
        else:
            level, color = "תמיכה במניות", "#3fb950"
        return {'value': val, 'change': chg, 'level': level, 'color': color}
    except Exception:
        return None


# ── סקטורים ──────────────────────────────────────────────────────────────────
SECTORS = [
    ("XLK",  "טכנולוגיה 💻"),
    ("XLF",  "פיננסים 🏦"),
    ("XLE",  "אנרגיה ⛽"),
    ("XLV",  "בריאות 🏥"),
    ("XLI",  "תעשייה 🏭"),
    ("XLY",  "צרכנות מחזורית 🛍️"),
    ("XLP",  "צרכנות בסיסית 🛒"),
    ("XLC",  "תקשורת 📡"),
    ("XLRE", "נדל\"ן 🏠"),
    ("XLB",  "חומרי גלם 🪨"),
    ("XLU",  "שירותים ⚡"),
]

def get_sector_performance():
    """
    ביצועי הסקטורים של S&P 500 היום.
    עוזר להבין אילו תחומים בשוק חזקים/חלשים.
    סקטור עולה = כסף נכנס לשם → הזדמנות.
    סקטור יורד = יציאת כסף → זהירות.
    """
    import math
    results = []
    for ticker, name in SECTORS:
        try:
            t  = _ticker(ticker)
            df = t.history(period="5d")
            if df.empty or len(df) < 2:
                continue
            curr = float(df['Close'].iloc[-1])
            prev = float(df['Close'].iloc[-2])
            if prev == 0 or math.isnan(curr) or math.isnan(prev):
                continue
            chg = round((curr - prev) / prev * 100, 2)
            if math.isnan(chg) or math.isinf(chg):
                continue
            results.append({'ticker': ticker, 'name': name, 'change': chg})
        except Exception:
            pass
    results.sort(key=lambda x: x['change'], reverse=True)
    return results


# ── לוח אירועים כלכלי ────────────────────────────────────────────────────────
# FOMC 2025/2026 + חגים + אירועים מרכזיים
ECONOMIC_CALENDAR = [
    # ── חגים וסגירות בורסה 2025 ──
    {"date": "2025-01-20", "event": "יום מרטין לותר קינג — הבורסה סגורה 🏛️",       "type": "holiday"},
    {"date": "2025-02-17", "event": "יום הנשיאים — הבורסה סגורה 🏛️",               "type": "holiday"},
    {"date": "2025-04-18", "event": "שישי הטוב — הבורסה סגורה 🏛️",                 "type": "holiday"},
    {"date": "2025-05-26", "event": "Memorial Day — הבורסה סגורה 🏛️",              "type": "holiday"},
    {"date": "2025-06-19", "event": "Juneteenth — הבורסה סגורה 🏛️",               "type": "holiday"},
    {"date": "2025-07-04", "event": "יום העצמאות האמריקאי — הבורסה סגורה 🇺🇸",     "type": "holiday"},
    {"date": "2025-09-01", "event": "Labor Day — הבורסה סגורה 🏛️",                 "type": "holiday"},
    {"date": "2025-11-27", "event": "Thanksgiving — הבורסה סגורה 🦃",              "type": "holiday"},
    {"date": "2025-12-25", "event": "חג המולד — הבורסה סגורה 🎄",                  "type": "holiday"},
    {"date": "2026-01-01", "event": "ראש השנה האזרחי — הבורסה סגורה 🎆",           "type": "holiday"},
    {"date": "2026-01-19", "event": "יום מרטין לותר קינג — הבורסה סגורה 🏛️",       "type": "holiday"},
    {"date": "2026-02-16", "event": "יום הנשיאים — הבורסה סגורה 🏛️",               "type": "holiday"},
    {"date": "2026-04-03", "event": "שישי הטוב — הבורסה סגורה 🏛️",                 "type": "holiday"},
    {"date": "2026-05-25", "event": "Memorial Day — הבורסה סגורה 🏛️",              "type": "holiday"},
    {"date": "2026-06-19", "event": "Juneteenth — הבורסה סגורה 🏛️",               "type": "holiday"},
    {"date": "2026-07-03", "event": "יום העצמאות (צפוי) — הבורסה סגורה 🇺🇸",       "type": "holiday"},
    # ── ישיבות FOMC 2025 ──
    {"date": "2025-01-29", "event": "החלטת ריבית FOMC 🏦 — השפעה ישירה על השוק",   "type": "fomc"},
    {"date": "2025-03-19", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2025-05-07", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2025-06-18", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2025-07-30", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2025-09-17", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2025-10-29", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2025-12-10", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    # ── ישיבות FOMC 2026 ──
    {"date": "2026-01-28", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2026-03-18", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2026-04-29", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2026-06-17", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2026-07-29", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2026-09-16", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2026-10-28", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    {"date": "2026-12-09", "event": "החלטת ריבית FOMC 🏦",                         "type": "fomc"},
    # ── דוחות כלכליים חשובים (משוער — תחילת כל חודש) ──
    {"date": "2025-04-04", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼 — מדד כוח העבודה",  "type": "economic"},
    {"date": "2025-05-02", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2025-06-06", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2025-07-03", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2025-08-01", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2025-09-05", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2025-10-03", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2025-11-07", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2025-12-05", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    {"date": "2026-01-09", "event": "דוח תעסוקה (Non-Farm Payrolls) 💼",             "type": "economic"},
    # ── עונת דוחות רבעוניים ──
    {"date": "2025-04-11", "event": "תחילת עונת דוחות Q1 2025 📋 — JPMorgan פותח",  "type": "earnings"},
    {"date": "2025-07-11", "event": "תחילת עונת דוחות Q2 2025 📋",                   "type": "earnings"},
    {"date": "2025-10-10", "event": "תחילת עונת דוחות Q3 2025 📋",                   "type": "earnings"},
    {"date": "2026-01-09", "event": "תחילת עונת דוחות Q4 2025 📋",                   "type": "earnings"},
]

def get_upcoming_events(days_ahead=7):
    """מחזיר אירועים ב-7 הימים הקרובים"""
    today    = datetime.date.today()
    upcoming = []
    for ev in ECONOMIC_CALENDAR:
        ev_date = datetime.date.fromisoformat(ev['date'])
        delta   = (ev_date - today).days
        if 0 <= delta <= days_ahead:
            upcoming.append({**ev, 'days_until': delta, 'date_fmt': ev_date.strftime('%d/%m')})
    upcoming.sort(key=lambda x: x['days_until'])
    return upcoming


# ── Market Breadth ────────────────────────────────────────────────────────────
BREADTH_SAMPLE = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","BRK-B","JPM","JNJ",
    "V","PG","UNH","HD","MA","ABBV","MRK","LLY","PEP","KO",
    "BAC","WFC","DIS","CSCO","VZ","INTC","IBM","GS","MS","CAT",
    "XOM","CVX","BA","MMM","GE","F","GM","AMD","MU","QCOM"
]

def get_market_breadth():
    """
    אחוז המניות בדגימה של S&P 500 שנסחרות מעל ממוצע 200 ימים.
    מעל 70% = שוק בריא, רוחב עולה.
    50-70%  = שוק בינוני.
    מתחת 50% = חולשה רחבה, זהירות.
    """
    try:
        above = 0
        total = 0
        data = yf.download(BREADTH_SAMPLE, period="1y", auto_adjust=True, progress=False)
        close = data['Close'] if 'Close' in data else data
        for ticker in BREADTH_SAMPLE:
            try:
                if ticker in close.columns:
                    s = close[ticker].dropna()
                    if len(s) >= 200:
                        ma200 = s.rolling(200).mean().iloc[-1]
                        if s.iloc[-1] > ma200:
                            above += 1
                        total += 1
            except Exception:
                pass
        if total == 0:
            return None
        pct = round(above / total * 100, 1)
        if pct >= 70:
            level, color = "שוק בריא", "#3fb950"
        elif pct >= 50:
            level, color = "שוק בינוני", "#e3b341"
        else:
            level, color = "חולשה רחבה", "#f85149"
        return {'pct': pct, 'above': above, 'total': total, 'level': level, 'color': color}
    except Exception:
        return None


# ── 52W High / Low ───────────────────────────────────────────────────────────
def get_52w_movers(results):
    """
    מניות מרשימת הסריקה שקרובות לשיא/שפל של 52 שבועות.
    קרוב לשיא (95%+) = מומנטום חזק.
    קרוב לשפל (105%−) = הזדמנות ריקושט פוטנציאלית.
    """
    near_high, near_low = [], []
    for r in results:
        try:
            t  = _ticker(r['ticker'])
            hi = t.info.get('fiftyTwoWeekHigh')
            lo = t.info.get('fiftyTwoWeekLow')
            p  = r['price']
            if hi and lo and hi > lo:
                pct_from_high = (p - hi) / hi * 100
                pct_from_low  = (p - lo) / lo * 100
                if pct_from_high >= -5:
                    near_high.append({**r, 'pct_from_high': round(pct_from_high,1), 'w52_high': hi})
                elif pct_from_low <= 10:
                    near_low.append({**r, 'pct_from_low': round(pct_from_low,1), 'w52_low': lo})
        except Exception:
            pass
    return near_high[:5], near_low[:5]


# ── Market Driver Detection ───────────────────────────────────────────────────

COMMODITIES = [
    {"ticker": "CL=F",   "name": "נפט גולמי (WTI)",  "emoji": "🛢️",
     "unit": "$/חבית",
     "explain_up":   "עליית נפט מעלה עלויות תחבורה וייצור — לחץ על מניות צרכנות ותעשייה. מיטיב עם חברות אנרגיה (XLE).",
     "explain_down": "ירידת נפט מקטינה עלויות ייצור ותחבורה — תמיכה בצרכנות ותעשייה. לחץ על חברות אנרגיה.",
     "geopolitical": True},
    {"ticker": "GC=F",   "name": "זהב",              "emoji": "🥇",
     "unit": "$/אונקיה",
     "explain_up":   "זהב עולה = שוק מחפש מקלט בטוח. סימן לפחד, אי-ודאות גיאופוליטית, או ציפייה לאינפלציה.",
     "explain_down": "זהב יורד = שוק אופטימי ומוכן לסיכון. כסף יוצא ממקלטים בטוחים ונכנס למניות.",
     "geopolitical": True},
    {"ticker": "SI=F",   "name": "כסף (Silver)",     "emoji": "🪙",
     "unit": "$/אונקיה",
     "explain_up":   "כסף עולה עם זהב — בריחה למקלטים בטוחים ו/או ביקוש תעשייתי גובר.",
     "explain_down": "כסף יורד — ירידת ביקוש תעשייתי או חזרה לנכסי סיכון.",
     "geopolitical": False},
    {"ticker": "NG=F",   "name": "גז טבעי",          "emoji": "⚡",
     "unit": "$/MMBtu",
     "explain_up":   "גז עולה — ביקוש גבוה (חורף) או הפרעות אספקה. מעלה עלויות חשמל ותעשייה.",
     "explain_down": "גז יורד — ביקוש נמוך או אספקה עודפת. מקטין עלויות אנרגיה.",
     "geopolitical": False},
    {"ticker": "BTC-USD","name": "ביטקוין",          "emoji": "₿",
     "unit": "$",
     "explain_up":   "BTC עולה = תיאבון לסיכון גבוה. לרוב חיובי גם למניות צמיחה וטכנולוגיה.",
     "explain_down": "BTC יורד = בריחה מנכסי סיכון. לרוב מלווה בירידות בטק ומניות ספקולטיביות.",
     "geopolitical": False},
    {"ticker": "^GSPC",  "name": "S&P 500",          "emoji": "📈",
     "unit": "נק'",
     "explain_up":   "S&P 500 עולה — שוק אמריקאי חיובי, סנטימנט טוב.",
     "explain_down": "S&P 500 יורד — שוק אמריקאי שלילי, זהירות.",
     "geopolitical": False},
    {"ticker": "QQQ",    "name": "נאסד\"ק 100 (QQQ)", "emoji": "💻",
     "unit": "$",
     "explain_up":   "נאסד\"ק עולה — מניות טכנולוגיה חזקות, תיאבון לסיכון.",
     "explain_down": "נאסד\"ק יורד — לחץ על טכנולוגיה, לרוב בגלל ריבית גבוהה או פחד.",
     "geopolitical": False},
]


def get_market_drivers():
    """
    מזהה מה מניע את השוק עכשיו.
    בודק את כל הסחורות, מדדים ואינדיקטורים ומחזיר את 3 המניעים הכי חזקים.
    """
    drivers = []

    # ── שלב 1: מחיר כל נכס ושינוי יומי ──
    tickers = [c["ticker"] for c in COMMODITIES]
    try:
        import yfinance as yf
        data = yf.download(tickers, period="5d", auto_adjust=True, progress=False)
        close = data['Close'] if 'Close' in data else data

        for c in COMMODITIES:
            try:
                s    = close[c['ticker']].dropna()
                if len(s) < 2: continue
                curr = float(s.iloc[-1])
                prev = float(s.iloc[-2])
                chg  = round((curr - prev) / prev * 100, 2)
                drivers.append({
                    **c,
                    'price':  round(curr, 2),
                    'change': chg,
                    'impact': abs(chg),   # עוצמת ההשפעה
                })
            except Exception:
                pass
    except Exception:
        pass

    # ── שלב 2: VIX כמניע ──
    try:
        vix = get_vix()
        if vix:
            vix_impact = 0
            if vix['value'] > 30:
                vix_impact = 5.0
            elif vix['value'] > 25:
                vix_impact = 3.0
            elif vix['value'] > 20:
                vix_impact = 1.5
            if abs(vix.get('change', 0)) > 2:
                vix_impact += abs(vix['change'])
            if vix_impact > 0:
                drivers.append({
                    'ticker': '^VIX', 'name': 'VIX — מדד הפחד',
                    'emoji': '😱', 'unit': 'נק\'',
                    'price': vix['value'], 'change': float(vix.get('change', 0)),
                    'impact': vix_impact,
                    'geopolitical': False,
                    'explain_up':   f"VIX = {vix['value']:.1f} — {vix['level']}. רמה זו מעידה על אי-ודאות גבוהה בשוק. משקיעים קונים ביטוח (Put Options) ומוכרים מניות.",
                    'explain_down': f"VIX = {vix['value']:.1f} — שוק רגוע יחסית. רמת פחד נמוכה.",
                })
    except Exception:
        pass

    # ── שלב 3: DXY כמניע ──
    try:
        dxy = get_dxy()
        if dxy and abs(dxy.get('change_pct', 0)) > 0.3:
            drivers.append({
                'ticker': 'DXY', 'name': 'מדד הדולר (DXY)',
                'emoji': '💵', 'unit': 'נק\'',
                'price': dxy['value'], 'change': float(dxy.get('change_pct', 0)),
                'impact': abs(float(dxy.get('change_pct', 0))) * 2,
                'geopolitical': False,
                'explain_up':   "דולר חזק = לחץ על חברות רב-לאומיות (הכנסות בחו\"ל שוות פחות). לחץ על סחורות ומדינות מתפתחות.",
                'explain_down': "דולר חלש = תמיכה בחברות יצוא אמריקאיות. סחורות (נפט, זהב) עולות.",
            })
    except Exception:
        pass

    # ── שלב 4: מיון לפי עוצמה ─────────────────────────────────────────────────
    drivers.sort(key=lambda x: x['impact'], reverse=True)

    # ── שלב 5: בניית הסבר לכל מניע ──────────────────────────────────────────
    result = []
    for d in drivers[:4]:
        going_up = d['change'] >= 0
        explanation = d['explain_up'] if going_up else d['explain_down']

        # אלרט עוצמה
        if d['impact'] >= 3:
            alert = 'high'
            alert_text = '🔴 השפעה גבוהה מאוד'
        elif d['impact'] >= 1.5:
            alert = 'medium'
            alert_text = '🟡 השפעה בינונית'
        else:
            alert = 'low'
            alert_text = '🟢 השפעה נמוכה'

        result.append({
            'name':        d['name'],
            'emoji':       d['emoji'],
            'price':       d['price'],
            'change':      d['change'],
            'unit':        d['unit'],
            'explanation': explanation,
            'alert':       alert,
            'alert_text':  alert_text,
            'geopolitical': d.get('geopolitical', False),
        })

    return result


# ── חוזים עתידיים (Futures) ───────────────────────────────────────────────────
FUTURES_LIST = [
    {
        "ticker": "ES=F",
        "name": "S&P 500 (E-mini)",
        "emoji": "📈",
        "short": "S&P",
        "explain": "חוזה על מדד S&P 500 — מייצג את 500 החברות הגדולות בארה\"ב. עלייה = ציפייה חיובית לפתיחה · ירידה = לחץ על השוק הרחב.",
    },
    {
        "ticker": "NQ=F",
        "name": "נאסד\"ק 100 (E-mini)",
        "emoji": "💻",
        "short": "נאסד\"ק",
        "explain": "חוזה על מדד נאסד\"ק 100 — ממוקד בטכנולוגיה: Apple, Nvidia, Microsoft ועוד. רגיש לריבית ולציפיות צמיחה.",
    },
    {
        "ticker": "YM=F",
        "name": "דאו ג'ונס (E-mini)",
        "emoji": "🏭",
        "short": "דאו",
        "explain": "חוזה על דאו ג'ונס — 30 החברות הגדולות הוותיקות. משקף תעשייה, בנקים ובריאות יותר מטכנולוגיה.",
    },
    {
        "ticker": "RTY=F",
        "name": "ראסל 2000 (E-mini)",
        "emoji": "🏢",
        "short": "ראסל",
        "explain": "חוזה על ראסל 2000 — חברות קטנות (Small-Cap). ירידה חדה = סיכון מוגבר בשוק · עלייה = תיאבון לסיכון גבוה.",
    },
]

def get_futures():
    """
    חוזים עתידיים על המדדים הגדולים.
    נסחרים כמעט 24/7 — מראים לאן השוק צפוי לפתוח.
    עלייה לפני הפתיחה = ציפייה חיובית.
    ירידה לפני הפתיחה = לחץ צפוי.
    """
    results = []
    try:
        tickers = [f["ticker"] for f in FUTURES_LIST]
        data = yf.download(tickers, period="5d", auto_adjust=True, progress=False)
        close = data['Close'] if 'Close' in data else data

        for fut in FUTURES_LIST:
            try:
                s = close[fut["ticker"]].dropna()
                if len(s) < 2:
                    continue
                curr = float(s.iloc[-1])
                prev = float(s.iloc[-2])
                chg = round((curr - prev) / prev * 100, 2)
                chg_pts = round(curr - prev, 1)
                if chg >= 0.5:
                    direction, color = "עולה", "#3fb950"
                elif chg <= -0.5:
                    direction, color = "יורד", "#f85149"
                else:
                    direction, color = "יציב", "#8b949e"
                results.append({
                    **fut,
                    "price": round(curr, 1),
                    "change": chg,
                    "change_pts": chg_pts,
                    "direction": direction,
                    "color": color,
                })
            except Exception:
                pass
    except Exception:
        pass
    return results


if __name__ == '__main__':
    print("VIX:", get_vix())
    print("F&G:", get_fear_greed())
    print("DXY:", get_dxy())
    print("10Y:", get_us10y())
    print("Events:", get_upcoming_events())
    secs = get_sector_performance()
    for s in secs:
        print(f"  {s['name']}: {s['change']:+.2f}%")
