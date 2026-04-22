from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd
import numpy as np
import time
import requests
import datetime

def _ticker(symbol):
    return yf.Ticker(symbol)

def translate_he(text):
    """תרגום לעברית — מנסה כמה ממשקים בזה אחר זה"""
    if not text or not text.strip():
        return text

    # 1. Google Translate (ללא מפתח — endpoint ציבורי)
    try:
        import urllib.parse
        encoded = urllib.parse.quote(text[:500])
        url = (f'https://translate.googleapis.com/translate_a/single'
               f'?client=gtx&sl=en&tl=he&dt=t&q={encoded}')
        r = requests.get(url, timeout=6, headers={'User-Agent': 'Mozilla/5.0'})
        data = r.json()
        parts = [seg[0] for seg in data[0] if seg[0]]
        result = ''.join(parts).strip()
        if result and result != text:
            return result
    except Exception:
        pass

    # 2. MyMemory (גיבוי — מוגבל יומי)
    try:
        url = 'https://api.mymemory.translated.net/get'
        r = requests.get(url, params={'q': text[:400], 'langpair': 'en|he'}, timeout=5)
        result = r.json().get('responseData', {}).get('translatedText', '')
        if result and result != text and 'MYMEMORY WARNING' not in result:
            return result
    except Exception:
        pass

    return text
from market_data import (get_vix, get_fear_greed, get_dxy, get_us10y,
                         get_sector_performance, get_upcoming_events,
                         get_market_drivers, get_futures,
                         get_market_status, get_extended_hours)

app = Flask(__name__)

# ─── Cache ────────────────────────────────────────────────────────────────────
_cache = {}

def cache_get(key, ttl=300):
    entry = _cache.get(key)
    if entry and (time.time() - entry[0]) < ttl:
        return entry[1]
    return None

def cache_set(key, value):
    _cache[key] = (time.time(), value)

# ─── Indicators ───────────────────────────────────────────────────────────────

def calc_ma20(close):
    return close.rolling(window=20).mean()


def calc_cci(high, low, close, period=14):
    tp = (high + low + close) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma) / (0.015 * mad)


def calc_atr(high, low, close, period=14):
    # Wilder's RMA — זהה ל-TradingView (לא SMA)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


def calc_rsi(close, period=14):
    # Wilder's Smoothed MA — זהה לשיטת TradingView
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))


def calc_ma50(close):
    return close.rolling(window=50).mean()


def calc_fibonacci(df_6mo):
    """
    פיבונאצ'י נכון — מזהה swing high ו-swing low אמיתיים ב-6 חודשים אחורה.
    בטרנד עולה: מהשפל הגדול האחרון → לשיא האחרון.
    בטרנד יורד: מהשיא הגדול האחרון → לשפל האחרון.
    """
    high  = df_6mo['High'].values.astype(float)
    low   = df_6mo['Low'].values.astype(float)
    n     = len(high)
    if n < 20:
        return None

    window = 5  # מינימום נרות בכל צד לפיבוט משמעותי

    # מציאת כל פיבוטי השיא והשפל
    pivot_highs = []
    pivot_lows  = []
    for i in range(window, n - window):
        if high[i] == max(high[i - window:i + window + 1]):
            pivot_highs.append((i, high[i]))
        if low[i] == min(low[i - window:i + window + 1]):
            pivot_lows.append((i, low[i]))

    if not pivot_highs or not pivot_lows:
        # fallback: max/min פשוט
        swing_high = float(df_6mo['High'].max())
        swing_low  = float(df_6mo['Low'].min())
        direction  = 'up'
    else:
        last_ph_i, last_ph_v = pivot_highs[-1]   # שיא פיבוט אחרון
        last_pl_i, last_pl_v = pivot_lows[-1]    # שפל פיבוט אחרון

        if last_ph_i > last_pl_i:
            # השיא אחרי השפל → טרנד עולה: שפל → שיא
            direction  = 'up'
            swing_low  = last_pl_v
            swing_high = last_ph_v
        else:
            # השפל אחרי השיא → טרנד יורד: שיא → שפל
            direction  = 'down'
            swing_high = last_ph_v
            swing_low  = last_pl_v

    diff = swing_high - swing_low
    if diff == 0:
        return None

    # רמות פיבו תמיד ביחס ל-swing_high → swing_low (גם בטרנד יורד)
    return {
        'direction': direction,
        'high':  round(swing_high, 2),
        'low':   round(swing_low,  2),
        '236':   round(swing_high - diff * 0.236, 2),
        '382':   round(swing_high - diff * 0.382, 2),
        '500':   round(swing_high - diff * 0.500, 2),
        '618':   round(swing_high - diff * 0.618, 2),
        '786':   round(swing_high - diff * 0.786, 2),
    }


