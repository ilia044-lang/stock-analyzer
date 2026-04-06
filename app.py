from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
import time
import requests

def _ticker(symbol):
    return yf.Ticker(symbol)
from market_data import (get_vix, get_fear_greed, get_dxy, get_us10y,
                         get_sector_performance, get_upcoming_events,
                         get_market_drivers, get_futures,
                         get_market_status, get_extended_hours)

app = Flask(__name__)


# ─── Indicators ───────────────────────────────────────────────────────────────

def calc_ma20(close):
    return close.rolling(window=20).mean()


def calc_cci(high, low, close, period=14):
    tp = (high + low + close) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma) / (0.015 * mad)


def calc_atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calc_fibonacci(high_series, low_series):
    """מחשב רמות פיבונאצ'י מהשיא לשפל האחרון"""
    swing_high = high_series.max()
    swing_low  = low_series.min()
    diff = swing_high - swing_low
    return {
        'high': round(swing_high, 2),
        'low':  round(swing_low, 2),
        '236':  round(swing_high - diff * 0.236, 2),
        '382':  round(swing_high - diff * 0.382, 2),
        '500':  round(swing_high - diff * 0.500, 2),
        '618':  round(swing_high - diff * 0.618, 2),
        '786':  round(swing_high - diff * 0.786, 2),
    }


# ─── Analysis Functions ────────────────────────────────────────────────────────

def analyze_trend(close):
    if len(close) < 20:
        return {'monthly': 'neutral', 'weekly': 'neutral', 'monthly_change': 0, 'weekly_change': 0, 'signal': 'neutral', 'description': 'אין מספיק נתונים'}

    monthly_change = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100
    weekly_change  = (close.iloc[-1] - close.iloc[-5])  / close.iloc[-5]  * 100

    # Higher highs / higher lows over last 20 days
    highs = close.iloc[-20:]
    lows  = close.iloc[-20:]
    hh = highs.iloc[-1] > highs.iloc[:10].max()
    hl = lows.iloc[-1]  > lows.iloc[:10].min()
    ll = highs.iloc[-1] < highs.iloc[:10].max()
    lw = lows.iloc[-1]  < lows.iloc[:10].min()

    if (hh and hl) or monthly_change > 5:
        monthly = 'bullish'
    elif (ll and lw) or monthly_change < -5:
        monthly = 'bearish'
    else:
        monthly = 'neutral'

    weekly = 'bullish' if weekly_change > 2 else ('bearish' if weekly_change < -2 else 'neutral')

    if monthly == 'bullish' or weekly == 'bullish':
        signal = 'bullish'
        desc = f"טרנד עולה: חודש {monthly_change:+.1f}%, שבוע {weekly_change:+.1f}%"
    elif monthly == 'bearish' or weekly == 'bearish':
        signal = 'bearish'
        desc = f"טרנד יורד: חודש {monthly_change:+.1f}%, שבוע {weekly_change:+.1f}%"
    else:
        signal = 'neutral'
        desc = f"טרנד צידי: חודש {monthly_change:+.1f}%, שבוע {weekly_change:+.1f}%"

    # כלל 5 הימים — אחרי 5 ימים רצופים באותו כיוון, צפה לשינוי
    consecutive = 0
    consecutive_dir = 'none'
    for i in range(-1, -8, -1):
        try:
            if close.iloc[i] > close.iloc[i-1]:
                if consecutive_dir in ('none', 'up'):
                    consecutive_dir = 'up'
                    consecutive += 1
                else:
                    break
            else:
                if consecutive_dir in ('none', 'down'):
                    consecutive_dir = 'down'
                    consecutive += 1
                else:
                    break
        except IndexError:
            break

    five_day_warning = consecutive >= 5

    return {
        'monthly': monthly, 'weekly': weekly,
        'monthly_change': round(monthly_change, 2),
        'weekly_change': round(weekly_change, 2),
        'signal': signal, 'description': desc,
        'consecutive_days': consecutive,
        'consecutive_dir': consecutive_dir,
        'five_day_warning': five_day_warning,
    }


def analyze_candle(o, h, l, c):
    if len(o) < 2:
        return {'patterns': [], 'signal': 'neutral', 'description': 'אין מספיק נתונים'}

    o1, h1, l1, c1 = o.iloc[-1], h.iloc[-1], l.iloc[-1], c.iloc[-1]  # today
    o2, h2, l2, c2 = o.iloc[-2], h.iloc[-2], l.iloc[-2], c.iloc[-2]  # yesterday

    body1  = abs(c1 - o1)
    range1 = h1 - l1 if h1 != l1 else 0.0001
    lower1 = min(o1, c1) - l1
    upper1 = h1 - max(o1, c1)

    patterns = []
    signal = 'neutral'

    # Doji
    if range1 > 0 and body1 / range1 < 0.1:
        patterns.append("דוג'י - חוסר החלטיות")
        signal = 'neutral'

    # Bullish hammer
    if lower1 > 2 * body1 and upper1 < body1 * 0.5:
        patterns.append("פטיש בוליש - דחיית מחירים נמוכים")
        signal = 'bullish'

    # Bearish shooting star
    if upper1 > 2 * body1 and lower1 < body1 * 0.5 and c1 < o1:
        patterns.append("כוכב נופל בריש - דחיית מחירים גבוהים")
        signal = 'bearish'

    # Bullish engulfing
    if c1 > o1 and c2 < o2 and c1 > o2 and o1 < c2:
        patterns.append("בולען בוליש - קונים השתלטו")
        signal = 'bullish'

    # Bearish engulfing
    if c1 < o1 and c2 > o2 and c1 < o2 and o1 > c2:
        patterns.append("בולען בריש - מוכרים השתלטו")
        signal = 'bearish'

    # Bullish harami
    if c1 > o1 and c2 < o2 and c1 < o2 and o1 > c2:
        patterns.append("האראמי בוליש - עצירת ירידה")
        signal = 'bullish'

    # Bearish harami
    if c1 < o1 and c2 > o2 and c1 > o2 and o1 < c2:
        patterns.append("האראמי בריש - עצירת עלייה")
        signal = 'bearish'

    # Marubozu
    if range1 > 0 and body1 / range1 > 0.92:
        if c1 > o1:
            patterns.append("מרבוזו בוליש - כוח קנייה מלא")
            signal = 'bullish'
        else:
            patterns.append("מרבוזו בריש - כוח מכירה מלא")
            signal = 'bearish'

    if not patterns:
        patterns.append("נר רגיל - אין תבנית מיוחדת")

    desc = ' | '.join(patterns)
    return {'patterns': patterns, 'signal': signal, 'description': desc}


