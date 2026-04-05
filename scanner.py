"""
scanner.py — סורק 50 מניות לפי שיטת מיכה סטוקס (לייב 20)
מחזיר סיכום: strong-buy / buy / neutral / sell / strong-sell
"""

import yfinance as yf
import pandas as pd
import numpy as np

# ── רשימת 50 מניות מדוברות ברשת (Medium Cap) ──────────────────────────────────
WATCHLIST = [
    # FinTech / Crypto adjacent
    "HOOD", "SOFI", "AFRM", "UPST", "NU", "COIN", "MSTR",
    # EV / Clean Energy
    "RIVN", "LCID", "ACHR", "JOBY", "BLDE", "LILM",
    # AI / Tech mid-cap
    "IONQ", "SOUN", "RXRX", "BBAI", "AI", "SMCI",
    # Biotech
    "NVAX", "CRSP", "CELH", "BEAM", "MRNA", "BNTX",
    # Crypto miners
    "MARA", "RIOT", "CLSK", "HUT", "WULF", "IREN",
    # Space / Defense tech
    "ASTS", "RKLB", "PL", "LUNR",
    # Consumer / Gaming
    "RBLX", "DKNG", "PENN", "CHWY", "DASH", "LYFT",
    # Social / Comm
    "SNAP", "PINS",
    # Real estate / Alt
    "OPEN", "OPFI",
    # Telecom / Legacy tech
    "BB", "NOK",
    # Additional popular
    "SMAR", "ZI", "TWLO", "ARBK"
]


# ── אינדיקטורים ────────────────────────────────────────────────────────────────

def calc_ma20(close):
    return close.rolling(window=20).mean()

def calc_cci(high, low, close, period=14):
    tp  = (high + low + close) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma) / (0.015 * mad)

def calc_atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ── ניתוח מהיר ────────────────────────────────────────────────────────────────