def detect_chart_patterns(df):
    """
    זיהוי תבניות גרף קלאסיות על היסטוריה של עד 3 חודשים.
    מחזיר רשימת תבניות שזוהו, עם תיאור + כיוון + רמות מפתח.
    """
    close  = df['Close'].values.astype(float)
    high   = df['High'].values.astype(float)
    low    = df['Low'].values.astype(float)
    volume = df['Volume'].values.astype(float)
    n = len(close)
    if n < 20:
        return []

    current = close[-1]
    patterns = []

    # ── עזר: מציאת שיאים ושפלים מקומיים ──
    def local_highs(window=5):
        idx = []
        for i in range(window, n - window):
            if high[i] == max(high[i-window:i+window+1]):
                idx.append(i)
        return idx

    def local_lows(window=5):
        idx = []
        for i in range(window, n - window):
            if low[i] == min(low[i-window:i+window+1]):
                idx.append(i)
        return idx

    highs_idx = local_highs()
    lows_idx  = local_lows()

    # ════════════════════════════════════════
    # 1. ספל-וידית (Cup and Handle)
    # ════════════════════════════════════════
    if n >= 40:
        # מחפשים: שיא שמאלי → ירידה לשפל → עלייה חזרה לשיא → ידית (תיקון קטן) → פריצה
        cup_window = min(n - 5, 50)
        cup_high   = max(high[-cup_window:-cup_window//2])
        cup_low    = min(low[-cup_window+5:-5])
        cup_right  = max(high[-cup_window//2:-3])
        handle_low = min(low[-10:])
        depth_pct  = (cup_high - cup_low) / cup_high * 100
        symmetry   = abs(cup_high - cup_right) / cup_high

        if (10 < depth_pct < 35 and                  # עומק ספל 10-35%
                symmetry < 0.08 and                   # שני צדדים סימטריים
                handle_low > cup_low and              # הידית מעל שפל הספל
                (cup_high - handle_low) / cup_high < 0.15 and  # ידית לא יותר מ-15%
                current > cup_right * 0.97):          # מחיר קרוב לשפה הימנית
            breakout_target = round(cup_high * (1 + depth_pct / 100), 2)
            patterns.append({
                'name': 'ספל-וידית (Cup & Handle)',
                'emoji': '☕',
                'signal': 'bullish',
                'strength': 'חזק',
                'description': (
                    f"תבנית ספל-וידית מתגבשת — עומק ספל {depth_pct:.1f}%. "
                    f"'The wider the base the higher to space.' "
                    f"פריצה מעל {round(cup_right,2)} עם ווליום = כניסה."
                ),
                'target': breakout_target,
                'key_level': round(cup_right, 2),
                'what_it_means': 'ירידה ועלייה בצורת U = ספל. תיקון קטן לאחר = ידית. ככל שהספל רחב יותר — הפריצה חזקה יותר.',
            })

    # ════════════════════════════════════════
    # 2. ראש-וכתפיים (Head & Shoulders) — בריש
    # ════════════════════════════════════════
    if len(highs_idx) >= 3:
        for i in range(len(highs_idx) - 2):
            ls, hd, rs = highs_idx[i], highs_idx[i+1], highs_idx[i+2]
            if (high[hd] > high[ls] * 1.02 and    # ראש גבוה משתי הכתפיים
                    high[hd] > high[rs] * 1.02 and
                    abs(high[ls] - high[rs]) / high[hd] < 0.06 and  # כתפיים סימטריות
                    rs > n - 20):                  # תבנית עדכנית
                # קו צוואר — ממוצע השפלים בין הכתפיים
                neckline_lows = [low[j] for j in range(ls, rs+1)]
                neckline = sum(sorted(neckline_lows)[:3]) / 3
                target = round(neckline - (high[hd] - neckline), 2)
                if current < high[hd] * 0.98:     # לא בשיא עצמו
                    patterns.append({
                        'name': 'ראש-וכתפיים (H&S)',
                        'emoji': '👤',
                        'signal': 'bearish',
                        'strength': 'חזק מאוד',
                        'description': (
                            f"ראש-וכתפיים בריש — קו צוואר ב-{round(neckline,2)}. "
                            f"שבירת הצוואר = אות מכירה. יעד: {target}."
                        ),
                        'target': target,
                        'key_level': round(neckline, 2),
                        'what_it_means': 'שלושה שיאים: השני הכי גבוה = ראש. שניים בצדדים = כתפיים. שבירת קו הצוואר = אות מכירה חזק.',
                    })
                    break

    # ════════════════════════════════════════
    # 3. ראש-וכתפיים הפוך — בוליש
    # ════════════════════════════════════════
    if len(lows_idx) >= 3:
        for i in range(len(lows_idx) - 2):
            ls, hd, rs = lows_idx[i], lows_idx[i+1], lows_idx[i+2]
            if (low[hd] < low[ls] * 0.98 and
                    low[hd] < low[rs] * 0.98 and
                    abs(low[ls] - low[rs]) / (low[hd] + 0.001) < 0.06 and
                    rs > n - 20):
                neckline_highs = [high[j] for j in range(ls, rs+1)]
                neckline = sum(sorted(neckline_highs)[-3:]) / 3
                target = round(neckline + (neckline - low[hd]), 2)
                if current > low[hd] * 1.02:
                    patterns.append({
                        'name': 'ראש-וכתפיים הפוך',
                        'emoji': '🔃',
                        'signal': 'bullish',
                        'strength': 'חזק מאוד',
                        'description': (
                            f"ראש-וכתפיים הפוך — בוליש. קו צוואר ב-{round(neckline,2)}. "
                            f"פריצה מעל הצוואר = אות קנייה. יעד: {target}."
                        ),
                        'target': target,
                        'key_level': round(neckline, 2),
                        'what_it_means': 'שלושה שפלים: השני הכי נמוך = ראש. שניים בצדדים = כתפיים. פריצת קו הצוואר למעלה = אות קנייה חזק.',
                    })
                    break

    # ════════════════════════════════════════
    # 4. משולש עולה (Ascending Triangle) — בוליש
    # ════════════════════════════════════════
    if n >= 15:
        recent_highs = [high[i] for i in highs_idx if i > n - 30]
        recent_lows  = [low[i]  for i in lows_idx  if i > n - 30]
        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            res_flat  = max(recent_highs)
            hi_spread = (max(recent_highs) - min(recent_highs)) / res_flat
            lo_rising = recent_lows[-1] > recent_lows[0] if len(recent_lows) >= 2 else False
            if hi_spread < 0.04 and lo_rising and current > min(recent_lows):
                patterns.append({
                    'name': 'משולש עולה (Ascending Triangle)',
                    'emoji': '📐',
                    'signal': 'bullish',
                    'strength': 'בינוני-חזק',
                    'description': (
                        f"משולש עולה — התנגדות שטוחה ב-{round(res_flat,2)}, שפלים עולים. "
                        f"פריצה עם ווליום = כניסה. יעד: {round(res_flat * 1.08, 2)}."
                    ),
                    'target': round(res_flat * 1.08, 2),
                    'key_level': round(res_flat, 2),
                    'what_it_means': 'התנגדות אופקית קבועה + שפלים עולים = לחץ קנייה מצטבר. ככל שהמחיר מתכווץ — הפריצה קרובה.',
                })

    # ════════════════════════════════════════
    # 5. משולש יורד (Descending Triangle) — בריש
    # ════════════════════════════════════════
    if n >= 15:
        recent_highs = [high[i] for i in highs_idx if i > n - 30]
        recent_lows  = [low[i]  for i in lows_idx  if i > n - 30]
        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            sup_flat  = min(recent_lows)
            lo_spread = (max(recent_lows) - min(recent_lows)) / (sup_flat + 0.001)
            hi_falling = recent_highs[-1] < recent_highs[0] if len(recent_highs) >= 2 else False
            if lo_spread < 0.04 and hi_falling and current < max(recent_highs):
                patterns.append({
                    'name': 'משולש יורד (Descending Triangle)',
                    'emoji': '📐',
                    'signal': 'bearish',
                    'strength': 'בינוני-חזק',
                    'description': (
                        f"משולש יורד — תמיכה שטוחה ב-{round(sup_flat,2)}, שיאים יורדים. "
                        f"שבירת תמיכה = סיגנל מכירה. יעד: {round(sup_flat * 0.92, 2)}."
                    ),
                    'target': round(sup_flat * 0.92, 2),
                    'key_level': round(sup_flat, 2),
                    'what_it_means': 'תמיכה אופקית קבועה + שיאים יורדים = לחץ מכירה מצטבר. שבירת התמיכה = צניחה.',
                })

    # ════════════════════════════════════════
    # 6. משולש מתכנס (Symmetrical Triangle)
    # ════════════════════════════════════════
    if n >= 15:
        recent_highs = [high[i] for i in highs_idx if i > n - 25]
        recent_lows  = [low[i]  for i in lows_idx  if i > n - 25]
        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            hi_falling = recent_highs[-1] < recent_highs[0]
            lo_rising  = recent_lows[-1]  > recent_lows[0]
            range_now  = recent_highs[-1] - recent_lows[-1]
            range_then = recent_highs[0]  - recent_lows[0]
            tightening = range_now < range_then * 0.7
            if hi_falling and lo_rising and tightening:
                apex = round((recent_highs[-1] + recent_lows[-1]) / 2, 2)
                patterns.append({
                    'name': 'משולש מתכנס (Symmetrical)',
                    'emoji': '🔺',
                    'signal': 'neutral',
                    'strength': 'בינוני',
                    'description': (
                        f"משולש מתכנס — שיאים יורדים ושפלים עולים. "
                        f"השוק בלחץ — פריצה בקרוב. אפקס סביב {apex}. "
                        "כיוון הפריצה יקבע את הטרייד."
                    ),
                    'target': None,
                    'key_level': apex,
                    'what_it_means': 'שיאים יורדים + שפלים עולים = השוק בלחץ. הפריצה בקרוב — לא ידוע לאיזה כיוון. חכה לאישור.',
                })

    # ════════════════════════════════════════
    # 7. דגל (Flag) — בוליש
    # ════════════════════════════════════════
    if n >= 15:
        pole_start = max(0, n - 20)
        pole_high  = max(high[pole_start:n-5])
        pole_low   = min(low[pole_start:n-10])
        pole_gain  = (pole_high - pole_low) / (pole_low + 0.001) * 100
        flag_range = max(high[-7:]) - min(low[-7:])
        flag_pct   = flag_range / (pole_high + 0.001) * 100
        flag_trending_down = close[-1] < close[-5]  # דגל יורד קצת

        if (pole_gain > 10 and          # עמוד חזק > 10%
                flag_pct < 5 and         # דגל צר
                flag_trending_down and   # דגל יורד
                current > pole_low * 1.05):
            target = round(current + (pole_high - pole_low) * 0.8, 2)
            patterns.append({
                'name': 'דגל בוליש (Bull Flag)',
                'emoji': '🚩',
                'signal': 'bullish',
                'strength': 'חזק',
                'description': (
                    f"דגל בוליש — עמוד עלייה {pole_gain:.1f}%, ואז תיקון צר {flag_pct:.1f}%. "
                    f"פריצה מעל {round(max(high[-7:]),2)} = כניסה. יעד: {target}."
                ),
                'target': target,
                'key_level': round(max(high[-7:]), 2),
                'what_it_means': 'עמוד עלייה חד + תיקון קטן צידי = הפסקה לפני המשך. הדגל תמיד "מוריד" — כניסה בפריצה מעל הדגל.',
            })

    # ════════════════════════════════════════
    # 8. כפל תחתית (Double Bottom) — בוליש
    # ════════════════════════════════════════
    if len(lows_idx) >= 2:
        l1_i, l2_i = lows_idx[-2], lows_idx[-1]
        if (l2_i > l1_i + 5 and                          # מספיק מרחק בין שפלים
                abs(low[l1_i] - low[l2_i]) / low[l1_i] < 0.04 and  # שפלים קרובים
                l2_i > n - 15):                           # שפל שני אחרון
            peak_between = max(high[l1_i:l2_i])
            target = round(peak_between + (peak_between - low[l2_i]), 2)
            patterns.append({
                'name': 'כפל תחתית (Double Bottom)',
                'emoji': 'W',
                'signal': 'bullish',
                'strength': 'חזק',
                'description': (
                    f"כפל תחתית — שני שפלים קרובים ב-{round(low[l1_i],2)} ו-{round(low[l2_i],2)}. "
                    f"פריצה מעל {round(peak_between,2)} = אות כניסה. יעד: {target}."
                ),
                'target': target,
                'key_level': round(peak_between, 2),
                'what_it_means': 'שני שפלים זהים = רמת תמיכה חזקה. השוק ניסה לרדת פעמיים ונכשל. פריצה כלפי מעלה = שינוי מגמה.',
            })

    # ════════════════════════════════════════
    # 9. כפל שיא (Double Top) — בריש
    # ════════════════════════════════════════
    if len(highs_idx) >= 2:
        h1_i, h2_i = highs_idx[-2], highs_idx[-1]
        if (h2_i > h1_i + 5 and
                abs(high[h1_i] - high[h2_i]) / high[h1_i] < 0.04 and
                h2_i > n - 15):
            trough_between = min(low[h1_i:h2_i])
            target = round(trough_between - (high[h2_i] - trough_between), 2)
            patterns.append({
                'name': 'כפל שיא (Double Top)',
                'emoji': 'M',
                'signal': 'bearish',
                'strength': 'חזק',
                'description': (
                    f"כפל שיא — שני שיאים קרובים ב-{round(high[h1_i],2)} ו-{round(high[h2_i],2)}. "
                    f"שבירה מתחת {round(trough_between,2)} = אות מכירה. יעד: {target}."
                ),
                'target': target,
                'key_level': round(trough_between, 2),
                'what_it_means': 'שני שיאים זהים = התנגדות חזקה. השוק ניסה לפרוץ פעמיים ונכשל. שבירה כלפי מטה = ירידה חזקה.',
            })

    # ════════════════════════════════════════
    # 10. תנועת V (V-Recovery) — ריקושט חד
    # ════════════════════════════════════════
    if n >= 10:
        low_10  = min(low[-10:])
        low_idx = list(low[-10:]).index(low_10)
        gain_since_low = (current - low_10) / (low_10 + 0.001) * 100
        if gain_since_low > 8 and low_idx < 7:   # עלייה >8% משפל האחרון
            patterns.append({
                'name': 'ריקושט V חד',
                'emoji': '⚡',
                'signal': 'bullish',
                'strength': 'בינוני',
                'description': (
                    f"ריקושט חד — +{gain_since_low:.1f}% מהשפל ב-{round(low_10,2)}. "
                    "תשאלו: האם זה ריקושט אמיתי עם ווליום, או dead-cat bounce?"
                ),
                'target': None,
                'key_level': round(low_10, 2),
                'what_it_means': 'ירידה חדה ואז עלייה חדה = V. יכול להיות ריקושט אמיתי עם ווליום, או dead-cat bounce. חובה לבדוק ווליום.',
            })

    # ════════════════════════════════════════
    # 11. קונסולידציה (בסיס) — Wide Base
    # ════════════════════════════════════════
    if n >= 20:
        rng_20 = (max(high[-20:]) - min(low[-20:])) / (min(low[-20:]) + 0.001) * 100
        vol_20_avg = sum(volume[-20:]) / 20
        vol_5_avg  = sum(volume[-5:])  / 5
        vol_declining = vol_5_avg < vol_20_avg * 0.8
        if rng_20 < 10 and vol_declining:
            patterns.append({
                'name': 'קונסולידציה / בסיס רחב',
                'emoji': '🏗️',
                'signal': 'neutral',
                'strength': 'בינוני',
                'description': (
                    f"בסיס רחב — תנודתיות {rng_20:.1f}% ב-20 יום עם ווליום יורד. "
                    "'The wider the base the higher to space.' "
                    "לחכות לפריצה עם ווליום."
                ),
                'target': round(max(high[-20:]) * 1.05, 2),
                'key_level': round(max(high[-20:]), 2),
                'what_it_means': 'מחיר תקוע בטווח צר עם ווליום יורד = לחץ מצטבר. ככל שהבסיס רחב יותר — הפריצה חזקה יותר.',
            })

    return patterns


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
    """
    זיהוי תבניות נרות יפניים — כולל תבניות חד-נרי, דו-נרי ותלת-נרי.
    תבניות לפי סדר חשיבות: חזקות יותר דורסות חלשות יותר.
    """
    if len(o) < 2:
        return {'patterns': [], 'signal': 'neutral', 'description': 'אין מספיק נתונים'}

    # נר נוכחי
    o1, h1, l1, c1 = float(o.iloc[-1]), float(h.iloc[-1]), float(l.iloc[-1]), float(c.iloc[-1])
    # נר קודם
    o2, h2, l2, c2 = float(o.iloc[-2]), float(h.iloc[-2]), float(l.iloc[-2]), float(c.iloc[-2])
    # לפני קודם (לתבניות תלת-נרי)
    has3 = len(o) >= 3
    if has3:
        o3, h3, l3, c3 = float(o.iloc[-3]), float(h.iloc[-3]), float(l.iloc[-3]), float(c.iloc[-3])

    range1 = h1 - l1 if h1 != l1 else 0.0001
    body1  = abs(c1 - o1)
    upper1 = h1 - max(o1, c1)
    lower1 = min(o1, c1) - l1
    body_pct1 = body1 / range1

    range2 = h2 - l2 if h2 != l2 else 0.0001
    body2  = abs(c2 - o2)

    bull1 = c1 > o1   # נר נוכחי ירוק
    bear1 = c1 < o1   # נר נוכחי אדום
    bull2 = c2 > o2   # נר קודם ירוק
    bear2 = c2 < o2   # נר קודם אדום

    patterns = []
    # רשימת סיגנלים לפי עוצמה (bull=+1, bear=-1, neutral=0)
    signals = []

    # ══════════════════════════════════════════
    # תבניות חד-נרי
    # ══════════════════════════════════════════

    # ── דוג'ים ──
    is_doji = body_pct1 < 0.08 and range1 > 0

    if is_doji:
        # דוג'י טאבלאית (Dragonfly) — פתיל תחתון ארוך, גוף בראש
        if lower1 > range1 * 0.6 and upper1 < range1 * 0.1:
            patterns.append("דוגי טאבלאית 🐉 — דחיית שפל, רמז בוליש")
            signals.append(1)
        # דוג'י מצבה (Gravestone) — פתיל עליון ארוך, גוף בתחתית
        elif upper1 > range1 * 0.6 and lower1 < range1 * 0.1:
            patterns.append("דוגי מצבה 🪦 — דחיית שיא, רמז בריש")
            signals.append(-1)
        # דוג'י רגיל
        else:
            patterns.append("דוג'י ⚖️ — חוסר החלטיות, מחכים לאישור")
            signals.append(0)

    # ── סביבון (Spinning Top) — גוף קטן, פתילים גדולים ──
    elif body_pct1 < 0.25 and upper1 > body1 * 0.5 and lower1 > body1 * 0.5:
        patterns.append("סביבון 🌀 — חוסר החלטיות, קונים ומוכרים מתמודדים")
        signals.append(0)

    # ── מרבוזו (Marubozu) — גוף מלא, כמעט ללא פתילים ──
    elif body_pct1 > 0.92:
        if bull1:
            patterns.append("מרבוזו בוליש 💪 — כוח קנייה מלא, ללא היסוס")
            signals.append(2)
        else:
            patterns.append("מרבוזו בריש 🔴 — כוח מכירה מלא, ללא היסוס")
            signals.append(-2)

    else:
        # ── פטיש (Hammer) — פתיל תחתון ארוך, גוף למעלה ──
        if lower1 >= 2 * body1 and upper1 <= body1 * 0.5 and range1 > 0:
            if bull1:
                patterns.append("פטיש ירוק 🔨 — דחיית שפל עם סגירה גבוהה, בוליש חזק")
                signals.append(2)
            else:
                patterns.append("פטיש אדום 🔨 — דחיית שפל, בוליש (גוף אדום פחות אידיאלי)")
                signals.append(1)

        # ── פטיש הפוך (Inverted Hammer) — פתיל עליון ארוך, גוף למטה ──
        elif upper1 >= 2 * body1 and lower1 <= body1 * 0.5 and bull1:
            patterns.append("פטיש הפוך 🔄 — ניסיון עלייה, צריך אישור ביום הבא")
            signals.append(1)

        # ── כוכב נופל (Shooting Star) — פתיל עליון ארוך + נר אדום ──
        elif upper1 >= 2 * body1 and lower1 <= body1 * 0.5 and bear1:
            patterns.append("כוכב נופל ⭐ — דחיית שיא, בריש")
            signals.append(-2)

        # ── איש תלוי (Hanging Man) — פטיש בסוף עלייה ──
        elif lower1 >= 2 * body1 and upper1 <= body1 * 0.5 and bear1:
            patterns.append("איש תלוי 🪝 — פטיש אדום בסוף עלייה, אזהרה בריש")
            signals.append(-1)

    # ══════════════════════════════════════════
    # תבניות דו-נרי
    # ══════════════════════════════════════════

    # ── בולען בוליש (Bullish Engulfing) ──
    if bull1 and bear2 and c1 >= o2 and o1 <= c2 and body1 > body2:
        patterns.append("בולען בוליש 🟢 — קונים בלעו את המוכרים לגמרי")
        signals.append(2)

    # ── בולען בריש (Bearish Engulfing) ──
    elif bear1 and bull2 and c1 <= o2 and o1 >= c2 and body1 > body2:
        patterns.append("בולען בריש 🔴 — מוכרים בלעו את הקונים לגמרי")
        signals.append(-2)

    # ── האראמי בוליש (Bullish Harami) — נר פנימי אחרי ירידה ──
    if bull1 and bear2 and c1 < o2 and o1 > c2 and body1 < body2 * 0.6:
        patterns.append("האראמי בוליש 🔵 — נר קטן בתוך נר גדול, עצירת ירידה")
        signals.append(1)

    # ── האראמי בריש (Bearish Harami) ──
    elif bear1 and bull2 and c1 > o2 and o1 < c2 and body1 < body2 * 0.6:
        patterns.append("האראמי בריש 🟠 — נר קטן בתוך נר גדול, עצירת עלייה")
        signals.append(-1)

    # ── נר פנימי (Inside Bar) — הנר נמצא בתוך הנר הקודם ──
    if h1 < h2 and l1 > l2 and not any("האראמי" in p for p in patterns):
        patterns.append("נר פנימי 📦 — התכווצות תנודתיות, לפני תנועה גדולה")
        signals.append(0)

    # ── נר חיצוני (Outside Bar) — הנר בולע את הנר הקודם ──
    if h1 > h2 and l1 < l2:
        if bull1:
            patterns.append("נר חיצוני ירוק 📈 — קונים שלטו בכל הטווח")
            signals.append(1)
        else:
            patterns.append("נר חיצוני אדום 📉 — מוכרים שלטו בכל הטווח")
            signals.append(-1)

    # ── פינצטה תחתית (Tweezer Bottom) — שני שפלים זהים ──
    if abs(l1 - l2) / (max(l1, l2) + 0.0001) < 0.003 and bear2 and bull1:
        patterns.append("פינצטה תחתית 🔧 — דחיית שפל כפולה, בוליש")
        signals.append(2)

    # ── פינצטה עליונה (Tweezer Top) — שני שיאים זהים ──
    if abs(h1 - h2) / (max(h1, h2) + 0.0001) < 0.003 and bull2 and bear1:
        patterns.append("פינצטה עליונה 🔧 — דחיית שיא כפולה, בריש")
        signals.append(-2)

    # ── קו חוצה (Piercing Line) — ירידה חדה ואחריה כיסוי >50% ──
    if bear2 and bull1 and o1 < l2 and c1 > (o2 + c2) / 2 and body2 > range2 * 0.5:
        patterns.append("קו חוצה 💉 — קונים חזרו בחוזקה, בוליש")
        signals.append(2)

    # ── כיסוי עננה כהה (Dark Cloud Cover) ──
    if bull2 and bear1 and o1 > h2 and c1 < (o2 + c2) / 2 and body2 > range2 * 0.5:
        patterns.append("עננה כהה ☁️ — מוכרים חזרו בחוזקה, בריש")
        signals.append(-2)

    # ══════════════════════════════════════════
    # תבניות תלת-נרי
    # ══════════════════════════════════════════

    if has3:
        bull3 = c3 > o3
        bear3 = c3 < o3
        body3 = abs(c3 - o3)

        # ── כוכב בוקר (Morning Star) — ירידה, סביבון/דוגי, עלייה ──
        if (bear3 and body3 > 0 and
                abs(c2 - o2) / (h2 - l2 + 0.0001) < 0.3 and
                bull1 and c1 > (o3 + c3) / 2):
            patterns.append("כוכב בוקר 🌅 — תבנית היפוך בוליש קלאסית (3 נרות)")
            signals.append(3)

        # ── כוכב ערב (Evening Star) — עלייה, סביבון/דוגי, ירידה ──
        elif (bull3 and body3 > 0 and
              abs(c2 - o2) / (h2 - l2 + 0.0001) < 0.3 and
              bear1 and c1 < (o3 + c3) / 2):
            patterns.append("כוכב ערב 🌆 — תבנית היפוך בריש קלאסית (3 נרות)")
            signals.append(-3)

        # ── שלושה חיילים לבנים (Three White Soldiers) ──
        if (bull1 and bull2 and bull3 and
                c1 > c2 > c3 and
                o1 > o2 > o3 and
                body1 > range1 * 0.5 and body2 > range2 * 0.5):
            patterns.append("3 חיילים לבנים 🪖🪖🪖 — עלייה חזקה ומסודרת, בוליש חזק")
            signals.append(3)

        # ── שלושה עורבים שחורים (Three Black Crows) ──
        elif (bear1 and bear2 and bear3 and
              c1 < c2 < c3 and
              o1 < o2 < o3 and
              body1 > range1 * 0.5 and body2 > range2 * 0.5):
            patterns.append("3 עורבים שחורים 🦅🦅🦅 — ירידה חזקה ומסודרת, בריש חזק")
            signals.append(-3)

        # ── קיקר בוליש (Bullish Kicker) — גפ בין נר אדום לנר ירוק ──
        if bear2 and bull1 and o1 >= c2:
            patterns.append("קיקר בוליש ⚡ — גפ חד מנר אדום לנר ירוק, שינוי כיוון מהיר")
            signals.append(3)

        # ── קיקר בריש (Bearish Kicker) ──
        elif bull2 and bear1 and o1 <= c2:
            patterns.append("קיקר בריש ⚡ — גפ חד מנר ירוק לנר אדום, שינוי כיוון מהיר")
            signals.append(-3)

    # ══════════════════════════════════════════
    # קביעת סיגנל סופי
    # ══════════════════════════════════════════

    if not patterns:
        patterns.append("נר רגיל — אין תבנית מיוחדת")
        signals.append(0)

    # ממוצע משוקלל לפי עוצמה
    total = sum(signals)
    if total >= 2:
        signal = 'bullish'
    elif total <= -2:
        signal = 'bearish'
    elif total > 0:
        signal = 'bullish'
    elif total < 0:
        signal = 'bearish'
    else:
        signal = 'neutral'

    desc = ' | '.join(patterns)
    return {
        'patterns': patterns,
        'signal': signal,
        'description': desc,
        'signal_strength': total,   # עוצמת הסיגנל המצטבר
    }


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
    """
    ניתוח בסגנון מיכה סטוק — לייב 20.
    עובר על הצ'קליסט לפי הסדר: טרנד → נר → ווליום → MA20 → גאפים → CCI
    ואז נותן סיכום + תוכנית מסחר.
    """
    lines = []
    consec     = trend.get('consecutive_days', 0)
    consec_dir = trend.get('consecutive_dir', 'none')
    dist       = ma20['distance_pct']
    cci_val    = cci['value']
    ma20_val   = ma20['ma20']

    # ══ פתיח ══
    monthly_ch = trend['monthly_change']
    weekly_ch  = trend['weekly_change']
    dir_he = 'ירוקים' if consec_dir == 'up' else 'אדומים' if consec_dir == 'down' else ''

    lines.append(
        f"בוא נעבור על הצ'קליסט של {ticker} ({current_price} {currency}). "
        f"חודש אחרון: {monthly_ch:+.1f}%, שבוע: {weekly_ch:+.1f}%."
    )

    # ══ שאלה 1: ימים רצופים ══
    if consec >= 7 and consec_dir == 'up':
        lines.append(
            f"📌 ימים רצופים — בואו נספור: {consec} ימים {dir_he} רצופים. "
            f"תשאלו את עצמכם: האם הגיוני {consec} ימים ירוקים? לא. "
            "זה לא הזמן להיכנס."
        )
    elif consec >= 5 and consec_dir == 'up':
        lines.append(
            f"📌 ימים רצופים — {consec} ימים {dir_he}. "
            "לפי כלל 5 הימים — צפו לשינוי כיוון בקרוב. לא מוחלט, אבל שמו לב."
        )
    elif consec >= 5 and consec_dir == 'down':
        lines.append(
            f"📌 ימים רצופים — {consec} ימים {dir_he}. "
            "המוכרים עייפים — מתקרבים לנקודה שמעניינת אותנו."
        )
    elif consec >= 3:
        lines.append(
            f"📌 ימים רצופים — {consec} ימים {dir_he}. עדיין לא קיצוני."
        )
    else:
        lines.append(f"📌 ימים רצופים — {consec} יום. ניטרלי.")

    # ══ שאלה 2: מרחק ממוצע 20 ══
    if dist > 12:
        lines.append(
            f"📌 ממוצע 20 — מרחק {dist:+.1f}%. "
            f"האם הגיוני מרחק כזה גדול מממוצע 20? לא. "
            f"הממוצע ב-{ma20_val} הוא המגנט — שם הוא הולך לחזור."
        )
    elif dist > 6:
        lines.append(
            f"📌 ממוצע 20 — מניה {dist:+.1f}% מעל הממוצע. "
            "מרחק שמתחיל להיות לא נוח. ממוצע 20 משמש כמגנט."
        )
    elif 0 < dist <= 6:
        lines.append(
            f"📌 ממוצע 20 — מניה {dist:+.1f}% מעל הממוצע עולה. סביבה בוליש, מרחק סביר."
        )
    elif -4 <= dist <= 0:
        lines.append(
            f"📌 ממוצע 20 — מניה {abs(dist):.1f}% מתחת לממוצע. "
            "האזור הכי לא נוח — קצת מתחת אבל לא מספיק לריקושט."
        )
    elif dist < -8:
        lines.append(
            f"📌 ממוצע 20 — מניה {abs(dist):.1f}% מתחת לממוצע. "
            f"מרחק גדול — הגומיה מתוחה. יעד: חזרה לממוצע ב-{ma20_val}."
        )
    else:
        lines.append(
            f"📌 ממוצע 20 — מניה {abs(dist):.1f}% מתחת לממוצע. "
            f"מתקרבת לאזור שמעניין אותנו."
        )

    # ══ נר (סוג + פתיחה/סגירה) ══
    candle_sig = candle['signal']
    candle_desc = candle['description']
    if candle_sig == 'bullish':
        lines.append(
            f"📌 נר — {candle_desc}. "
            "קונים נכנסו — זה מה שאנחנו רוצים לראות."
        )
    elif candle_sig == 'bearish':
        lines.append(
            f"📌 נר — {candle_desc}. "
            "המוכרים השתלטו. מדליק נורה אדומה."
        )
    else:
        lines.append(
            f"📌 נר — {candle_desc}. "
            "אין החלטה ברורה — מחכים לאישור."
        )

    # ══ ווליום ══
    vol_ratio = volume.get('ratio', 1)
    vol_sig   = volume['signal']
    if vol_sig == 'bullish':
        lines.append(
            f"📌 ווליום — {volume['description']}. "
            "כשהווליום מגיב ככה זה מה שאוהב לראות — קונים אמיתיים נכנסים."
        )
    elif vol_sig == 'bearish':
        lines.append(
            f"📌 ווליום — {volume['description']}. "
            "מוסדיים יוצאים — לא לרוץ להיכנס."
        )
    else:
        # בדוק עלייה עם ירידת ווליום (חולשה)
        if vol_ratio < 0.85:
            lines.append(
                f"📌 ווליום — עלייה עם ירידת ווליום ({vol_ratio:.1f}x ממוצע). "
                "זה לא מאשר את הכיוון. ממתינים לווליום אמיתי."
            )
        else:
            lines.append(
                f"📌 ווליום — {volume['description']}. אין confirmation חזק."
            )

    # ══ גאפים ══
    gaps_up  = gaps.get('gaps_up', [])
    gaps_dn  = gaps.get('gaps_down', [])
    if gaps_dn:
        lvls = ', '.join(str(g['level']) for g in gaps_dn[:2])
        lines.append(
            f"📌 גאפים — יש גפ פתוח מתחת ב-{lvls}. "
            "גפ משמש כמגנט — הוא מושך את המחיר כלפי מטה."
        )
    elif gaps_up:
        lvls = ', '.join(str(g['level']) for g in gaps_up[:2])
        lines.append(
            f"📌 גאפים — יש גפ פתוח למעלה ב-{lvls}. "
            "גפ משמש כמגנט כלפי מעלה."
        )
    else:
        lines.append("📌 גאפים — אין גפים פתוחים משמעותיים קרובים.")

    # ══ שאלה 3: CCI ══
    if cci_val > 150:
        lines.append(
            f"📌 CCI — עומד על {cci_val:.0f}. האם מצביע לקנות? לא. "
            "זה אוברבוט. CCI אומר לנו שהמומנטום חם מדי."
        )
    elif cci_val > 100:
        lines.append(
            f"📌 CCI — {cci_val:.0f}. מעל 100, מומנטום חיובי. "
            "עם טרנד עולה — מאשר."
        )
    elif 0 <= cci_val <= 100:
        lines.append(
            f"📌 CCI — {cci_val:.0f}. בטריטוריה חיובית, "
            "עוד 2-4 ימי עליות צפויים לפי הסטטיסטיקה."
        )
    elif -100 <= cci_val < 0:
        lines.append(
            f"📌 CCI — {cci_val:.0f}. שלילי, מומנטום חלש. "
            "ממתינים לחציית האפס כלפי מעלה."
        )
    elif cci_val < -100:
        lines.append(
            f"📌 CCI — {cci_val:.0f}. אוסלד קיצוני. "
            "האות שאנחנו מחכים לו — חציית -100 כלפי מעלה."
        )

    # ══ סיכום בסגנון מיכה ══
    lines.append("")  # שורה ריקה לפני הסיכום

    if rec_key == 'strong-buy':
        pct = 100
        lines.append(
            f"סיכום — {bullish}/6 בוליש. כל הצ'קליסט עובר. "
            f"מה הייתי עושה? נכנס ב-{pct}% מהפוזיציה, SL מתחת לממוצע 20 ({ma20_val}). "
            "ממשיכים לנטר."
        )
    elif rec_key == 'buy':
        pct = 50
        lines.append(
            f"סיכום — {bullish}/6 בוליש. תמונה טובה, לא מושלמת. "
            f"כניסה חלקית — {pct}%. "
            f"אם תאשר עם ווליום — להשלים. SL מתחת לממוצע 20 ({ma20_val})."
        )
    elif rec_key in ('sell', 'strong-sell'):
        pct = 0
        lines.append(
            f"סיכום — {bearish}/6 בריש. "
            "אנחנו לא נכנסים פה. אחוז כניסה: 0. "
            "ממשיכים לנטר — תמיד יש מניות."
        )
    else:
        pct = 25
        lines.append(
            f"סיכום — תמונה מעורבת: {bullish} בוליש, {bearish} בריש, {neutral} ניטרלי. "
            "אין פה כרגע טרייד ברור. "
            "תמיד יש מניות — ממשיכים הלאה."
        )

    return {
        'text': '\n'.join(lines),
        'position_pct': pct if rec_key in ('strong-buy', 'buy', 'neutral') else 0
    }


def generate_chart_analysis(df, ticker, info, current_price, trend, candle, volume, ma20, cci, bullish, bearish, gaps=None):
    """ניתוח גרף לפי שיטת מיכה סטוק — לייב 20"""

    # ── נר סגירה אחרון ──
    o1 = df['Open'].iloc[-1];  h1 = df['High'].iloc[-1]
    l1 = df['Low'].iloc[-1];   c1 = df['Close'].iloc[-1]
    v1 = df['Volume'].iloc[-1]
    range1   = h1 - l1 if h1 != l1 else 0.0001
    body1    = abs(c1 - o1)
    upper_w  = h1 - max(o1, c1)
    lower_w  = min(o1, c1) - l1
    avg_vol  = df['Volume'].iloc[-20:].mean() if len(df) >= 20 else df['Volume'].mean()
    vol_r    = round(v1 / avg_vol, 2) if avg_vol > 0 else 1

    prev_candle = {
        'open': round(o1, 2), 'high': round(h1, 2),
        'low':  round(l1, 2), 'close': round(c1, 2),
        'volume': int(v1), 'avg_volume': int(avg_vol), 'vol_ratio': vol_r,
        'body_pct':       round(body1  / range1 * 100, 1),
        'upper_wick_pct': round(upper_w / range1 * 100, 1),
        'lower_wick_pct': round(lower_w / range1 * 100, 1),
        'color':  'bullish' if c1 >= o1 else 'bearish',
        'type':   candle['patterns'][0] if candle['patterns'] else 'נר רגיל',
    }

    # ── ממוצעים ──
    ma50_s  = calc_ma50(df['Close'])
    ma20_v  = ma20['ma20']
    ma50_v  = round(ma50_s.iloc[-1], 2) if pd.notna(ma50_s.iloc[-1]) else None
    ma_table = []
    for lbl, val in [('MA20', ma20_v), ('MA50', ma50_v)]:
        if val is None:
            continue
        dist = round((current_price - val) / val * 100, 2)
        ma_table.append({'label': lbl, 'value': val,
                         'above': current_price > val, 'dist_pct': dist,
                         'signal': 'bullish' if current_price > val else 'bearish'})

    # ── RSI ──
    rsi_s   = calc_rsi(df['Close'])
    rsi_val = round(rsi_s.iloc[-1], 1) if pd.notna(rsi_s.iloc[-1]) else None
    if rsi_val is not None:
        if rsi_val < 30:   rsi_sig = 'bullish'; rsi_desc = f'RSI = {rsi_val} — אוסלד, מניה זולה יחסית'
        elif rsi_val > 70: rsi_sig = 'bearish'; rsi_desc = f'RSI = {rsi_val} — אוברבוט, זהירות מתיקון'
        else:              rsi_sig = 'neutral';  rsi_desc = f'RSI = {rsi_val} — טווח ניטרלי'
    else:
        rsi_sig = 'neutral'; rsi_desc = 'RSI לא זמין'

    # ── פריצה ──
    high_5d = df['High'].iloc[-5:].max()
    low_5d  = df['Low'].iloc[-5:].min()
    consolidation_pct = round((high_5d - low_5d) / current_price * 100, 1)

    swing_highs = []
    for i in range(2, len(df) - 2):
        if (df['High'].iloc[i] > df['High'].iloc[i-1] and
            df['High'].iloc[i] > df['High'].iloc[i-2] and
            df['High'].iloc[i] > df['High'].iloc[i+1] and
            df['High'].iloc[i] > df['High'].iloc[i+2]):
            swing_highs.append(df['High'].iloc[i])

    near_resistance = False;  resistance_level = None
    for sh in sorted(swing_highs, reverse=True):
        if sh > current_price and (sh - current_price) / current_price < 0.03:
            near_resistance = True;  resistance_level = round(sh, 2);  break

    vol_trend_up = df['Volume'].iloc[-3:].mean() > df['Volume'].iloc[-10:-3].mean()

    breakout_score = 0;  bo_signals = []
    if consolidation_pct < 5:
        breakout_score += 25;  bo_signals.append('קונסולידציה צרה — הצטברות')
    if vol_trend_up and volume['signal'] == 'bullish':
        breakout_score += 30;  bo_signals.append('ווליום עולה — לחץ קנייה')
    if near_resistance:
        breakout_score += 20;  bo_signals.append(f'קרוב להתנגדות {resistance_level} — פריצה אפשרית')
    if bullish >= 4:
        breakout_score += 25;  bo_signals.append('רוב האינדיקטורים בוליש')

    if breakout_score >= 70:   bo_label = 'סבירות גבוהה לפריצה';  bo_color = 'green'
    elif breakout_score >= 40: bo_label = 'פריצה אפשרית — עקוב';   bo_color = 'yellow'
    else:                      bo_label = 'אין סימני פריצה ברורים'; bo_color = 'red'

    # ── פונדמנטלי ──
    sector    = info.get('sector', 'לא ידוע')
    mcap      = info.get('marketCap', None)
    pe        = info.get('trailingPE', None)
    fpe       = info.get('forwardPE', None)
    eps       = info.get('trailingEps', None)
    w52h      = info.get('fiftyTwoWeekHigh', None)
    w52l      = info.get('fiftyTwoWeekLow', None)
    rating    = info.get('recommendationKey', None)

    if mcap:
        if mcap >= 1e12:   cap_s = f'{mcap/1e12:.1f}T'
        elif mcap >= 1e9:  cap_s = f'{mcap/1e9:.1f}B'
        else:              cap_s = f'{mcap/1e6:.0f}M'
    else: cap_s = None

    w52_pos = None
    if w52h and w52l and w52h != w52l:
        w52_pos = round((current_price - w52l) / (w52h - w52l) * 100, 1)

    fundamental = {
        'sector': sector, 'market_cap': cap_s,
        'pe':     round(pe, 1)  if pe  and pe  > 0 else None,
        'fpe':    round(fpe, 1) if fpe and fpe > 0 else None,
        'eps':    round(eps, 2) if eps else None,
        'week52_high': round(w52h, 2) if w52h else None,
        'week52_low':  round(w52l, 2) if w52l else None,
        'week52_pos':  w52_pos,
        'analyst_rating': rating,
    }

    # ══════════════════════════════════════════════════════════════════
    # ── מח מיכה סטוק — לייב 20 ──
    # שיטת שלוש השאלות:
    #  1. האם מספר ימי העלייה הרצופים הגיוני?
    #  2. האם המרחק לממוצע 20 הגיוני?
    #  3. האם ה-CCI מצביע לקנות?
    # ══════════════════════════════════════════════════════════════════

    ma20_dist   = float(ma20['distance_pct'])   # + = מעל, - = מתחת
    cci_val_raw = float(cci['value'])
    consec_days = trend.get('consecutive_days', 0)
    consec_dir  = trend.get('consecutive_dir', 'none')

    # ── גפים פתוחים כמגנטים ──
    gaps_down_levels = []   # גפים פתוחים מתחת למחיר = יעדי תיקון
    gaps_up_levels   = []   # גפים פתוחים מעל למחיר = יעדי עלייה
    if gaps:
        gaps_down_levels = [g['level'] for g in gaps.get('gaps_down', []) if g['level'] < current_price]
        gaps_up_levels   = [g['level'] for g in gaps.get('gaps_up', [])   if g['level'] > current_price]
    else:
        # מחשב ידנית
        lookback = min(30, len(df) - 1)
        for i in range(len(df) - lookback, len(df)):
            if i <= 0: continue
            prev_h = float(df['High'].iloc[i - 1])
            prev_l = float(df['Low'].iloc[i - 1])
            curr_o = float(df['Open'].iloc[i])
            if curr_o > prev_h * 1.002:
                if float(df['Low'].iloc[i:].min()) > prev_h:
                    if prev_h < current_price:
                        gaps_down_levels.append(round(prev_h, 2))
            elif curr_o < prev_l * 0.998:
                if float(df['High'].iloc[i:].max()) < prev_l:
                    if prev_l > current_price:
                        gaps_up_levels.append(round(prev_l, 2))

    # ── זיהוי: עלייה עם ירידת ווליום (סיגנל חולשה) ──
    rise_weak_vol = (
        c1 > o1 and
        len(df) >= 3 and
        float(df['Volume'].iloc[-1]) < float(df['Volume'].iloc[-2]) and
        float(df['Volume'].iloc[-2]) < float(df['Volume'].iloc[-3])
    )

    # ── הגדרת מצב לפי שיטת מיכה ──

    # מצב A: מורחק מעל ממוצע 20 + ימים ירוקים רבים = "לא נכנסים"
    is_extended_overbought = (
        (consec_days >= 7 and consec_dir == 'up') or
        (consec_days >= 5 and consec_dir == 'up' and ma20_dist > 10) or
        (cci_val_raw > 150 and rsi_val and rsi_val > 70 and ma20_dist > 8)
    )

    # מצב B: ריקושט — מניה ירדה ומתייצבת (כמו CRCL בלייב)
    # קריטריונים: RSI נמוך, CCI לא בקיצון חמור ועולה, ירידה מודרטה
    cci_series_full = calc_cci(df['High'], df['Low'], df['Close'])
    cci_prev  = float(cci_series_full.iloc[-2]) if len(df) > 2 else cci_val_raw
    cci_prev2 = float(cci_series_full.iloc[-3]) if len(df) > 3 else cci_prev
    cci_rising = cci_val_raw > cci_prev  # CCI עולה ביום האחרון
    cci_rising_2d = cci_val_raw > cci_prev2  # CCI עולה ב-2 ימים
    monthly_drop = trend.get('monthly_change', 0)
    is_recovery_setup = (
        not is_extended_overbought and
        rsi_val and rsi_val < 38 and
        ma20_dist < -4 and
        # CCI לא בקיצון חמור מדי — מתחת -150 חייב לעלות כבר
        (cci_val_raw > -150 or (cci_val_raw > -220 and cci_rising_2d)) and
        # לא ירידה חופשית עם מגמה ירדנית קיצונית (כמו PLTR -15%)
        not (monthly_drop < -12 and bearish >= 2 and not cci_rising_2d)
    )

    # מצב C: חזרה לממוצע 20 מלמעלה = "הנקודה המעניינת"
    is_near_ma20 = (
        not is_extended_overbought and
        -4 <= ma20_dist <= 6 and
        trend['signal'] == 'bullish'
    )

    # מצב D: פריצה אפשרית
    is_breakout_candidate = (
        not is_extended_overbought and
        not is_recovery_setup and
        breakout_score >= 50
    )

    # מצב E: מגמה יורדת ברורה
    monthly_drop_abs = abs(trend.get('monthly_change', 0))
    is_downtrend = (
        not is_recovery_setup and
        trend['signal'] == 'bearish' and
        (
            bearish >= 3 or
            (bearish >= 2 and monthly_drop_abs > 10) or   # ירידה חדה עם 2+ bearish
            (bearish >= 2 and ma20_dist < -10)            # מרחק גדול מממוצע 20 עם מגמה יורדת
        )
    )

    # ════════════════════════════════
    # ── דעת מומחה בסגנון מיכה ──
    # ════════════════════════════════
    lines = []

    if is_extended_overbought:
        # מיכה: "תספרו ימים, תבדקו מרחק ממוצע 20, תשאלו האם הגיוני"
        if consec_days >= 7 and consec_dir == 'up':
            lines.append(
                f"בואו נספור — {consec_days} ימים ירוקים רצופים ב-{ticker}. "
                f"תשאלו את עצמכם: האם הגיוני {consec_days} ימים ירוקים? "
                f"שנית — מרחק ממוצע 20: {ma20_dist:+.1f}%. האם הגיוני מרחק כזה גדול? "
                f"שלישית — CCI = {cci_val_raw:.0f}. האם מצביע לקנות? "
                f"כל התשובות — לא. לפי לייב 20, זה לא פעולה חכמה להיכנס פה."
            )
        elif ma20_dist > 10:
            lines.append(
                f"{ticker} נמצאת {ma20_dist:+.1f}% מעל ממוצע 20. "
                f"{consec_days} ימים ירוקים ו-CCI = {cci_val_raw:.0f}. "
                f"שלוש השאלות — לא, לא, לא. "
                f"להיכנס פה זה לרדוף אחרי הרכבת. אין פספוס — תמיד יש מניות."
            )
        else:
            lines.append(
                f"{ticker} — CCI = {cci_val_raw:.0f}, RSI = {rsi_val}, {ma20_dist:+.1f}% ממוצע 20. "
                f"שלושת המדדים בקיצון בוליש בו-זמנית. "
                f"זה לא הזמן לקנות — זה הזמן לחכות לתיקון."
            )

        # יעד לתיקון: גפ פתוח מתחת או ממוצע 20
        if gaps_down_levels:
            nearest_gap = max(gaps_down_levels)
            lines.append(
                f"יש גפ פתוח ב-{nearest_gap} — גפ משמש כמגנט. "
                f"אני מעריך תיקון לפחות לכיוון {nearest_gap}."
            )
        ma20_target = round(ma20_v, 2)
        lines.append(
            f"ממוצע 20 ב-{ma20_target} הוא המגנט הראשי. "
            f"שמו התראה שם ותחכו לנר שינוי כיוון — דוגי, פטיש, בולי שרמי — עם ווליום."
        )
        if rise_weak_vol:
            lines.append("עלייה תוך כדי ירידת ווליום — אין ביטחון אמיתי בעלייה הזו.")

        verdict = f"חכה לתיקון — {consec_days} ימי עלייה, {ma20_dist:+.1f}% ממוצע 20"
        vc = 'red'
        vd = (f"מניה מורחקת מממוצע 20. לפי לייב 20 — לא נכנסים כרגע. "
              f"חכה לתיקון לכיוון {ma20_target} ולנר שינוי כיוון.")

    elif is_recovery_setup:
        # מיכה: "מניה יורדת ומתייצבת — הזדמנות. The wider the base the higher to space."
        lines.append(
            f"{ticker} ירדה חזק — {ma20_dist:.1f}% מתחת לממוצע 20. "
            f"RSI = {rsi_val} — קרוב לאזור אוסלד. CCI = {cci_val_raw:.0f}."
        )
        if consolidation_pct < 8:
            lines.append(
                f"הבסיס מצטמצם — תנודתיות של {consolidation_pct}% ב-5 ימים אחרונים. "
                f"'The wider the base the higher to space' — "
                f"אני רושם אותה."
            )
        else:
            lines.append(f"עדיין לא ייצבה בסיס ברור — תנודתיות {consolidation_pct}% גבוהה. מחכים.")

        if cci_rising and cci_val_raw > -200:
            lines.append(f"CCI מתחיל לעלות — מתקרבים לחציית -100. זה האות שאנחנו מחכים לו.")

        if candle.get('signal') == 'bullish':
            lines.append(
                f"נר חיובי: {candle['description']}. "
                f"קונים מתחילים להיכנס. מעודד."
            )
        elif candle.get('signal') == 'bearish':
            lines.append("נר אדום — עוד לא סיימנו. ממשיכים לחכות לנר שינוי כיוון.")

        ma20_target = round(ma20_v, 2)
        if gaps_up_levels:
            first_target = min(gaps_up_levels)
            lines.append(
                f"יעד ראשון — ממוצע 20 ב-{ma20_target}. "
                f"יעד שני — גפ פתוח ב-{first_target}."
            )
        else:
            lines.append(f"יעד: ממוצע 20 ב-{ma20_target}. סטופ מתחת לשפל האחרון ({round(l1,2)}).")

        verdict = "מסתמן ריקושט — חכה לנר אישור"
        vc = 'yellow'
        vd = (f"מניה ירדה ומתייצבת. RSI = {rsi_val} — אוסלד. "
              f"חכה לנר שינוי כיוון עם ווליום לפני כניסה. יעד: MA20 ב-{ma20_target}.")

    elif is_near_ma20 and not is_extended_overbought:
        # מיכה: "פה זה מעניין — קרוב לממוצע 20"
        lines.append(
            f"{ticker} נמצאת {ma20_dist:+.1f}% מעל ממוצע 20. "
            f"זה האזור המעניין — מניות שחוזרות לבדוק את ממוצע 20 זה בדיוק מה שאנחנו מחפשים."
        )

        if candle.get('signal') == 'bullish':
            lines.append(
                f"נר חיובי ב-{candle['description']} — קונים מגיבים לממוצע 20. מעודד."
            )
        elif candle.get('signal') == 'bearish':
            lines.append(
                f"נר שלילי — לחץ מוכרים בממוצע 20. לא שבר עדיין, אבל שמים עין."
            )

        if volume.get('signal') == 'bullish' and vol_trend_up:
            lines.append("ווליום עולה — קונים מגיעים. אישור לכיוון.")
        elif rise_weak_vol:
            lines.append("עלייה עם ירידת ווליום — לא מאשר. ממתין לווליום.")

        if gaps_down_levels:
            nearest_gap = max(gaps_down_levels)
            lines.append(f"גפ פתוח ב-{nearest_gap} — תמיכה / מגנט אפשרי למטה.")

        if bullish >= 4:
            lines.append(f"{bullish}/6 אינדיקטורים בוליש — תמונה טובה.")
            verdict = "כניסה אפשרית — ליד ממוצע 20"
            vc = 'green'
            vd = (f"מניה חזרה לבדוק ממוצע 20 ({round(ma20_v,2)}). "
                  f"כניסה חלקית עם סטופ מתחת לממוצע ב-{round(ma20_v*0.98,2)}.")
        else:
            verdict = "עקוב — מתקרב לאזור כניסה"
            vc = 'yellow'
            vd = f"מניה בדרך לממוצע 20. חכה לנר חיובי עם ווליום לפני כניסה."

    elif is_breakout_candidate:
        # מיכה: "שימו התראה. אם פורצת עם ווליום — נכנסים. בלי ווליום — לא."
        lines.append(
            f"תסתכלו על הגרף של {ticker} — "
            f"קונסולידציה {consolidation_pct}% ב-5 ימים. "
        )
        if near_resistance:
            lines.append(
                f"קרובה להתנגדות {resistance_level}. "
                f"אם תפרוץ עם ווליום — נכנסים. "
                f"אם פורצת בלי ווליום — אפס. לא נכנסים."
            )
        if bo_signals:
            lines.append(f"סיגנלים: {' | '.join(bo_signals)}.")
        lines.append(
            f"מה אני הייתי עושה? שם התראה בפריצה. "
            f"רואים ווליום? נכנסים עם סטופ הדוק. לא רואים? ממשיכים הלאה."
        )

        verdict = "פריצה אפשרית — שים התראה"
        vc = 'yellow'
        vd = f"סטאפ פריצה מתבשל. פריצה עם ווליום = כניסה. בלי ווליום = לא."

    elif is_downtrend:
        # מיכה: "אין טרייד. ממתינים."
        lines.append(
            f"{ticker} במגמה יורדת — {trend['monthly_change']:+.1f}% בחודש. "
        )
        if cci_val_raw < -100:
            if cci_rising:
                lines.append(
                    f"CCI = {cci_val_raw:.0f} ומתחיל לעלות — מתקרבים לחציית -100. "
                    f"זה האות שאנחנו מחכים לו. עדיין לא הגיע."
                )
            else:
                lines.append(
                    f"CCI = {cci_val_raw:.0f} — אוסלד קיצוני, עדיין יורד. "
                    f"ממתינים לחציית -100 כלפי מעלה."
                )
        if gaps_up_levels:
            lines.append(f"יש גפ פתוח מעל ב-{min(gaps_up_levels)} — יעד לריקושט עתידי.")

        lines.append("כרגע אין טרייד. תמיד יש מניות — ממשיכים הלאה.")

        verdict = "אין כניסה — מגמה יורדת" if bearish >= 4 else "זהירות — מגמה שלילית"
        vc = 'red'
        vd = "מגמה יורדת ברורה. המתן לשינוי כיוון מאושר לפני כל כניסה."

    else:
        # מצב מעורב
        lines.append(
            f"{ticker} — תמונה לא חד-משמעית. "
            f"מרחק ממוצע 20: {ma20_dist:+.1f}%, CCI = {cci_val_raw:.0f}, RSI = {rsi_val if rsi_val else 'N/A'}."
        )
        if ma20_dist > 5:
            lines.append(f"מרחק {ma20_dist:+.1f}% ממוצע 20 — לא בנקודת כניסה מיטבית.")
        elif ma20_dist < -3:
            lines.append(f"מחיר {abs(ma20_dist):.1f}% מתחת לממוצע 20 — הממוצע הוא המגנט.")
        if rise_weak_vol:
            lines.append("עלייה עם ירידת ווליום — לא מאשר את הכיוון.")
        if gaps_down_levels:
            lines.append(f"גפ פתוח מתחת ב-{max(gaps_down_levels)} — מגנט לתיקון.")
        lines.append("אין פה כרגע טרייד ברור. ממשיכים לנטר.")

        verdict = "שקול — אין כיוון ברור"
        vc = 'yellow'
        vd = "תמונה מעורבת. המתן לסיגנל ברור יותר לפי שלוש השאלות."

    # ── פונדמנטלי (קצר) ──
    pe_v = pe if pe else None
    if pe_v and 0 < pe_v < 15:
        lines.append(f"פונדמנטלי: P/E={pe_v:.1f} — זול יחסית. סקטור: {sector}.")
    elif pe_v and pe_v > 50:
        lines.append(f"פונדמנטלי: P/E={pe_v:.1f} — ציפיות גדולות במחיר. סקטור: {sector}.")
    elif cap_s:
        lines.append(f"שווי שוק: {cap_s}. סקטור: {sector}.")

    # ── קביעת situation label ──
    if is_extended_overbought:
        situation = 'extended_overbought'
    elif is_recovery_setup:
        situation = 'recovery_setup'
    elif is_near_ma20:
        situation = 'near_ma20'
    elif is_breakout_candidate:
        situation = 'breakout_candidate'
    elif is_downtrend:
        situation = 'downtrend'
    else:
        situation = 'mixed'

    return {
        'prev_candle': {k: (bool(v) if isinstance(v, (bool, np.bool_)) else v) for k, v in prev_candle.items()},
        'ma_table': [{'label': m['label'], 'value': float(m['value']),
                      'above': bool(m['above']), 'dist_pct': float(m['dist_pct']),
                      'signal': m['signal']} for m in ma_table],
        'rsi': {'value': float(rsi_val) if rsi_val is not None else None, 'signal': rsi_sig, 'description': rsi_desc},
        'breakout': {
            'score': int(breakout_score), 'label': bo_label, 'color': bo_color,
            'signals': bo_signals, 'near_resistance': bool(near_resistance),
            'resistance_level': resistance_level, 'consolidation_pct': float(consolidation_pct),
        },
        'fundamental': fundamental,
        'expert_opinion': ' '.join(lines),
        'verdict': verdict, 'verdict_color': vc, 'verdict_detail': vd,
        'situation': situation,
        'ma20_dist': round(ma20_dist, 1),
        'consecutive_days': int(consec_days),
        'consecutive_dir': consec_dir,
        'gaps_down_targets': sorted(gaps_down_levels, reverse=True)[:3],
        'gaps_up_targets': sorted(gaps_up_levels)[:3],
        'rise_weak_vol': bool(rise_weak_vol),
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

    style  = request.args.get('style', 'swing')   # day / swing / position
    cached = cache_get(f'analyze_{ticker}_{style}', ttl=300)
    if cached:
        return jsonify(cached)

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

        # ── לונג / שורט לפי שיטת מיכה סטוקס ──
        if bearish >= 4:
            direction     = 'short'
            direction_label = 'שורט'
            direction_reason = (
                f'{bearish} מתוך 6 אינדיקטורים בריש · '
                f'טרנד {"יורד" if trend["signal"]=="bearish" else "ניטרלי"} · '
                f'נר {"בריש" if candle["signal"]=="bearish" else candle["signal"]} · '
                'מתאים לשורט לפי כלל הרוב הבריש'
            )
        elif bullish >= 4:
            direction     = 'long'
            direction_label = 'לונג'
            direction_reason = (
                f'{bullish} מתוך 6 אינדיקטורים בוליש · '
                f'טרנד {"עולה" if trend["signal"]=="bullish" else "ניטרלי"} · '
                f'נר {"בוליש" if candle["signal"]=="bullish" else candle["signal"]} · '
                'מתאים ללונג לפי כלל הרוב הבוליש'
            )
        elif trend['signal'] == 'bearish' and candle['signal'] == 'bearish':
            direction     = 'short'
            direction_label = 'שורט (חלש)'
            direction_reason = 'טרנד ונר שניהם בריש — נטייה לשורט, אך אין אישור מלא'
        elif trend['signal'] == 'bullish' and candle['signal'] == 'bullish':
            direction     = 'long'
            direction_label = 'לונג (חלש)'
            direction_reason = 'טרנד ונר שניהם בוליש — נטייה ללונג, אך אין אישור מלא'
        else:
            direction       = 'neutral'
            direction_label = 'ניטרלי — המתן'
            direction_reason = 'אין כיוון ברור, לא מומלץ ללונג ולא לשורט כרגע'

        current_price = round(df['Close'].iloc[-1], 2)
        prev_close    = round(df['Close'].iloc[-2], 2)
        change        = round(current_price - prev_close, 2)
        change_pct    = round(change / prev_close * 100, 2)

        # ATR (14)
        atr_series = calc_atr(df['High'], df['Low'], df['Close'])
        atr_val    = round(atr_series.iloc[-1], 2)
        atr_pct    = round(atr_val / current_price * 100, 2)

        # פיבונאצ'י — 6 חודשים, פיבוט אמיתי
        try:
            df_6mo = stock.history(period='6mo')
        except Exception:
            df_6mo = df
        fib = calc_fibonacci(df_6mo if not df_6mo.empty else df)
        # כמה % תיקנה המניה מהשיא (לפי מיקום בין high ל-low של הפיבו)
        if fib and fib['high'] != fib['low']:
            fib_retrace = round((fib['high'] - current_price) / (fib['high'] - fib['low']) * 100, 1)
        else:
            fib_retrace = 0

        # ── תוכנית מסחר (כניסה / יציאה / SL) ──
        entry_price = current_price
        last_low    = round(df['Low'].iloc[-1], 2)
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

        # ── תוכנית מסחר לפי סגנון ──
        if style == 'day':
            sl_price = round(current_price * 0.99, 2)       # SL 1% מתחת
            target1  = round(current_price * 1.02, 2)       # T1: 2%
            target2  = round(current_price * 1.04, 2)       # T2: 4%
            hold_desc = 'מסחר יומי — סגור הכל לפני סוף יום'
        elif style == 'position':
            sl_price = round(min(last_low, current_price * 0.92), 2)  # SL 8%
            target1  = round(current_price * 1.20, 2)       # T1: 20%
            target2  = round(current_price * 1.40, 2)       # T2: 40%
            hold_desc = 'פוזיציה ארוכה — החזקה חודש עד שנה'
        else:  # swing
            hold_desc = 'סווינג — החזקה 3–10 ימי מסחר'

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
            'style': style,
            'hold_desc': hold_desc,
        }

        # ── אזהרות פסיכולוגיות (לפי שיטת מיכה) ──
        psych_warnings = []
        # FOMO — עלייה חדה לפני כניסה
        if trend['weekly_change'] > 8:
            psych_warnings.append('⚠️ FOMO — המניה עלתה {:.0f}% השבוע. אל תרדוף אחרי הרכבת, המתן לתיקון.'.format(trend['weekly_change']))
        # כניסה מוקדמת מדי — אין אישור
        if bullish < 3 and bearish < 3:
            psych_warnings.append('⏳ מוקדם מדי — פחות מ-3 אינדיקטורים מאשרים כיוון. המתן לאישור לפני כניסה.')
        # 5 ימים באותו כיוון — צפה לשינוי
        if trend.get('five_day_warning'):
            dir_he = 'עלייה' if trend['consecutive_dir'] == 'up' else 'ירידה'
            psych_warnings.append(f'🔄 כלל 5 הימים — {trend["consecutive_days"]} ימי {dir_he} רצופים. צפה לשינוי כיוון בקרוב.')
        # R:R גרוע
        if rr is not None and rr < 1.5:
            psych_warnings.append(f'🚨 יחס סיכוי/סיכון נמוך ({rr}) — לפי מיכה, כניסה רק מעל 2:1. שנה את ה-SL או היעד.')
        # פחד מהפסד — CCI שלילי עם נר בוליש
        if cci.get('signal') == 'bearish' and candle.get('signal') == 'bullish':
            psych_warnings.append('🧠 סיגנל מנוגד — נר בוליש אבל CCI שלילי. ייתכן שזה מלכודת שורט. אל תמהר.')
        # נפח נמוך בפריצה
        if gaps.get('signal') == 'bullish' and volume.get('signal') != 'bullish':
            psych_warnings.append('📉 פריצה ללא נפח — פריצה בנפח נמוך היא לרוב מזויפת. המתן לאישור נפח.')

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
                title_he = translate_he(title) if title else title
                news_list.append({
                    'title': title_he,
                    'title_en': title,
                    'publisher': publisher,
                    'link': link,
                    'date': pub_date,
                    'is_mover': is_mover
                })
        except Exception:
            pass

        # ── דוחות רבעוניים מורחב — שיטת מיכה סטוק ──
        earnings_data   = []
        next_earnings   = None
        next_earnings_time = None
        eps_next_est    = None
        rev_next_high   = None
        rev_next_low    = None
        expected_move_pct = None

        # 1. quarterly_income_stmt — הכי אמין: revenue + EPS בפועל + net income
        try:
            qe = stock.quarterly_income_stmt
            if qe is not None and not qe.empty:
                for col in qe.columns[:8]:
                    date_key = str(col.date()) if hasattr(col, 'date') else str(col)[:10]
                    rev = qe.loc['Total Revenue', col] if 'Total Revenue' in qe.index else None
                    net = qe.loc['Net Income',    col] if 'Net Income'    in qe.index else None
                    eps_act = None
                    for eps_row in ('Basic EPS', 'Diluted EPS'):
                        if eps_row in qe.index:
                            v = qe.loc[eps_row, col]
                            if v is not None and pd.notna(v):
                                eps_act = round(float(v), 2)
                                break
                    # דלג על שורות ריקות לחלוטין
                    if rev is None and net is None and eps_act is None:
                        continue
                    earnings_data.append({
                        'date':         date_key,
                        'revenue':      int(rev) if rev is not None and pd.notna(rev) else None,
                        'net_income':   int(net) if net is not None and pd.notna(net) else None,
                        'eps_actual':   eps_act,
                        'eps_estimate': None,
                        'surprise_pct': None,
                        'beat':         None,
                    })
        except Exception:
            pass

        # 2. earnings_dates — EPS צפי vs בפועל + beat/miss (דורש lxml)
        try:
            ed_df = stock.earnings_dates
            if ed_df is not None and not ed_df.empty:
                now_utc = pd.Timestamp.now(tz='UTC')
                past_ed = ed_df[ed_df.index <= now_utc].head(8)
                for idx, row in past_ed.iterrows():
                    eps_est  = row.get('EPS Estimate', None)
                    eps_act  = row.get('Reported EPS', None)
                    surprise = row.get('Surprise(%)', None)
                    beat_eps = None
                    if (eps_est is not None and eps_act is not None
                            and pd.notna(eps_est) and pd.notna(eps_act)):
                        beat_eps = float(eps_act) >= float(eps_est)
                    date_key = str(idx.date())
                    # מחפש ערך קיים ב-earnings_data ומעשיר אותו
                    matched = False
                    for item in earnings_data:
                        try:
                            if abs((datetime.date.fromisoformat(item['date']) -
                                    datetime.date.fromisoformat(date_key)).days) < 50:
                                if eps_est is not None and pd.notna(eps_est):
                                    item['eps_estimate'] = round(float(eps_est), 2)
                                if eps_act is not None and pd.notna(eps_act):
                                    item['eps_actual'] = round(float(eps_act), 2)
                                if surprise is not None and pd.notna(surprise):
                                    item['surprise_pct'] = round(float(surprise), 1)
                                item['beat'] = beat_eps
                                matched = True
                                break
                        except Exception:
                            pass
                    if not matched:
                        earnings_data.append({
                            'date':         date_key,
                            'eps_estimate': round(float(eps_est), 2) if eps_est is not None and pd.notna(eps_est) else None,
                            'eps_actual':   round(float(eps_act), 2) if eps_act is not None and pd.notna(eps_act) else None,
                            'surprise_pct': round(float(surprise), 1) if surprise is not None and pd.notna(surprise) else None,
                            'beat':         beat_eps,
                            'revenue':      None,
                            'net_income':   None,
                        })
                # תאריך הדוח הבא
                future_ed = ed_df[ed_df.index > now_utc]
                if not future_ed.empty:
                    next_dt = future_ed.index[-1]
                    next_earnings = str(next_dt.date())
                    try:
                        next_earnings_time = 'לפני פתיחה' if next_dt.hour < 12 else 'אחרי סגירה'
                    except Exception:
                        pass
        except Exception:
            pass

        # 3. calendar — הדוח הבא + תחזיות (Earnings Average = EPS קונסנזוס)
        try:
            cal = stock.calendar
            if cal is not None:
                if isinstance(cal, dict):
                    ed = cal.get('Earnings Date', None)
                    if ed is not None and next_earnings is None:
                        if hasattr(ed, '__iter__') and not isinstance(ed, str):
                            ed = list(ed)
                            next_earnings = str(ed[0])[:10] if ed else None
                        else:
                            next_earnings = str(ed)[:10]
                    # EPS estimate — מנסה כמה שמות אפשריים
                    for k in ('Earnings Average', 'Earnings EPS', 'EPS Estimate', 'EPS Average'):
                        v = cal.get(k)
                        if v is not None:
                            try:
                                if pd.notna(v):
                                    eps_next_est = round(float(v), 2)
                                    break
                            except Exception:
                                pass
                    rh = cal.get('Revenue High')
                    rl = cal.get('Revenue Low')
                    if rh is not None:
                        try:
                            if pd.notna(rh): rev_next_high = int(rh)
                        except Exception:
                            pass
                    if rl is not None:
                        try:
                            if pd.notna(rl): rev_next_low = int(rl)
                        except Exception:
                            pass
                elif hasattr(cal, 'loc'):
                    try:
                        if next_earnings is None:
                            ed = cal.loc['Earnings Date']
                            next_earnings = str(ed.iloc[0])[:10] if hasattr(ed, 'iloc') else str(ed)[:10]
                    except Exception:
                        pass
                    try:
                        for k in ('Earnings Average', 'EPS Estimate'):
                            ep = cal.loc[k]
                            eps_next_est = round(float(ep.iloc[0] if hasattr(ep, 'iloc') else ep), 2)
                            break
                    except Exception:
                        pass
                    try:
                        rh = cal.loc['Revenue High']
                        rev_next_high = int(rh.iloc[0] if hasattr(rh, 'iloc') else rh)
                        rl2 = cal.loc['Revenue Low']
                        rev_next_low  = int(rl2.iloc[0] if hasattr(rl2, 'iloc') else rl2)
                    except Exception:
                        pass
        except Exception:
            pass

        # 4. תנועה צפויה — ממוצע הפתעות עבר + ATR
        try:
            surprises = [abs(e['surprise_pct']) for e in earnings_data
                         if e.get('surprise_pct') is not None and pd.notna(e['surprise_pct'])]
            if surprises:
                avg_surp = sum(surprises) / len(surprises)
                expected_move_pct = round(max(atr_pct * 2, avg_surp * 0.8), 1)
            else:
                expected_move_pct = round(atr_pct * 2.5, 1)
        except Exception:
            pass

        # 5. beat rate
        eps_beats = sum(1 for e in earnings_data if e.get('beat') is True)
        eps_total = sum(1 for e in earnings_data if e.get('beat') is not None)
        beat_rate = round(eps_beats / eps_total * 100) if eps_total > 0 else None

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

        # Chart data — calculate on full history, display last 60 trading days
        ma20_series = calc_ma20(df['Close'])
        ma50_series = calc_ma50(df['Close'])
        cci_series  = calc_cci(df['High'], df['Low'], df['Close'], period=14)
        rsi_series  = calc_rsi(df['Close'])

        # גרף + תבניות — שנה מלאה
        try:
            df_1y = stock.history(period='1y')
        except Exception:
            df_1y = df
        chart_df = df_1y if (not df_1y.empty and len(df_1y) > len(df)) else df

        chart_ma20 = calc_ma20(chart_df['Close'])
        chart_ma50 = calc_ma50(chart_df['Close'])
        chart_cci  = calc_cci(chart_df['High'], chart_df['Low'], chart_df['Close'])
        chart_rsi  = calc_rsi(chart_df['Close'])

        chart = {
            'dates':  [str(d.date()) for d in chart_df.index],
            'open':   chart_df['Open'].round(2).tolist(),
            'high':   chart_df['High'].round(2).tolist(),
            'low':    chart_df['Low'].round(2).tolist(),
            'close':  chart_df['Close'].round(2).tolist(),
            'volume': chart_df['Volume'].tolist(),
            'ma20':   [round(x, 2) if pd.notna(x) else None for x in chart_ma20],
            'ma50':   [round(x, 2) if pd.notna(x) else None for x in chart_ma50],
            'cci':    [round(x, 2) if pd.notna(x) else None for x in chart_cci],
            'rsi':    [round(x, 2) if pd.notna(x) else None for x in chart_rsi],
        }

        # ── תבניות גרף קלאסיות ──
        chart_patterns = detect_chart_patterns(chart_df)

        # ── ניתוח גרף מקצועי + פונדמנטלים ──
        chart_analysis = generate_chart_analysis(
            df, ticker, info, current_price, trend, candle, volume, ma20, cci, bullish, bearish, gaps=gaps
        )

        # Support/resistance levels (simple: recent swing highs/lows)
        highs  = df['High'].tolist()
        lows   = df['Low'].tolist()
        levels = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                levels.append({'type': 'resistance', 'price': round(highs[i], 2), 'date': str(df.index[i].date())})
            if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                levels.append({'type': 'support', 'price': round(lows[i], 2), 'date': str(df.index[i].date())})

        result = {
            'ticker': ticker,
            'company_name': company_name,
            'currency': currency,
            'current_price': current_price,
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
            'direction': direction,
            'direction_label': direction_label,
            'direction_reason': direction_reason,
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
            'next_earnings_time': next_earnings_time,
            'eps_next_est': eps_next_est,
            'rev_next_high': rev_next_high,
            'rev_next_low': rev_next_low,
            'expected_move_pct': expected_move_pct,
            'beat_rate': beat_rate,
            'short_pct': short_pct,
            'short_shares': short_shares,
            'short_ratio': short_ratio,
            'trade_plan': trade_plan,
            'diagnosis': diagnosis,
            'psych_warnings': psych_warnings,
            'style': style,
            'chart_analysis': chart_analysis,
            'chart_patterns': chart_patterns,
        }
        cache_set(f'analyze_{ticker}_{style}', result)
        return jsonify(result)

    except Exception as e:
        return jsonify({'error': f'שגיאה בניתוח: {str(e)}'}), 500


@app.route('/price')
def get_price():
    """Lightweight endpoint for live price updates (no heavy analysis)"""
    ticker = request.args.get('ticker', '').upper().strip()
    if not ticker:
        return jsonify({'error': 'no ticker'}), 400

    cached = cache_get(f'price_{ticker}', ttl=60)
    if cached:
        return jsonify(cached)

    try:
        stock = _ticker(ticker)
        df = stock.history(period='2d')
        if df.empty or len(df) < 2:
            return jsonify({'error': 'no data'}), 404
        current_price = round(df['Close'].iloc[-1], 2)
        prev_close    = round(df['Close'].iloc[-2], 2)
        change        = round(current_price - prev_close, 2)
        change_pct    = round(change / prev_close * 100, 2)
        currency = stock.info.get('currency', 'USD')
        result = {
            'current_price': current_price,
            'change': change,
            'change_pct': change_pct,
            'currency': currency,
        }
        cache_set(f'price_{ticker}', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/drivers')
def market_drivers():
    """מזהה מה מניע את השוק עכשיו"""
    cached = cache_get('market_drivers', ttl=300)
    if cached:
        return jsonify(cached)
    try:
        result = {'drivers': get_market_drivers()}
        cache_set('market_drivers', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/market')
def market_overview():
    """נתוני שוק רחבים — VIX, Fear & Greed, DXY, ריבית, סקטורים, אירועים"""
    cached = cache_get('market_overview', ttl=300)
    if cached:
        return jsonify(cached)
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

        result = {
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
        }
        cache_set('market_overview', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/macro')
def macro_alerts():
    """המלצות סקטור לפי מצב מאקרו — חדשות עולם + VIX + F&G"""
    cached = cache_get('macro_alerts', ttl=300)
    if cached:
        return jsonify(cached)
    try:
        vix  = get_vix()
        fg   = get_fear_greed()
        dxy  = get_dxy()
        t10  = get_us10y()

        alerts = []
        hot_sectors = []

        vix_val = vix['value'] if vix else 20
        fg_score = fg['score'] if fg else 50
        dxy_chg  = dxy['change_pct'] if dxy else 0
        t10_val  = t10['value'] if t10 else 4.0

        # VIX גבוה — שוק בפחד, הזדמנויות קנייה
        if vix_val > 30:
            alerts.append({'type': 'danger', 'text': f'VIX = {vix_val:.1f} — פחד קיצוני בשוק. לפי מיכה: זה הזמן לחפש מניות חזקות שנמכרו יחד עם השוק — הזדמנות.'})
            hot_sectors.append({'sector': 'מניות ערך / דיבידנד', 'reason': 'VIX גבוה = מוכרים כל מה שיש. מניות דיבידנד יציבות נמכרות זול.', 'tickers': 'JNJ, KO, PG, VZ'})
        elif vix_val < 15:
            alerts.append({'type': 'info', 'text': f'VIX = {vix_val:.1f} — שוק רגוע, ביטחון גבוה. הזהר מהתרדמות — לא הזמן לקחת סיכונים גדולים.'})

        # פחד קיצוני — F&G מתחת ל-20
        if fg_score < 20:
            alerts.append({'type': 'opportunity', 'text': f'Fear & Greed = {fg_score:.0f} (פחד קיצוני) — היסטורית, כאשר כולם מפחדים זה הזמן לקנות חזק.'})
            hot_sectors.append({'sector': 'טכנולוגיה / נאסד"ק', 'reason': 'פחד קיצוני מייצר הזדמנות ב-QQQ, NVDA, MSFT שנמכרו יתר על המידה.', 'tickers': 'QQQ, NVDA, MSFT, AAPL'})
        elif fg_score > 80:
            alerts.append({'type': 'warning', 'text': f'Fear & Greed = {fg_score:.0f} (חמדנות קיצונית) — כולם קונים. לפי מיכה: זה הזמן לממש רווחים, לא להיכנס.'})

        # דולר חזק — לחץ על חומרי גלם
        if dxy_chg > 0.5:
            alerts.append({'type': 'warning', 'text': f'דולר חזק (+{dxy_chg:.1f}%) — לחץ על נפט, זהב, וחברות יצוא. סיכון למניות מולטי-נשיונל.'})
            hot_sectors.append({'sector': 'בנקים / פיננסים', 'reason': 'דולר חזק = בנקים מרוויחים יותר על ריבית. XLF מועדף.', 'tickers': 'XLF, JPM, GS, BAC'})
        elif dxy_chg < -0.5:
            alerts.append({'type': 'opportunity', 'text': f'דולר חלש ({dxy_chg:.1f}%) — חיובי לנפט, זהב, וחברות עם הכנסות בינלאומיות.'})
            hot_sectors.append({'sector': 'אנרגיה / זהב', 'reason': 'דולר חלש = נפט וזהב עולים. XLE, GLD מועדפים.', 'tickers': 'XLE, GLD, XOM, CVX'})

        # ריבית גבוהה
        if t10_val > 4.5:
            alerts.append({'type': 'warning', 'text': f'ריבית 10 שנה = {t10_val:.2f}% — גבוהה. לחץ על מניות צמיחה (טק). בנקים נהנים.'})
            hot_sectors.append({'sector': 'בנקים / ביטוח', 'reason': f'ריבית {t10_val:.2f}% — בנקים מרוויחים יותר על הלוואות. KRE, XLF.', 'tickers': 'KRE, XLF, JPM, BRK-B'})

        # המלצות גיאופוליטיות קבועות (לפי מגמות עולמיות)
        macro_themes = [
            {
                'theme': '🛢️ מתח במזרח התיכון / נפט',
                'explanation': 'כל מתח עם איראן, סעודיה, או עיראק מעלה נפט. קנה לפני כולם.',
                'tickers': 'XLE, USO, XOM, CVX, OXY',
                'trigger': 'חדשות על מתח בפרסי, פיגועים, סנקציות על נפט'
            },
            {
                'theme': '🔫 בטחון / ביטחוני',
                'explanation': 'מלחמה או מתח גיאופוליטי = תקציבי ביטחון עולים בכל העולם.',
                'tickers': 'LMT, RTX, NOC, GD, PLTR',
                'trigger': 'מלחמה, צבא, נאט"ו, הגנה'
            },
            {
                'theme': '🤖 AI / בינה מלאכותית',
                'explanation': 'כל חדשה חיובית על AI מעלה את NVDA ואת כל המגזר.',
                'tickers': 'NVDA, MSFT, GOOGL, META, AMD',
                'trigger': 'ChatGPT, Gemini, AI חדש, שיתוף פעולה טכנולוגי'
            },
            {
                'theme': '💊 פארמה / בריאות',
                'explanation': 'אישור FDA, תוצאות ניסוי קליני — מניות פארמה קטנות יכולות להכפיל.',
                'tickers': 'XLV, UNH, LLY, PFE, MRNA',
                'trigger': 'FDA, ניסוי קליני, תרופה חדשה, מגיפה'
            },
            {
                'theme': '⚡ אנרגיה ירוקה',
                'explanation': 'מדיניות ממשלתית על אנרגיה מתחדשת = הזדמנות ב-solar/EV.',
                'tickers': 'ICLN, ENPH, FSLR, NEE, TSLA',
                'trigger': 'הסכם אקלים, כלי רכב חשמלי, מענקי אנרגיה'
            },
        ]

        result = {
            'alerts': alerts,
            'hot_sectors': hot_sectors,
            'macro_themes': macro_themes,
        }
        cache_set('macro_alerts', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/ask')
def ask_assistant():
    """עוזר חכם — שאל שאלה על מניה או שוק"""
    q = request.args.get('q', '').strip()
    ticker = request.args.get('ticker', '').upper().strip()
    if not q:
        return jsonify({'answer': 'שאל אותי שאלה על מניה או שוק...'})

    q_lower = q.lower()
    answer = ''

    # קבל נתוני ניתוח אם יש טיקר
    analysis = cache_get(f'analyze_{ticker}_swing') if ticker else None

    # תשובות חכמות לפי שאלה
    if any(w in q_lower for w in ['כניסה', 'לקנות', 'לקנות', 'לקנות עכשיו', 'לכנס']):
        if analysis:
            bullish = analysis.get('bullish_count', 0)
            bearish = analysis.get('bearish_count', 0)
            rec = analysis.get('recommendation', '')
            direction = analysis.get('direction', 'neutral')
            if bullish >= 4 and direction == 'long':
                answer = f'לפי הניתוח של {ticker}: {bullish}/6 אינדיקטורים בוליש — כן, זה זמן כניסה סביר. {rec}. אבל המתן לנר אישור ובדוק נפח.'
            elif bearish >= 4:
                answer = f'לפי הניתוח של {ticker}: {bearish}/6 אינדיקטורים בריש — לא מומלץ לקנות עכשיו. {rec}.'
            else:
                answer = f'לפי {ticker}: תמונה מעורבת ({bullish} בוליש, {bearish} בריש). לפי מיכה — אם אתה לא בטוח 100%, אל תיכנס. המתן לאישור.'
        else:
            answer = 'חפש קודם מניה ואז שאל אותי. לפי מיכה סטוק: כניסה רק כשרוב האינדיקטורים מכוונים לאותו כיוון.'

    elif any(w in q_lower for w in ['שורט', 'short', 'ירידה', 'למכור']):
        if analysis:
            bearish = analysis.get('bearish_count', 0)
            direction = analysis.get('direction', 'neutral')
            if bearish >= 4 or direction == 'short':
                answer = f'{ticker}: {bearish}/6 אינדיקטורים בריש. מתאים לשורט. SL מעל הגבוה האחרון. יעד: ממוצע 20.'
            else:
                answer = f'{ticker}: אין מספיק אישורים לשורט. {analysis.get("bearish_count",0)}/6 בריש — לפי מיכה, צריך לפחות 4.'
        else:
            answer = 'לפי מיכה: שורט טוב צריך: טרנד יורד + נר בריש + נפח עולה + CCI שלילי. ודא שיש לפחות 4/6 אינדיקטורים.'

    elif any(w in q_lower for w in ['sl', 'סטופ', 'stop', 'stop loss', 'סל']):
        if analysis:
            tp = analysis.get('trade_plan', {})
            answer = f'לפי הניתוח: SL מומלץ ב-{tp.get("sl")} ({analysis.get("currency","USD")}). זה שפל הנר האחרון. אל תזיז SL כלפי מטה — זו טעות קלאסית.'
        else:
            answer = 'לפי מיכה: SL תמיד מתחת לשפל נר הכניסה. לעולם אל תזיז SL כלפי מטה. אם ה-SL נפגע — צא, ובדוק מחדש.'

    elif any(w in q_lower for w in ['יעד', 'target', 'מטרה', 'רווח']):
        if analysis:
            tp = analysis.get('trade_plan', {})
            rr = tp.get('rr')
            answer = f'יעד 1: {tp.get("target1")} | יעד 2: {tp.get("target2")} | יחס סיכוי/סיכון: {rr}. '
            if rr and rr >= 2:
                answer += 'יחס טוב! לפי מיכה — מכור חצי ביעד 1, תן לשאר לרוץ.'
            else:
                answer += 'יחס נמוך. לפי מיכה — כניסה רק כשיחס מעל 2:1. שנה את נקודת הכניסה או ה-SL.'
        else:
            answer = 'לפי מיכה: יעד 1 = ממוצע 20 (אפקט גומיה). יעד 2 = התנגדות קרובה. מכור חצי ביעד 1 ותן לשאר לרוץ.'

    elif any(w in q_lower for w in ['fomo', 'פומו', 'פחד', 'מפחד', 'חרדה']):
        answer = 'לפי מיכה: FOMO הורג חשבונות. אם אתה נכנס כי אתה "מפחד להפסיד" — כבר טעית. הרכבת תמיד יוצאת שוב. המתן לסטיפ-אפ הבא.'

    elif any(w in q_lower for w in ['vix', 'ויקס', 'פחד שוק']):
        try:
            vix = get_vix()
            val = vix['value'] if vix else 'N/A'
            level = vix['level'] if vix else ''
            if val != 'N/A':
                if val > 30:
                    answer = f'VIX עכשיו: {val:.1f} ({level}). פחד קיצוני! לפי מיכה — זה הזמן לחפש הזדמנויות, לא לברוח. המשקיע הטוב קונה כשאחרים מפחדים.'
                elif val > 20:
                    answer = f'VIX עכשיו: {val:.1f} ({level}). אי-ודאות בינונית. סחר בזהירות, קח פוזיציות קטנות יותר.'
                else:
                    answer = f'VIX עכשיו: {val:.1f} ({level}). שוק רגוע. זהר מהתרדמות — לפעמים השקט הכי מסוכן.'
        except Exception:
            answer = 'לא הצלחתי לטעון VIX כרגע.'

    elif any(w in q_lower for w in ['שיטה', 'מיכה', 'ליב 20', 'כלל']):
        answer = ('שיטת מיכה סטוקס (ליב 20):\n'
                  '1. זהה טרנד — בוליש או בריש\n'
                  '2. אשר עם נר יפני + נפח\n'
                  '3. בדוק MA20 — הגומייה\n'
                  '4. כניסה רק כש-4/6 אינדיקטורים באותו כיוון\n'
                  '5. SL תמיד מתחת לשפל הנר\n'
                  '6. יחס סיכוי/סיכון לפחות 2:1\n'
                  '7. 5 ימים רצופים = צפה לשינוי')

    else:
        answer = ('אני עוזר המסחר שלך לפי שיטת מיכה סטוקס. תוכל לשאול:\n'
                  '• "האם לקנות את [מניה]?"\n'
                  '• "מה ה-SL המומלץ?"\n'
                  '• "מה היעדים?"\n'
                  '• "מה ה-VIX עכשיו?"\n'
                  '• "מה שיטת מיכה?"\n'
                  '• "יש לי FOMO, מה לעשות?"')

    return jsonify({'answer': answer, 'ticker': ticker})


@app.route('/world-news')
def world_news():
    """חדשות מאקרו עולמיות + מי מרוויח מכל אירוע"""
    cached = cache_get('world_news', ttl=900)
    if cached:
        return jsonify(cached)
    try:
        # אירועים מאקרו עם מנצחים ומפסידים
        MACRO_EVENTS = [
            {
                'topic': 'מלחמת סחר / מכסים',
                'keywords': ['tariff','trade war','trade deal','customs','import duty','export ban','sanctions','מכס','סנקציות'],
                'winners': [
                    {'ticker': 'LMT', 'name': 'Lockheed Martin', 'reason': 'ביטחון + תעשייה מקומית'},
                    {'ticker': 'RTX',  'name': 'Raytheon',        'reason': 'תעשיית הגנה אמריקאית'},
                    {'ticker': 'CAT',  'name': 'Caterpillar',      'reason': 'ייצור מקומי מוגן ממכסים'},
                    {'ticker': 'DE',   'name': 'John Deere',       'reason': 'מכונות חקלאות אמריקאיות'},
                    {'ticker': 'NEM',  'name': 'Newmont Mining',   'reason': 'זהב עולה בעת אי-ודאות'},
                ],
                'losers': [
                    {'ticker': 'AAPL', 'name': 'Apple',    'reason': 'שרשרת אספקה בסין'},
                    {'ticker': 'NVDA', 'name': 'NVIDIA',   'reason': 'הגבלות יצוא שבבים'},
                    {'ticker': 'WMT',  'name': 'Walmart',  'reason': 'מוצרים מיובאים מסין'},
                    {'ticker': 'NKE',  'name': 'Nike',     'reason': 'ייצור בדרום-מזרח אסיה'},
                ],
                'tip': 'כשיש מכסים — קנה תעשייה מקומית, מכור יצואניות גלובליות',
            },
            {
                'topic': 'עלייה בריבית / פד אגרסיבי',
                'keywords': ['interest rate','fed hike','hawkish','rate rise','fomc','jerome powell','ריבית','פד','העלאת ריבית'],
                'winners': [
                    {'ticker': 'JPM',  'name': 'JP Morgan',       'reason': 'בנקים מרוויחים מריבית גבוהה'},
                    {'ticker': 'BAC',  'name': 'Bank of America',  'reason': 'מרווח ריבית גדל'},
                    {'ticker': 'GS',   'name': 'Goldman Sachs',    'reason': 'בנק השקעות, הכנסות ריבית'},
                    {'ticker': 'BRK-B','name': 'Berkshire',        'reason': 'מזומן עצום מניב ריבית'},
                    {'ticker': 'UNH',  'name': 'UnitedHealth',     'reason': 'שירותי בריאות — לא תלויים בריבית'},
                ],
                'losers': [
                    {'ticker': 'TSLA', 'name': 'Tesla',        'reason': 'מכירות מימון רכב מתייקרות'},
                    {'ticker': 'AMZN', 'name': 'Amazon',       'reason': 'צמיחה נחתכת — מכפילים יורדים'},
                    {'ticker': 'ARKK', 'name': 'ARK Innovation','reason': 'טכנולוגיה ספקולטיבית נפגעת'},
                    {'ticker': 'IYR',  'name': 'Real Estate ETF','reason': 'נדל"ן תלוי ריבית'},
                ],
                'tip': 'ריבית עולה = הטה לבנקים ובריאות, הימנע מצמיחה ונדל"ן',
            },
            {
                'topic': 'מלחמה / קונפליקט גיאופוליטי',
                'keywords': ['war','conflict','military','attack','invasion','geopolitical','nato','מלחמה','קונפליקט','התקפה','צבאי'],
                'winners': [
                    {'ticker': 'LMT', 'name': 'Lockheed Martin', 'reason': 'נשק וביטחון'},
                    {'ticker': 'NOC', 'name': 'Northrop Grumman','reason': 'מערכות הגנה'},
                    {'ticker': 'GD',  'name': 'General Dynamics', 'reason': 'ספינות וכלי רכב צבאיים'},
                    {'ticker': 'XOM', 'name': 'Exxon Mobil',      'reason': 'נפט עולה בקונפליקטים'},
                    {'ticker': 'GLD', 'name': 'Gold ETF',          'reason': 'זהב — מקלט בטוח'},
                ],
                'losers': [
                    {'ticker': 'DAL', 'name': 'Delta Air Lines', 'reason': 'תעופה נפגעת מקונפליקטים'},
                    {'ticker': 'CCL', 'name': 'Carnival Cruise',  'reason': 'תיירות יורדת'},
                    {'ticker': 'BABA','name': 'Alibaba',          'reason': 'ריסק סין עולה'},
                ],
                'tip': 'קונפליקט = קנה ביטחון ואנרגיה, מכור תיירות ותחבורה',
            },
            {
                'topic': 'ירידת ריבית / פד יוני',
                'keywords': ['rate cut','dovish','pivot','fed cut','lower rates','הורדת ריבית','פיבוט','יוני'],
                'winners': [
                    {'ticker': 'TSLA', 'name': 'Tesla',     'reason': 'ריבית נמוכה = מימון זול לרכב חשמלי'},
                    {'ticker': 'AMZN', 'name': 'Amazon',    'reason': 'מכפילים עולים בריבית נמוכה'},
                    {'ticker': 'NVDA', 'name': 'NVIDIA',    'reason': 'טכנולוגיה צומחת נהנית'},
                    {'ticker': 'IYR',  'name': 'Real Estate ETF','reason': 'נדל"ן עולה'},
                    {'ticker': 'ARKK', 'name': 'ARK Innovation','reason': 'ספקולציה חוזרת'},
                ],
                'losers': [
                    {'ticker': 'JPM', 'name': 'JP Morgan',  'reason': 'מרווח ריבית נצמצם'},
                    {'ticker': 'BAC', 'name': 'Bank of America','reason': 'הכנסות ריבית יורדות'},
                ],
                'tip': 'ריבית יורדת = קנה טכנולוגיה ונדל"ן, הקטן בנקים',
            },
            {
                'topic': 'משבר בנקאי / פשיטת רגל',
                'keywords': ['bank crisis','bank failure','bankruptcy','credit crunch','collapse','contagion','משבר','פשיטת רגל','קריסה'],
                'winners': [
                    {'ticker': 'GLD', 'name': 'Gold ETF',    'reason': 'מקלט בטוח קלאסי'},
                    {'ticker': 'TLT', 'name': 'Bonds ETF',   'reason': 'אג"ח ארוך עולה בפחד'},
                    {'ticker': 'V',   'name': 'Visa',         'reason': 'תשלומים — לא נפגע ממשבר בנקאי'},
                    {'ticker': 'UNH', 'name': 'UnitedHealth', 'reason': 'דפנסיבי — אנשים עדיין צריכים ביטוח'},
                ],
                'losers': [
                    {'ticker': 'SIVB','name': 'SVB type',    'reason': 'בנקים קטנים/אזוריים נפגעים'},
                    {'ticker': 'KRE', 'name': 'Regional Banks ETF','reason': 'חשיפה לבנקים אזוריים'},
                ],
                'tip': 'משבר בנקאי = ברח לזהב ואג"ח, הימנע מבנקים אזוריים',
            },
            {
                'topic': 'מחיר נפט גבוה',
                'keywords': ['oil price','crude oil','opec','brent','wti','energy crisis','נפט','אנרגיה','אופ"ק'],
                'winners': [
                    {'ticker': 'XOM',  'name': 'Exxon',      'reason': 'חברת נפט — מרוויחה ישירות'},
                    {'ticker': 'CVX',  'name': 'Chevron',     'reason': 'חברת נפט גדולה'},
                    {'ticker': 'OXY',  'name': 'Occidental',  'reason': 'ייצור נפט אמריקאי'},
                    {'ticker': 'HAL',  'name': 'Halliburton', 'reason': 'שירותי קידוח'},
                    {'ticker': 'XLE',  'name': 'Energy ETF',  'reason': 'אנרגיה כולה'},
                ],
                'losers': [
                    {'ticker': 'DAL', 'name': 'Delta',   'reason': 'עלויות דלק עולות'},
                    {'ticker': 'UPS', 'name': 'UPS',     'reason': 'לוגיסטיקה תלויה בדלק'},
                    {'ticker': 'AMZN','name': 'Amazon',  'reason': 'משלוחים מתייקרים'},
                ],
                'tip': 'נפט עולה = קנה XLE, מכור תעופה ולוגיסטיקה',
            },
            {
                'topic': 'בינה מלאכותית / AI boom',
                'keywords': ['artificial intelligence','ai','machine learning','chatgpt','generative ai','large language','gpu','ai chip','בינה מלאכותית','AI'],
                'winners': [
                    {'ticker': 'NVDA', 'name': 'NVIDIA',   'reason': 'GPU לאימון AI — מלך השוק'},
                    {'ticker': 'MSFT', 'name': 'Microsoft','reason': 'Copilot + Azure AI'},
                    {'ticker': 'GOOG', 'name': 'Google',   'reason': 'Gemini + אינפרה ענן'},
                    {'ticker': 'AMD',  'name': 'AMD',      'reason': 'תחרות עם NVIDIA בשבבי AI'},
                    {'ticker': 'SMCI', 'name': 'Super Micro','reason': 'שרתי AI'},
                ],
                'losers': [
                    {'ticker': 'IBM',  'name': 'IBM',      'reason': 'מחשוב ישן נדחק'},
                    {'ticker': 'ACN',  'name': 'Accenture','reason': 'ייעוץ IT מוחלף ע"י AI'},
                ],
                'tip': 'AI boom = NVDA + MSFT + ענן, הימנע מ-IT ישן',
            },
            {
                'topic': 'אינפלציה גבוהה',
                'keywords': ['inflation','cpi','pce','price index','cost of living','אינפלציה','יוקר מחיה','מדד מחירים'],
                'winners': [
                    {'ticker': 'GLD',  'name': 'Gold ETF',       'reason': 'גידור קלאסי מול אינפלציה'},
                    {'ticker': 'XOM',  'name': 'Exxon',           'reason': 'אנרגיה עולה עם אינפלציה'},
                    {'ticker': 'PG',   'name': 'Procter & Gamble','reason': 'מעביר עליות מחיר לצרכנים'},
                    {'ticker': 'COST', 'name': 'Costco',          'reason': 'מוצרי יסוד בסיטונאות'},
                ],
                'losers': [
                    {'ticker': 'TSLA', 'name': 'Tesla',  'reason': 'מוצר לא הכרחי — ביקוש יורד'},
                    {'ticker': 'AMZN', 'name': 'Amazon', 'reason': 'עלויות לוגיסטיקה ועבודה עולות'},
                    {'ticker': 'DIS',  'name': 'Disney', 'reason': 'בידור — ביקוש יורד ביוקר מחיה'},
                ],
                'tip': 'אינפלציה = זהב + אנרגיה + יסודות, הימנע מבידור ו-discretionary',
            },
        ]

        # שלוף חדשות עולם אמיתיות מ-RSS
        import xml.etree.ElementTree as ET
        rss_feeds = [
            ('https://feeds.bbci.co.uk/news/business/rss.xml', 'BBC Business'),
            ('https://rss.cnn.com/rss/money_news_international.rss', 'CNN Money'),
        ]
        live_news = []
        for feed_url, source in rss_feeds:
            try:
                req = requests.get(feed_url, timeout=5,
                    headers={'User-Agent': 'Mozilla/5.0'})
                root = ET.fromstring(req.content)
                for item in root.findall('.//item')[:5]:
                    title_en = item.findtext('title', '')
                    desc_en  = item.findtext('description', '')
                    link     = item.findtext('link', '')
                    # תרגם כותרת לעברית
                    title_he = translate_he(title_en) if title_en else title_en
                    live_news.append({
                        'title': title_he,
                        'title_en': title_en,
                        'desc': desc_en[:200],
                        'link': link,
                        'source': source,
                    })
            except Exception:
                pass

        # התאם חדשות לאירועי מאקרו (השוואה לפי אנגלית, הצגה בעברית)
        matched = []
        for event in MACRO_EVENTS:
            score = 0
            matched_titles = []
            for news in live_news:
                text = (news['title_en'] + ' ' + news['desc']).lower()
                hits = sum(1 for kw in event['keywords'] if kw.lower() in text)
                if hits > 0:
                    score += hits
                    matched_titles.append(news['title'])   # כבר עברית
            if matched_titles:
                matched.append({
                    'topic': event['topic'],
                    'relevance': score,
                    'news': matched_titles[:3],
                    'winners': event['winners'],
                    'losers': event['losers'],
                    'tip': event['tip'],
                })

        matched.sort(key=lambda x: x['relevance'], reverse=True)

        result = {
            'events': matched[:4],
            'all_events': [{'topic': e['topic'], 'tip': e['tip'], 'winners': e['winners'], 'losers': e['losers']} for e in MACRO_EVENTS],
            'live_news': live_news[:8],
        }
        cache_set('world_news', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/scan')
def scan_watchlist():
    """סריקת מניות לפי שיטת לייב 20 — מחזיר רק buy/strong-buy"""
    cached = cache_get('scan', ttl=600)
    if cached:
        return jsonify(cached)
    try:
        from scanner import run_scan
        results = run_scan(only_qualifying=True)
        import datetime as dt
        result = {
            'stocks': results,
            'scanned_at': dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            'total': len(results),
        }
        cache_set('scan', result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'stocks': []}), 500


@app.route('/dip-check')
def dip_check():
    """בדיקת 4 כללים לתפיסת הדיפ לפי שיטת מיכה סטוק"""
    cached = cache_get('dip_check', ttl=300)
    if cached:
        return jsonify(cached)
    import datetime as dt

    result = {
        'fg': {'value': None, 'pass': False, 'label': ''},
        'vix': {'value': None, 'pass': False, 'label': ''},
        's5fi': {'value': None, 'pass': False, 'label': ''},
        'spy_red': {'value': 0, 'pass': False, 'label': ''},
        'all_pass': False,
        'checked_at': dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    }

    # ── 1. Fear & Greed < 10 ──────────────────────────────────────────────────
    try:
        fg_data = get_fear_greed()
        fg_score = fg_data.get('score') if fg_data else None
        if fg_score is not None:
            result['fg']['value'] = round(float(fg_score), 1)
            result['fg']['pass'] = float(fg_score) < 10
            result['fg']['label'] = f'F&G = {result["fg"]["value"]}'
    except Exception:
        pass

    # ── 2. VIX > 30 ────────────────────────────────────────────────────────────
    try:
        vix_data = get_vix()
        if vix_data is not None:
            vix_val = float(vix_data['value'])
            result['vix']['value'] = vix_val
            result['vix']['pass'] = vix_val > 30
            result['vix']['label'] = f'VIX = {vix_val}'
    except Exception:
        pass

    # ── 3. S5FI < 20% — proxy: מספר סקטורים מתחת ל-MA50 ─────────────────────
    # ^S5FI לא זמין ב-Yahoo Finance; משתמשים ב-11 ETF סקטוריאליים כ-proxy
    try:
        sectors = ['XLK','XLF','XLE','XLC','XLI','XLV','XLY','XLP','XLB','XLRE','XLU']
        below_ma = 0
        checked  = 0
        for s in sectors:
            try:
                df_s = yf.Ticker(s).history(period='3mo')
                if len(df_s) >= 50:
                    ma50 = float(df_s['Close'].rolling(50).mean().iloc[-1])
                    price = float(df_s['Close'].iloc[-1])
                    checked += 1
                    if price < ma50:
                        below_ma += 1
            except Exception:
                pass
        if checked > 0:
            pct_below = round(below_ma / checked * 100, 1)
            result['s5fi']['value'] = pct_below
            result['s5fi']['pass'] = pct_below >= 80   # 80%+ סקטורים מתחת ל-MA50 ≈ S5FI < 20%
            result['s5fi']['label'] = f'{below_ma}/{checked} סקטורים מתחת MA50'
    except Exception:
        pass

    # ── 4. 3 ימים אדומים רצופים ב-SPY ──────────────────────────────────────
    try:
        spy = yf.Ticker('SPY')
        spy_hist = spy.history(period='10d')
        if len(spy_hist) >= 3:
            closes = spy_hist['Close'].values
            red_streak = 0
            for i in range(len(closes) - 1, 0, -1):
                if closes[i] < closes[i - 1]:
                    red_streak += 1
                else:
                    break
            result['spy_red']['value'] = red_streak
            result['spy_red']['pass'] = red_streak >= 3
            result['spy_red']['label'] = f'{red_streak} ימים אדומים רצופים'
    except Exception:
        pass

    result['all_pass'] = all([
        result['fg']['pass'],
        result['vix']['pass'],
        result['s5fi']['pass'],
        result['spy_red']['pass'],
    ])

    cache_set('dip_check', result)
    return jsonify(result)


@app.route('/portfolio')
def portfolio():
    return render_template('portfolio.html')


@app.route('/stock-news/<ticker>')
def stock_news(ticker):
    """חדשות עדכניות על מניה — Yahoo Finance + Google News, מתורגמות לעברית"""
    ticker = ticker.upper().strip()
    cache_key = f'stock_news_{ticker}'
    cached = cache_get(cache_key, ttl=300)  # cache 5 דקות
    if cached:
        return jsonify(cached)

    import xml.etree.ElementTree as ET
    import datetime as dt
    import urllib.parse

    news_items = []
    seen_titles = set()

    # ── 1. Google News RSS ─────────────────────────────────────────────────────
    try:
        q = urllib.parse.quote(f'{ticker} stock')
        rss_url = f'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'
        resp = requests.get(rss_url, timeout=8, headers={'User-Agent': 'Mozilla/5.0'})
        root = ET.fromstring(resp.content)
        for item in root.findall('.//item')[:12]:
            title_en = (item.findtext('title') or '').split(' - ')[0].strip()
            link     = item.findtext('link') or ''
            pub_str  = item.findtext('pubDate') or ''
            source_el = item.find('{https://news.google.com/rss}source')
            source   = source_el.text if source_el is not None else 'Google News'
            # parse date
            try:
                pub_dt = dt.datetime.strptime(pub_str[:25], '%a, %d %b %Y %H:%M:%S')
                pub_date = pub_dt.strftime('%d/%m %H:%M')
                sort_ts  = pub_dt.timestamp()
            except Exception:
                pub_date = ''
                sort_ts  = 0
            if title_en and title_en not in seen_titles:
                seen_titles.add(title_en)
                news_items.append({
                    'title_en': title_en,
                    'source': source,
                    'link': link,
                    'date': pub_date,
                    'sort_ts': sort_ts,
                    'origin': 'google',
                })
    except Exception:
        pass

    # ── 2. Yahoo Finance (yfinance) ────────────────────────────────────────────
    try:
        stock    = yf.Ticker(ticker)
        raw_news = stock.news or []
        for item in raw_news[:10]:
            content  = item.get('content', item)
            title_en = (content.get('title', '') or item.get('title', '')).strip()
            provider = content.get('provider', {})
            source   = provider.get('displayName', '') if isinstance(provider, dict) else item.get('publisher', '')
            canonical = content.get('canonicalUrl', {}) or content.get('clickThroughUrl', {})
            link     = canonical.get('url', '') if isinstance(canonical, dict) else item.get('link', '')
            pub_str  = content.get('pubDate', '') or content.get('displayTime', '')
            try:
                pub_dt   = dt.datetime.strptime(pub_str[:16], '%Y-%m-%dT%H:%M')
                pub_date = pub_dt.strftime('%d/%m %H:%M')
                sort_ts  = pub_dt.timestamp()
            except Exception:
                pub_ts   = item.get('providerPublishTime', 0)
                if pub_ts:
                    pub_dt   = dt.datetime.fromtimestamp(pub_ts)
                    pub_date = pub_dt.strftime('%d/%m %H:%M')
                    sort_ts  = pub_ts
                else:
                    pub_date = ''
                    sort_ts  = 0
            if title_en and title_en not in seen_titles:
                seen_titles.add(title_en)
                news_items.append({
                    'title_en': title_en,
                    'source': source or 'Yahoo Finance',
                    'link': link,
                    'date': pub_date,
                    'sort_ts': sort_ts,
                    'origin': 'yahoo',
                })
    except Exception:
        pass

    # ── מיון לפי זמן (הכי חדש ראשון) ──────────────────────────────────────────
    news_items.sort(key=lambda x: x['sort_ts'], reverse=True)
    news_items = news_items[:15]

    # ── תרגום לעברית ──────────────────────────────────────────────────────────
    keywords_mover = [
        'earnings', 'revenue', 'guidance', 'merger', 'acquisition', 'lawsuit',
        'sec', 'fda', 'deal', 'contract', 'beat', 'miss', 'upgrade', 'downgrade',
        'buyback', 'dividend', 'recall', 'investigation', 'bankruptcy', 'layoff',
        'partnership', 'ipo', 'split', 'rally', 'crash', 'surge', 'plunge',
    ]
    result_items = []
    for n in news_items:
        title_he = translate_he(n['title_en'])
        is_mover = any(kw in n['title_en'].lower() for kw in keywords_mover)
        result_items.append({
            'title':    title_he,
            'title_en': n['title_en'],
            'source':   n['source'],
            'link':     n['link'],
            'date':     n['date'],
            'is_mover': is_mover,
            'origin':   n['origin'],
        })

    result = {
        'ticker': ticker,
        'items':  result_items,
        'fetched_at': dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
    }
    cache_set(cache_key, result)
    return jsonify(result)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