def analyze_volume(volume, close):
    """
    לפי שיטת מיכה סטוקס:
    - ווליום יורד בירידה = בוליש (המוכרים נחלשים — ריקושט בדרך)
    - ווליום עולה בירידה = בריש (מוכרים חזקים)
    - ווליום עולה בעלייה + נר שינוי כיוון = בוליש (קונים נכנסו)
    """
    if len(volume) < 5:
        return {'signal': 'neutral', 'description': 'אין מספיק נתונים', 'ratio': 1}

    avg = volume.iloc[-20:].mean() if len(volume) >= 20 else volume.mean()
    last_vol  = volume.iloc[-1]
    prev_vol  = volume.iloc[-2]
    ratio     = last_vol / avg if avg > 0 else 1
    up_day    = close.iloc[-1] > close.iloc[-2]
    vol_falling = last_vol < prev_vol  # ווליום יורד ביחס לאתמול

    if up_day:
        if ratio > 1.2:
            signal = 'bullish'
            desc   = f"עלייה עם ווליום גבוה ({ratio:.1f}x ממוצע) — קונים נכנסו בכוח"
        else:
            signal = 'neutral'
            desc   = f"עלייה עם ווליום נמוך ({ratio:.1f}x) — עלייה בלי ביטחון"
    else:
        # יום ירידה
        if vol_falling and ratio < 1.0:
            signal = 'bullish'
            desc   = f"ירידה עם ווליום יורד ({ratio:.1f}x) — המוכרים נחלשים, ריקושט מתקרב"
        elif ratio > 1.3:
            signal = 'bearish'
            desc   = f"ירידה עם ווליום גבוה ({ratio:.1f}x) — מוכרים חזקים, זהירות"
        elif vol_falling:
            signal = 'neutral'
            desc   = f"ירידה עם ווליום קצת יורד ({ratio:.1f}x) — עייפות מוכרים"
        else:
            signal = 'neutral'
            desc   = f"ירידה עם ווליום ממוצע ({ratio:.1f}x)"

    return {
        'signal': signal, 'description': desc,
        'ratio': round(ratio, 2),
        'last_volume': int(last_vol),
        'avg_volume': int(avg)
    }


def analyze_ma20(close, weekly_change=0):
    """
    לפי שיטת לייב 20:
    - מחיר מעל MA20 + ממוצע עולה = טרנד בוליש, כניסה עם הטרנד
    - מחיר רחוק מאוד מתחת ל-MA20 (>5%) = הזדמנות ריקושט — מניות חוזרות לממוצע
    - מחיר קצת מתחת ל-MA20 = בריש / ניטרלי
    """
    if len(close) < 21:
        return {'signal': 'neutral', 'description': 'אין מספיק נתונים', 'ma20': 0, 'distance_pct': 0}

    ma = calc_ma20(close)
    curr_price = close.iloc[-1]
    curr_ma    = ma.iloc[-1]
    prev_ma    = ma.iloc[-2]

    above     = curr_price > curr_ma
    ma_rising = curr_ma > prev_ma
    dist      = (curr_price - curr_ma) / curr_ma * 100

    if above and ma_rising:
        signal = 'bullish'
        desc = f"מחיר מעל ממוצע 20 עולה ({dist:+.1f}%) — טרנד בוליש קלאסי"
    elif above and dist > 3 and weekly_change > 2:
        signal = 'bullish'
        desc = f"מחיר {dist:+.1f}% מעל ממוצע 20 עם שבוע חזק — מומנטום בוליש"
    elif above:
        signal = 'neutral'
        desc = f"מחיר מעל ממוצע 20 אבל הממוצע מאט ({dist:+.1f}%)"
    elif dist < -8:
        # רחוק מאוד מתחת = הזדמנות ריקושט לפי לייב 20
        signal = 'bullish'
        desc = f"מחיר {dist:.1f}% מתחת ממוצע 20 — מרחק אדיר, מניות חוזרות לממוצע (לייב 20)"
    elif dist < -4:
        signal = 'neutral'
        desc = f"מחיר {dist:.1f}% מתחת ממוצע 20 — מתקרב לאזור ריקושט"
    else:
        signal = 'bearish'
        desc = f"מחיר {dist:.1f}% מתחת ממוצע 20 — סביבה בריש, לא נכנסים"

    return {
        'signal': signal, 'description': desc,
        'current_price': round(curr_price, 2),
        'ma20': round(curr_ma, 2),
        'distance_pct': round(dist, 2)
    }