def quick_analyze(ticker):
    try:
        stock = yf.Ticker(ticker)
        df    = stock.history(period='3mo')
        if df.empty or len(df) < 22:
            return None

        close  = df['Close']
        high   = df['High']
        low    = df['Low']
        volume = df['Volume']
        open_p = df['Open']

        current_price = round(close.iloc[-1], 2)
        prev_close    = round(close.iloc[-2], 2)
        change_pct    = round((current_price - prev_close) / prev_close * 100, 2)

        # ── 1. טרנד ──
        monthly_chg = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100
        weekly_chg  = (close.iloc[-1] - close.iloc[-5])  / close.iloc[-5]  * 100
        if monthly_chg > 5 or weekly_chg > 2:
            trend_sig = 'bullish'
        elif monthly_chg < -5 or weekly_chg < -2:
            trend_sig = 'bearish'
        else:
            trend_sig = 'neutral'

        # ── 2. נרות ──
        o1,h1,l1,c1 = open_p.iloc[-1], high.iloc[-1], low.iloc[-1], close.iloc[-1]
        o2,h2,l2,c2 = open_p.iloc[-2], high.iloc[-2], low.iloc[-2], close.iloc[-2]
        body1  = abs(c1-o1)
        range1 = h1-l1 if h1!=l1 else 0.0001
        lower1 = min(o1,c1)-l1
        upper1 = h1-max(o1,c1)
        candle_sig = 'neutral'
        if lower1 > 2*body1 and upper1 < body1*0.5:
            candle_sig = 'bullish'
        elif upper1 > 2*body1 and lower1 < body1*0.5 and c1 < o1:
            candle_sig = 'bearish'
        elif c1 > o1 and c2 < o2 and c1 > o2 and o1 < c2:
            candle_sig = 'bullish'  # בולען בוליש
        elif c1 < o1 and c2 > o2 and c1 < o2 and o1 > c2:
            candle_sig = 'bearish'  # בולען בריש
        elif range1 > 0 and body1/range1 > 0.92:
            candle_sig = 'bullish' if c1 > o1 else 'bearish'

        # ── 3. ווליום ──
        avg_vol  = volume.iloc[-20:].mean()
        last_vol = volume.iloc[-1]
        ratio    = last_vol / avg_vol if avg_vol > 0 else 1
        up_day   = close.iloc[-1] > close.iloc[-2]
        vol_sig  = 'neutral'
        if up_day and ratio > 1.2:
            vol_sig = 'bullish'
        elif not up_day and last_vol < volume.iloc[-2] and ratio < 1.0:
            vol_sig = 'bullish'
        elif not up_day and ratio > 1.3:
            vol_sig = 'bearish'

        # ── 4. MA20 ──
        ma     = calc_ma20(close)
        curr_ma = ma.iloc[-1]
        prev_ma = ma.iloc[-2]
        dist    = (current_price - curr_ma) / curr_ma * 100
        ma_sig  = 'neutral'
        if current_price > curr_ma and curr_ma > prev_ma:
            ma_sig = 'bullish'
        elif dist < -8:
            ma_sig = 'bullish'  # גומיה מתוחה
        elif dist < -4:
            ma_sig = 'neutral'
        elif current_price < curr_ma:
            ma_sig = 'bearish'

        # ── 5. גאפים ──
        gaps_up, gaps_down = 0, 0
        for i in range(max(0, len(open_p)-30), len(open_p)):
            if i == 0: continue
            ph, pl = high.iloc[i-1], low.iloc[i-1]
            co = open_p.iloc[i]
            if co > ph * 1.002:
                if low.iloc[i:].min() > ph:
                    gaps_up += 1
            elif co < pl * 0.998:
                if high.iloc[i:].max() < pl:
                    gaps_down += 1
        gap_sig = 'bullish' if gaps_up and not gaps_down else \
                  'bearish' if gaps_down and not gaps_up else 'neutral'

        # ── 6. CCI ──
        cci    = calc_cci(high, low, close)
        val    = cci.iloc[-1]
        prev_v = cci.iloc[-2]
        cci_sig = 'neutral'
        if prev_v < -100 <= val:
            cci_sig = 'bullish'
        elif prev_v >= 0 > val:
            cci_sig = 'bearish'
        elif prev_v < 0 <= val:
            cci_sig = 'bullish'
        elif val < -100 and val > prev_v:
            cci_sig = 'bullish'
        elif val > 100 and val > prev_v:
            cci_sig = 'bullish'

        # ── ציון ──
        signals  = [trend_sig, candle_sig, vol_sig, ma_sig, gap_sig, cci_sig]
        bullish  = signals.count('bullish')
        bearish  = signals.count('bearish')

        if bullish >= 5:
            rec = 'strong-buy'
        elif bullish >= 4:
            rec = 'buy'
        elif bullish >= 3 and bearish == 0:
            rec = 'buy'
        elif bearish >= 5:
            rec = 'strong-sell'
        elif bearish >= 4:
            rec = 'sell'
        else:
            rec = 'neutral'

        # ATR
        atr = round(calc_atr(high, low, close).iloc[-1], 2)
        atr_pct = round(atr / current_price * 100, 2)

        return {
            'ticker':     ticker,
            'price':      current_price,
            'change_pct': change_pct,
            'bullish':    bullish,
            'bearish':    bearish,
            'rec':        rec,
            'ma20':       round(curr_ma, 2),
            'dist_ma':    round(dist, 1),
            'atr_pct':    atr_pct,
            'cci_val':    round(val, 0),
            'avg_vol':    int(avg_vol),
        }
    except Exception:
        return None


def run_scan():
    """סורק את כל המניות ומחזיר תוצאות ממוינות"""
    results = []
    for ticker in WATCHLIST:
        r = quick_analyze(ticker)
        if r:
            results.append(r)

    # מיון: קודם strong-buy, אחר כך buy, וכו'
    order = {'strong-buy': 0, 'buy': 1, 'neutral': 2, 'sell': 3, 'strong-sell': 4}
    results.sort(key=lambda x: (order.get(x['rec'], 5), -x['bullish']))
    return results


if __name__ == '__main__':
    print("סורק...")
    res = run_scan()
    for r in res:
        print(f"{r['ticker']:6} {r['rec']:12} bull={r['bullish']} bear={r['bearish']} "
              f"price={r['price']} chg={r['change_pct']:+.1f}% dist={r['dist_ma']:+.1f}%")