def analyze_gaps(open_p, high, low, close, today_open=None):
    gaps_up, gaps_down = [], []
    lookback = min(30, len(open_p) - 1)

    for i in range(len(open_p) - lookback, len(open_p)):
        if i == 0:
            continue
        prev_h = high.iloc[i - 1]
        prev_l = low.iloc[i - 1]
        curr_o = open_p.iloc[i]
        date   = str(open_p.index[i].date())

        if curr_o > prev_h * 1.002:
            future_lows = low.iloc[i:]
            filled = future_lows.min() <= prev_h
            if not filled:
                gaps_up.append({'level': round(prev_h, 2), 'date': date})
        elif curr_o < prev_l * 0.998:
            future_highs = high.iloc[i:]
            filled = future_highs.max() >= prev_l
            if not filled:
                gaps_down.append({'level': round(prev_l, 2), 'date': date})

    # בדוק גאפ של היום — open היום מול high של אתמול
    if today_open is not None:
        last_high = high.iloc[-1]
        last_low  = low.iloc[-1]
        today_date = 'היום'
        if today_open > last_high * 1.002:
            gaps_up.append({'level': round(last_high, 2), 'date': today_date})
        elif today_open < last_low * 0.998:
            gaps_down.append({'level': round(last_low, 2), 'date': today_date})

    if gaps_up and not gaps_down:
        signal = 'bullish'
        desc = f"יש {len(gaps_up)} גאפ פתוח למעלה - מגנט עולה"
    elif gaps_down and not gaps_up:
        signal = 'bearish'
        desc = f"יש {len(gaps_down)} גאפ פתוח למטה - מגנט יורד"
    elif gaps_up and gaps_down:
        signal = 'neutral'
        desc = f"גאפים בשני הכיוונים ({len(gaps_up)} למעלה, {len(gaps_down)} למטה)"
    else:
        signal = 'neutral'
        desc = "אין גאפים פתוחים משמעותיים"

    return {
        'signal': signal, 'description': desc,
        'gaps_up': gaps_up[-3:],
        'gaps_down': gaps_down[-3:]
    }


def analyze_cci(high, low, close):
    """
    לפי שיטת לייב 20 (סדר עדיפות אחרון לפי מיכה):
    הסיגנל הכי חשוב: CCI חוצה את -100 כלפי מעלה = אות כניסה
    - CCI עבר מתחת -100 ועכשיו חוצה למעלה = STRONG BULLISH
    - CCI מתחת -100 ועולה = בוליש (ריקושט)
    - CCI עובר 0 כלפי מטה = בריש
    - CCI מעל +100 = מומנטום, עם טרנד = בוליש
    """
    if len(close) < 15:
        return {'signal': 'neutral', 'description': 'אין מספיק נתונים', 'value': 0}

    cci    = calc_cci(high, low, close, period=14)
    val    = cci.iloc[-1]
    prev   = cci.iloc[-2]
    prev2  = cci.iloc[-3] if len(cci) > 2 else prev
    rising = val > prev

    # כללי CCI לפי מיכה סטוקס (מהסרטון w-NGbxzMcDY):
    # - עובר מעל -100 = איתות קנייה
    # - מעל 0 = 2-4 ימים נוספים של עליות
    # - מעל +100 = אוברבוט (אבל יכול להמשיך)
    # - עובר מתחת 0 = איתות מכירה, 2-4 ימים נוספים של ירידות
    crossed_minus100_up = prev < -100 <= val
    crossed_zero_down   = prev >= 0 > val
    crossed_zero_up     = prev < 0 <= val

    if crossed_minus100_up:
        signal = 'bullish'
        desc = f"CCI(14) = {val:.0f} — חצה את -100 כלפי מעלה! איתות קנייה חזק 🎯"
    elif crossed_zero_down:
        signal = 'bearish'
        desc = f"CCI(14) = {val:.0f} — חצה את קו האפס כלפי מטה — 2-4 ימי ירידות צפויים"
    elif crossed_zero_up:
        signal = 'bullish'
        desc = f"CCI(14) = {val:.0f} — חצה את קו האפס כלפי מעלה — 2-4 ימי עליות צפויים"
    elif val < -100 and rising:
        signal = 'bullish'
        desc = f"CCI(14) = {val:.0f} — אקסטרים אוסלד ועולה, ממתין לחציית -100"
    elif val < -100:
        signal = 'neutral'
        desc = f"CCI(14) = {val:.0f} — אוסלד קיצוני, עדיין יורד. המתן לחציית -100"
    elif val > 100 and rising:
        signal = 'bullish'
        desc = f"CCI(14) = {val:.0f} — אוברבוט ועולה, מומנטום חזק"
    elif val > 100 and not rising:
        signal = 'neutral'
        desc = f"CCI(14) = {val:.0f} — אוברבוט ומאט, שים לב לתיקון"
    elif val > 0:
        signal = 'neutral'
        desc = f"CCI(14) = {val:.0f} — טריטוריה חיובית, ניטרלי"
    else:
        signal = 'neutral'
        desc = f"CCI(14) = {val:.0f} — שלילי, מומנטום חלש"

    return {'signal': signal, 'description': desc, 'value': round(val, 2)}


def self_diagnose(df, trend, candle, volume, ma20, gaps, cci,
                  bullish, bearish, neutral, contradictory,
                  atr_val, current_price, next_earnings):
    """
    בודק את איכות הניתוח ומחזיר דו"ח שיפורים + ציון אמינות.
    """
    issues      = []  # בעיות / אזהרות
    strengths   = []  # חוזקות של הניתוח הנוכחי
    suggestions = []  # המלצות לשיפור
    score       = 100  # מתחיל מ-100 ומורד נקודות

    # ── 1. איכות נתונים ──
    days = len(df)
    if days < 30:
        issues.append({'level': 'error', 'text': f'רק {days} ימי מסחר בהיסטוריה — מינימום 30 לניתוח אמין'})
        suggestions.append('לחכות לצבירת נתונים נוספים לפני כניסה')
        score -= 20
    elif days < 50:
        issues.append({'level': 'warning', 'text': f'{days} ימי מסחר — ניתוח סביר אך לא אופטימלי'})
        score -= 5

    # ── 2. ווליום ──
    vol_ratio = volume.get('ratio', 1)
    avg_vol   = volume.get('avg_volume', 0)
    if avg_vol < 100_000:
        issues.append({'level': 'error', 'text': f'ווליום ממוצע נמוך מאוד ({avg_vol:,}) — מניה לא נזילה, סיכון גבוה'})
        suggestions.append('הימנע ממניות עם ווליום ממוצע מתחת ל-100K — קשה לצאת בזמן')
        score -= 25
    elif avg_vol < 500_000:
        issues.append({'level': 'warning', 'text': f'ווליום ממוצע בינוני ({avg_vol:,}) — היזהר עם גודל פוזיציה'})
        score -= 10

    if vol_ratio < 0.4:
        issues.append({'level': 'warning', 'text': f'ווליום היום נמוך מאוד ({vol_ratio:.1f}x ממוצע) — אין עניין מוסדי'})
        suggestions.append('המתן לעלייה בווליום לפני כניסה — זה מאשש את הכיוון')
        score -= 10
    elif vol_ratio > 2.0:
        strengths.append(f'ווליום גבוה ({vol_ratio:.1f}x ממוצע) — עניין מוסדי ברור, אמינות גבוהה')

    # ── 3. נתונים סותרים ──
    if contradictory:
        issues.append({'level': 'error', 'text': 'נר ווליום סותרים — לפי מיכה: "נתונים סותרים = לא להיכנס"'})
        suggestions.append('המתן עד שהנר והווליום מסמנים אותו כיוון לפני כניסה')
        score -= 20

    # ── 4. ריבוי ניטרלים ──
    if neutral >= 3:
        issues.append({'level': 'warning', 'text': f'{neutral} פרמטרים ניטרליים — תמונה מעורבת, קשה להגיד כיוון'})
        suggestions.append('חכה לירידת ניטרליים — צריך לפחות 4 בוליש ברורים')
        score -= 10

    # ── 5. CCI ──
    cci_val = cci.get('value', 0)
    if -100 <= cci_val <= 100 and cci_val < 0:
        issues.append({'level': 'info', 'text': f'CCI = {cci_val:.0f} — שלילי אבל לא קיצוני. מחכים לחציית -100'})
    elif cci_val < -100:
        strengths.append(f'CCI = {cci_val:.0f} — אוסלד קיצוני, קרוב לאות כניסה (חציית -100)')

    # ── 6. MA20 ──
    dist = ma20.get('distance_pct', 0)
    if -4 < dist < 0:
        issues.append({'level': 'info', 'text': f'מחיר {dist:.1f}% מתחת MA20 — "אזור אפור", קצת רחוק אבל לא מספיק'})
        suggestions.append(f'לפי לייב 20: כניסה עדיפה כש-8%+ מתחת ל-MA20 (כרגע {dist:.1f}%)')
        score -= 5
    elif dist < -8:
        strengths.append(f'מחיר {dist:.1f}% מתחת MA20 — "גומיה מתוחה" — סיכוי ריקושט גבוה')

    # ── 7. ATR / תנודתיות ──
    atr_pct = atr_val / current_price * 100 if current_price > 0 else 0
    if atr_pct > 8:
        issues.append({'level': 'warning', 'text': f'ATR = {atr_pct:.1f}% — תנודתיות גבוהה מאוד, SL רחוק = סיכון גדול'})
        suggestions.append(f'תנודתיות {atr_pct:.1f}% — הקטן גודל פוזיציה בהתאם לניהול סיכון')
        score -= 8
    elif atr_pct < 1:
        issues.append({'level': 'info', 'text': f'ATR = {atr_pct:.1f}% — תנודתיות נמוכה מאוד, קשה לעשות כסף'})
        score -= 5

    # ── 8. דוח קרוב ──
    if next_earnings:
        import datetime
        try:
            days_to_earnings = (datetime.date.fromisoformat(next_earnings) - datetime.date.today()).days
            if 0 < days_to_earnings <= 7:
                issues.append({'level': 'error', 'text': f'דוח רבעוני בעוד {days_to_earnings} ימים! — סיכון גבוה לתנועה חדה'})
                suggestions.append('לא להיכנס לפני דוח — אלא אם כן מאד בטוח. שקול לחכות ליום אחרי הדוח.')
                score -= 20
            elif 0 < days_to_earnings <= 14:
                issues.append({'level': 'warning', 'text': f'דוח רבעוני בעוד {days_to_earnings} ימים — שים לב'})
                suggestions.append(f'עוד {days_to_earnings} ימים לדוח — שים SL הדוק יותר')
                score -= 8
        except Exception:
            pass

    # ── 9. חוזקות נוספות ──
    if bullish >= 5:
        strengths.append('5+ פרמטרים בוליש — סטאפ חזק ביותר, לפי מיכה כניסה מלאה')
    elif bullish >= 4:
        strengths.append('4 פרמטרים בוליש — סטאפ טוב, כניסה חלקית מוצדקת')

    if gaps.get('gaps_up') and len(gaps['gaps_up']) > 0:
        strengths.append(f'גאפ פתוח למעלה ב-{gaps["gaps_up"][0]["level"]} — יעד ברור (80% גאפים נסגרים)')

    if trend.get('five_day_warning'):
        issues.append({'level': 'info', 'text': f'{trend["consecutive_days"]} ימים רצופים — כלל 5 הימים: צפה לשינוי כיוון'})

    # ── ציון סופי ──
    score = max(0, min(100, score))
    if score >= 80:
        score_label = 'ניתוח אמין'
        score_color = 'green'
    elif score >= 60:
        score_label = 'ניתוח סביר'
        score_color = 'yellow'
    elif score >= 40:
        score_label = 'ניתוח חלש'
        score_color = 'orange'
    else:
        score_label = 'ניתוח לא אמין'
        score_color = 'red'

    return {
        'score': score,
        'score_label': score_label,
        'score_color': score_color,
        'issues': issues,
        'strengths': strengths,
        'suggestions': suggestions,
    }


def generate_narrative(ticker, company_name, current_price, currency,
                        trend, candle, volume, ma20, gaps, cci,
                        bullish, bearish, neutral, rec_key):
    """מייצר ניתוח טקסטואלי בסגנון מיכה סטוקס"""

    lines = []

    # ── פתיח ──
    trend_word = 'עולה' if trend['signal'] == 'bullish' else ('יורד' if trend['signal'] == 'bearish' else 'צידי')
    lines.append(
        f"אז בואו נדבר על {company_name} ({ticker}), שנסחרת כרגע ב-{current_price} {currency}. "
        f"הטרנד של החודש האחרון הוא {trend_word} — "
        f"המניה עשתה {trend['monthly_change']:+.1f}% בחודש ו-{trend['weekly_change']:+.1f}% בשבוע האחרון."
    )

    # כלל 5 ימים
    if trend.get('five_day_warning'):
        dir_heb = 'ירוקים' if trend['consecutive_dir'] == 'up' else 'אדומים'
        opp_heb = 'תיקון ירידה' if trend['consecutive_dir'] == 'up' else 'ריקושט עלייה'
        lines.append(
            f"שימו לב — {trend['consecutive_days']} ימים רצופים {dir_heb}. "
            f"לפי כלל 5 הימים, זה הזמן לצפות ל{opp_heb}. לא מוחלט, אבל שווה לשים לב."
        )

    # ── נרות ──
    candle_signal = candle['signal']
    if candle_signal == 'bullish':
        lines.append(
            f"הנר של אתמול אומר לנו סיפור בוליש: {candle['description']}. "
            "זה בדיוק מה שאנחנו רוצים לראות לפני כניסה — הקונים נכנסו בכוח."
        )
    elif candle_signal == 'bearish':
        lines.append(
            f"הנר של אתמול מדליק נורה אדומה: {candle['description']}. "
            "המוכרים השתלטו — צריך להיות זהירים כאן."
        )
    else:
        lines.append(
            f"הנר של אתמול לא נותן לנו הרבה מידע: {candle['description']}. "
            "אין כיוון ברור, לחכות לאישור."
        )

    # ── ווליום ──
    if volume['signal'] == 'bullish':
        lines.append(
            f"מה שמחזק את התמונה הבוליש זה הווליום — {volume['description']}. "
            "כשיש ווליום גדול בצד הקונים, זה מראה שהשוק באמת מאמין בעלייה."
        )
    elif volume['signal'] == 'bearish':
        lines.append(
            f"הווליום מדאיג אותי כאן — {volume['description']}. "
            "ווליום גדול בירידה אומר שהמוסדיים יוצאים, לא לרוץ להיכנס."
        )
    else:
        lines.append(
            f"הווליום הוא {volume['description']}. "
            "אין confirmation חזק בשום כיוון מהווליום."
        )

    # ── ממוצע 20 ──
    dist = ma20['distance_pct']
    if ma20['signal'] == 'bullish' and dist < -5:
        lines.append(
            f"ממוצע 20 — וזה הלב של לייב 20. המניה {dist:.1f}% מתחת לממוצע. "
            "מרחק כזה מהממוצע זה בדיוק מה שאנחנו מחפשים — ההיסטוריה מראה שמניות חוזרות לממוצע. "
            f"המטרה הראשונה שלנו זה הממוצע ב-{ma20['ma20']}."
        )
    elif ma20['signal'] == 'bullish':
        lines.append(
            f"ממוצע 20 — המניה {dist:+.1f}% מעל הממוצע והממוצע עולה. סביבה בוליש קלאסית."
        )
    elif ma20['signal'] == 'bearish':
        lines.append(
            f"ממוצע 20 — המניה קצת מתחת לממוצע ({dist:.1f}%) אבל לא מספיק רחוק כדי לדבר על ריקושט. "
            "זה האזור הכי לא נעים — לא ברור לאן."
        )
    else:
        lines.append(
            f"ממוצע 20 — המניה {dist:.1f}% מהממוצע, מתחילה להתקרב לאזור שמעניין אותנו. "
            "עוד לא שם, אבל שווה לנטר."
        )

    # ── גאפים ──
    if gaps['signal'] == 'bullish':
        levels = ', '.join(str(g['level']) for g in gaps['gaps_up'])
        lines.append(
            f"גאפים — יש גאפ פתוח למעלה ב-{levels}. "
            "גאפים פועלים כמגנטים, ויכולים למשוך את המחיר כלפי מעלה."
        )
    elif gaps['signal'] == 'bearish':
        levels = ', '.join(str(g['level']) for g in gaps['gaps_down'])
        lines.append(
            f"זהירות עם הגאפ — יש גאפ פתוח למטה ב-{levels}. "
            "גאפ פתוח מתחת למחיר הנוכחי הוא משקולת שמושכת למטה."
        )
    else:
        lines.append("אין גאפים פתוחים משמעותיים בסביבה הקרובה.")

    # ── CCI ──
    if cci['signal'] == 'bullish':
        lines.append(
            f"ה-CCI(14) עומד על {cci['value']:.0f} — מעל +100 זה אומר מומנטום בוליש חזק. "
            "כן, זה נשמע כמו 'קנייה יתר' אבל עם טרנד חזק זה בדיוק מה שאנחנו רוצים לראות."
        )
    elif cci['signal'] == 'bearish':
        lines.append(
            f"ה-CCI(14) עומד על {cci['value']:.0f} — מתחת ל-100 מינוס. "
            "מומנטום בריש חזק, המוכרים שולטים בקצב."
        )
    else:
        lines.append(
            f"ה-CCI(14) עומד על {cci['value']:.0f} — בטווח הניטרלי. "
            "אין מומנטום חזק בשום כיוון, השוק בלבול."
        )

    # ── סיכום ──
    if rec_key == 'strong-buy':
        pct = 100
        lines.append(
            f"\n📊 סיכום: {bullish} מתוך 6 אינדיקטורים בוליש — זאת תמונה מאוד חזקה. "
            f"אני הייתי נכנס ב-{pct}% מהפוזיציה המתוכננת. "
            "לשים SL מתחת לממוצע 20 ולנטר."
        )
    elif rec_key == 'buy':
        pct = 50
        lines.append(
            f"\n📊 סיכום: {bullish} מתוך 6 אינדיקטורים בוליש — תמונה טובה אבל לא מושלמת. "
            f"כניסה חלקית של {pct}% מהפוזיציה המתוכננת. "
            "אם המניה תאשר עם ווליום, להשלים את הפוזיציה."
        )
    elif rec_key == 'sell':
        pct = 0
        lines.append(
            f"\n📊 סיכום: {bearish} מתוך 6 אינדיקטורים בריש — אנחנו לא נכנסים לכאן. "
            f"אחוז כניסה: {pct}%. "
            "להמתין לשינוי כיוון לפני שמחליטים להיכנס."
        )
    elif rec_key == 'strong-sell':
        pct = 0
        lines.append(
            f"\n📊 סיכום: {bearish} מתוך 6 אינדיקטורים בריש — תמונה קשה. "
            f"אחוז כניסה: {pct}%. להישאר בצד ולחכות."
        )
    else:
        pct = 25
        lines.append(
            f"\n📊 סיכום: תמונה מעורבת — {bullish} בוליש, {bearish} בריש, {neutral} ניטרלי. "
            f"אם בכל זאת רוצים להיכנס, לא יותר מ-{pct}% מהפוזיציה. "
            "עדיף לחכות לאישור ברור יותר."
        )

    return {
        'text': ' '.join(lines),
        'position_pct': pct if rec_key in ('strong-buy','buy','neutral') else 0
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/analyze')
def analyze():
    ticker = request.args.get('ticker', '').upper().strip()
    if not ticker:
        return jsonify({'error': 'נא להזין טיקר'}), 400

    try:
        stock = _ticker(ticker)
        df = None
        for attempt in range(3):
            try:
                df = stock.history(period='3mo')
                if not df.empty:
                    break
            except Exception:
                pass
            time.sleep(2)
        if df is None or df.empty:
            return jsonify({'error': f'לא נמצאו נתונים עבור {ticker}. בדוק שהטיקר נכון.'}), 404

        info         = stock.info
        company_name = info.get('longName', ticker)
        currency     = info.get('currency', 'USD')

        # Run all 6 analyses
        trend   = analyze_trend(df['Close'])
        candle  = analyze_candle(df['Open'], df['High'], df['Low'], df['Close'])
        volume  = analyze_volume(df['Volume'], df['Close'])
        ma20    = analyze_ma20(df['Close'], weekly_change=trend['weekly_change'])

        # גאפ היום: נסה לקבל מחיר פתיחה עדכני
        try:
            fi = stock.fast_info
            today_open = fi['open'] if fi and 'open' in fi else None
        except Exception:
            today_open = None
        gaps    = analyze_gaps(df['Open'], df['High'], df['Low'], df['Close'], today_open=today_open)
        cci     = analyze_cci(df['High'], df['Low'], df['Close'])

        analyses = [trend, candle, volume, ma20, gaps, cci]
        bullish  = sum(1 for a in analyses if a['signal'] == 'bullish')
        bearish  = sum(1 for a in analyses if a['signal'] == 'bearish')
        neutral  = sum(1 for a in analyses if a['signal'] == 'neutral')

        if bullish >= 5:
            rec, rec_key = 'כניסה חזקה! רוב האינדיקטורים בוליש', 'strong-buy'
        elif bullish >= 4:
            rec, rec_key = 'כניסה אפשרית - לנטר מקרוב', 'buy'
        elif bullish >= 3 and bearish == 0:
            rec, rec_key = 'כניסה חלקית — אין בריש, 3 בוליש חזקים', 'buy'
        elif bearish >= 5:
            rec, rec_key = 'המנע! שוק בריש חזק', 'strong-sell'
        elif bearish >= 4:
            rec, rec_key = 'לא להיכנס - רוב האינדיקטורים בריש', 'sell'
        else:
            rec, rec_key = 'המתן - תמונה מעורבת, אין כיוון ברור', 'neutral'

        prev_close      = round(df['Close'].iloc[-1], 2)
        prev_prev_close = round(df['Close'].iloc[-2], 2) if len(df) >= 2 else prev_close
        yesterday_change     = round(prev_close - prev_prev_close, 2)
        yesterday_change_pct = round(yesterday_change / prev_prev_close * 100, 2) if prev_prev_close else 0

        # מחיר עדכני — פרה/אפטר מרקט קודם, אחר כך מחיר רגיל
        try:
            fi  = stock.fast_info
            live = (
                getattr(fi, 'preMarketPrice', None) or
                getattr(fi, 'postMarketPrice', None) or
                info.get('preMarketPrice') or
                info.get('postMarketPrice') or
                getattr(fi, 'lastPrice', None) or
                info.get('currentPrice') or
                info.get('regularMarketPrice')
            )
            current_price = round(float(live), 2) if live else prev_close
        except Exception:
            current_price = prev_close

        change     = round(current_price - prev_close, 2)
        change_pct = round(change / prev_close * 100, 2) if prev_close else 0

        # ATR (14)
        atr_series = calc_atr(df['High'], df['Low'], df['Close'])
        atr_val    = round(atr_series.iloc[-1], 2)
        atr_pct    = round(atr_val / current_price * 100, 2)

        # פיבונאצ'י — מהשיא לשפל של 3 חודשים
        fib = calc_fibonacci(df['High'], df['Low'])
        # כמה % תיקנה המניה מהשיא (לפי 0.618)
        fib_retrace = round((df['High'].max() - current_price) / (df['High'].max() - df['Low'].min()) * 100, 1) if df['High'].max() != df['Low'].min() else 0

        # ── תוכנית מסחר (כניסה / יציאה / SL) ──
        entry_price = current_price
        last_low    = round(df['Low'].iloc[-1], 2)
        last_high   = round(df['High'].iloc[-1], 2)
        ma20_val    = round(calc_ma20(df['Close']).iloc[-1], 2)

        # SL = שפל נר הכניסה
        sl_price = last_low

        # Target 1 = ממוצע 20 (אפקט גומיה)
        target1 = ma20_val if ma20_val > current_price else round(current_price * 1.08, 2)

        # Target 2 = ההתנגדות הקרובה הבאה / גאפ / 10% מעל
        gap_targets = [g['level'] for g in (gaps.get('gaps_up') or []) if g['level'] > current_price]
        target2 = min(gap_targets) if gap_targets else round(current_price * 1.15, 2)

        # R:R
        risk   = round(current_price - sl_price, 2)
        reward = round(target1 - current_price, 2)
        rr     = round(reward / risk, 2) if risk > 0 else None

        trade_plan = {
            'entry': entry_price,
            'sl': sl_price,
            'target1': target1,
            'target2': target2,
            'risk': risk,
            'reward': reward,
            'rr': rr,
            'currency': currency,
        }

        # אזהרת נתונים סותרים (לפי כלל מיכה)
        contradictory = (candle['signal'] == 'bullish' and volume['signal'] == 'bearish') or \
                        (candle['signal'] == 'bearish' and volume['signal'] == 'bullish')

        # ── חדשות ──
        news_list = []
        try:
            raw_news = stock.news or []
            for item in raw_news[:8]:
                # yfinance new format: nested under 'content'
                content = item.get('content', item)
                title = content.get('title', '') or item.get('title', '')
                provider = content.get('provider', {})
                publisher = provider.get('displayName', '') if isinstance(provider, dict) else item.get('publisher', '')
                canonical = content.get('canonicalUrl', {}) or content.get('clickThroughUrl', {})
                link = canonical.get('url', '') if isinstance(canonical, dict) else item.get('link', '')
                pub_str = content.get('pubDate', '') or content.get('displayTime', '')
                import datetime
                if pub_str:
                    try:
                        pub_date = datetime.datetime.strptime(pub_str[:16], '%Y-%m-%dT%H:%M').strftime('%d/%m %H:%M')
                    except Exception:
                        pub_date = pub_str[:10]
                else:
                    pub_ts = item.get('providerPublishTime', 0)
                    pub_date = datetime.datetime.fromtimestamp(pub_ts).strftime('%d/%m %H:%M') if pub_ts else ''
                # זיהוי חדשות שיכולות להזיז את המחיר
                keywords = ['earnings', 'revenue', 'guidance', 'merger', 'acquisition',
                            'lawsuit', 'sec', 'fda', 'deal', 'contract', 'beat', 'miss',
                            'upgrade', 'downgrade', 'buyback', 'dividend', 'recall',
                            'investigation', 'bankruptcy', 'layoff', 'partnership',
                            'דוח', 'רכישה', 'שותפות', 'הפסד', 'רווח']
                is_mover = any(kw.lower() in title.lower() for kw in keywords)
                news_list.append({
                    'title': title,
                    'publisher': publisher,
                    'link': link,
                    'date': pub_date,
                    'is_mover': is_mover
                })
        except Exception:
            pass

        # ── דוחות רבעוניים ──
        earnings_data = []
        next_earnings = None
        try:
            # דוחות היסטוריים
            qe = stock.quarterly_income_stmt
            if qe is not None and not qe.empty:
                for col in qe.columns[:4]:
                    try:
                        rev = qe.loc['Total Revenue', col] if 'Total Revenue' in qe.index else None
                        net = qe.loc['Net Income', col] if 'Net Income' in qe.index else None
                        eps_row = [r for r in qe.index if 'EPS' in str(r) or 'Diluted' in str(r)]
                        earnings_data.append({
                            'date': str(col.date()) if hasattr(col, 'date') else str(col)[:10],
                            'revenue': int(rev) if rev is not None and pd.notna(rev) else None,
                            'net_income': int(net) if net is not None and pd.notna(net) else None,
                        })
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            # תאריך הדוח הבא
            cal = stock.calendar
            if cal is not None:
                if isinstance(cal, dict):
                    ed = cal.get('Earnings Date', None)
                    if ed is not None:
                        if hasattr(ed, '__iter__') and not isinstance(ed, str):
                            ed = list(ed)
                            next_earnings = str(ed[0])[:10] if ed else None
                        else:
                            next_earnings = str(ed)[:10]
                elif hasattr(cal, 'loc'):
                    try:
                        ed = cal.loc['Earnings Date']
                        next_earnings = str(ed.iloc[0])[:10] if hasattr(ed, 'iloc') else str(ed)[:10]
                    except Exception:
                        pass
        except Exception:
            pass

        # ── שורט ──
        short_pct = None
        short_shares = None
        short_ratio = None
        try:
            short_pct    = info.get('shortPercentOfFloat', None)
            short_shares = info.get('sharesShort', None)
            short_ratio  = info.get('shortRatio', None)   # days to cover
            if short_pct is not None:
                short_pct = round(short_pct * 100, 2)
        except Exception:
            pass

        # ── דו"ח עצמי ──
        diagnosis = self_diagnose(
            df, trend, candle, volume, ma20, gaps, cci,
            bullish, bearish, neutral, contradictory,
            atr_val, current_price, next_earnings
        )

        # Narrative + position sizing
        narrative = generate_narrative(
            ticker, company_name, current_price, currency,
            trend, candle, volume, ma20, gaps, cci,
            bullish, bearish, neutral, rec_key
        )

        # Chart data — calculate on full history, display last 14 trading days
        ma20_series = calc_ma20(df['Close'])
        cci_series  = calc_cci(df['High'], df['Low'], df['Close'], period=14)

        display_n = 14
        df14 = df.iloc[-display_n:]
        ma20_14 = ma20_series.iloc[-display_n:]
        cci_14  = cci_series.iloc[-display_n:]

        chart = {
            'dates':  [str(d.date()) for d in df14.index],
            'open':   df14['Open'].round(2).tolist(),
            'high':   df14['High'].round(2).tolist(),
            'low':    df14['Low'].round(2).tolist(),
            'close':  df14['Close'].round(2).tolist(),
            'volume': df14['Volume'].tolist(),
            'ma20':   [round(x, 2) if pd.notna(x) else None for x in ma20_14],
            'cci':    [round(x, 2) if pd.notna(x) else None for x in cci_14],
        }

        # Support/resistance levels (simple: recent swing highs/lows)
        highs  = df['High'].tolist()
        lows   = df['Low'].tolist()
        levels = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                levels.append({'type': 'resistance', 'price': round(highs[i], 2), 'date': str(df.index[i].date())})
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                levels.append({'type': 'support', 'price': round(lows[i], 2), 'date': str(df.index[i].date())})

        return jsonify({
            'ticker': ticker,
            'company_name': company_name,
            'currency': currency,
            'current_price': current_price,
            'prev_close': prev_close,
            'yesterday_change': yesterday_change,
            'yesterday_change_pct': yesterday_change_pct,
            'change': change,
            'change_pct': change_pct,
            'trend': trend,
            'candle': candle,
            'volume': volume,
            'ma20': ma20,
            'gaps': gaps,
            'cci': cci,
            'bullish_count': bullish,
            'bearish_count': bearish,
            'neutral_count': neutral,
            'recommendation': rec,
            'rec_key': rec_key,
            'narrative': narrative['text'],
            'position_pct': narrative['position_pct'],
            'contradictory': contradictory,
            'consecutive_days': trend['consecutive_days'],
            'consecutive_dir': trend['consecutive_dir'],
            'five_day_warning': trend['five_day_warning'],
            'atr': atr_val,
            'atr_pct': atr_pct,
            'fibonacci': fib,
            'fib_retrace_pct': fib_retrace,
            'chart': chart,
            'levels': levels[-10:],
            'news': news_list,
            'earnings': earnings_data,
            'next_earnings': next_earnings,
            'short_pct': short_pct,
            'short_shares': short_shares,
            'short_ratio': short_ratio,
            'trade_plan': trade_plan,
            'diagnosis': diagnosis,
        })

    except Exception as e:
        return jsonify({'error': f'שגיאה בניתוח: {str(e)}'}), 500


@app.route('/price')
def get_price():
    """Lightweight endpoint for live price updates (no heavy analysis)"""
    ticker = request.args.get('ticker', '').upper().strip()
    if not ticker:
        return jsonify({'error': 'no ticker'}), 400
    try:
        stock = _ticker(ticker)
        df = stock.history(period='2d')
        if df.empty or len(df) < 2:
            return jsonify({'error': 'no data'}), 404
        prev_close      = round(df['Close'].iloc[-1], 2)
        prev_prev_close = round(df['Close'].iloc[-2], 2) if len(df) >= 2 else prev_close
        yesterday_change     = round(prev_close - prev_prev_close, 2)
        yesterday_change_pct = round(yesterday_change / prev_prev_close * 100, 2) if prev_prev_close else 0
        info = stock.info

        # מחיר עדכני — פרה/אפטר מרקט קודם
        try:
            fi  = stock.fast_info
            live = (
                getattr(fi, 'preMarketPrice', None) or
                getattr(fi, 'postMarketPrice', None) or
                info.get('preMarketPrice') or
                info.get('postMarketPrice') or
                getattr(fi, 'lastPrice', None) or
                info.get('currentPrice') or
                info.get('regularMarketPrice')
            )
            current_price = round(float(live), 2) if live else prev_close
        except Exception:
            current_price = prev_close

        change     = round(current_price - prev_close, 2)
        change_pct = round(change / prev_close * 100, 2) if prev_close else 0
        currency   = info.get('currency', 'USD')
        return jsonify({
            'current_price': current_price,
            'prev_close': prev_close,
            'yesterday_change': yesterday_change,
            'yesterday_change_pct': yesterday_change_pct,
            'change': change,
            'change_pct': change_pct,
            'currency': currency,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/drivers')
def market_drivers():
    """מזהה מה מניע את השוק עכשיו"""
    try:
        drivers = get_market_drivers()
        return jsonify({'drivers': drivers})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/market')
def market_overview():
    """נתוני שוק רחבים — VIX, Fear & Greed, DXY, ריבית, סקטורים, אירועים"""
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as ex:
            f_vix     = ex.submit(get_vix)
            f_fg      = ex.submit(get_fear_greed)
            f_dxy     = ex.submit(get_dxy)
            f_t10     = ex.submit(get_us10y)
            f_sectors = ex.submit(get_sector_performance)
            f_events  = ex.submit(lambda: get_upcoming_events(30))
            f_futures  = ex.submit(get_futures)
            f_mstatus  = ex.submit(get_market_status)
            f_extended = ex.submit(get_extended_hours)
            vix      = f_vix.result()
            fg       = f_fg.result()
            dxy      = f_dxy.result()
            t10      = f_t10.result()
            sectors  = f_sectors.result()
            events   = f_events.result()
            futures  = f_futures.result()
            mstatus  = f_mstatus.result()
            extended = f_extended.result()

        # המר numpy types לפייתון רגיל
        def clean(obj):
            if obj is None: return None
            return {k: (float(v) if hasattr(v, 'item') else v) for k, v in obj.items()}

        return jsonify({
            'vix':     clean(vix),
            'fg':      clean(fg),
            'dxy':     clean(dxy),
            'us10y':   clean(t10),
            'sectors': [{'ticker': s['ticker'], 'name': s['name'],
                         'change': float(s['change'])} for s in sectors],
            'events':  events,
            'market_status': mstatus,
            'extended': extended,
            'futures': [{
                'ticker':     f['ticker'],
                'name':       f['name'],
                'emoji':      f['emoji'],
                'short':      f['short'],
                'price':      float(f['price']),
                'change':     float(f['change']),
                'change_pts': float(f['change_pts']),
                'direction':  f['direction'],
                'color':      f['color'],
                'explain':    f['explain'],
            } for f in futures],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
