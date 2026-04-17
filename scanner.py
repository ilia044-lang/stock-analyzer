"""
scanner.py — סורק מניות לפי שיטת מיכה סטוקס (לייב 20)
מחזיר סיכום: strong-buy / buy / neutral / sell / strong-sell
"""

import yfinance as yf
import pandas as pd
import numpy as np

# ── רשימת מניות מלאה לפי צפיות המשתמש ─────────────────────────────────────────
WATCHLIST = [
    # מניות ליבה
    "ASTS", "NOW", "IREN", "NNE", "MSTR", "QQQ", "GOOG", "NVDA", "MSFT",
    "AAPL", "JPM", "META", "AMZN", "TSLA", "AVGO", "SOFI", "ORCL", "PLTR",
    "HOOD", "RKLB", "OKLO", "IBIT", "ETHA",
    # חלל / ביטחון
    "LUNR", "AFRM", "CVNA", "ONDS", "PL", "KTOS",
    "NFLX", "JOBY", "ACHR", "UFO", "SPCE", "HEI",
    "GE", "BA", "LHX", "LMT", "NOC", "RTX", "IRDM", "VSAT",
    # ETFs
    "SLV", "NLR", "URA", "IGV", "QTUM", "SOXX", "GLD", "AIQ", "VWO",
    "IWM", "SSO", "ITA", "DBA", "USO", "SPY", "WEAT", "PPA", "DIA",
    "FXI", "TAN",
    # כריית קריפטו
    "RIOT", "MARA", "BITF", "HUT", "WULF",
    # שונות
    "SOUN", "BBAI", "OPEN", "ZIM", "IONQ", "QBTS", "RGTI", "QUBT",
    "EOSE", "NBIS", "CIFR",
]

# ── שמות ידידותיים ────────────────────────────────────────────────────────────
NAMES = {
    "ASTS": "AST SpaceMobile", "NOW": "ServiceNow", "IREN": "Iris Energy",
    "NNE": "Nano Nuclear", "MSTR": "MicroStrategy", "QQQ": "QQQ ETF",
    "GOOG": "Alphabet", "NVDA": "NVIDIA", "MSFT": "Microsoft",
    "AAPL": "Apple", "JPM": "JPMorgan", "META": "Meta",
    "AMZN": "Amazon", "TSLA": "Tesla", "AVGO": "Broadcom",
    "SOFI": "SoFi Tech", "ORCL": "Oracle", "PLTR": "Palantir",
    "HOOD": "Robinhood", "RKLB": "Rocket Lab", "OKLO": "Oklo",
    "IBIT": "iShares Bitcoin", "ETHA": "iShares Ethereum",
    "LUNR": "Intuitive Machines", "AFRM": "Affirm", "CVNA": "Carvana",
    "ONDS": "Ondas Holdings", "PL": "Planet Labs", "KTOS": "Kratos Defense",
    "NFLX": "Netflix", "JOBY": "Joby Aviation", "ACHR": "Archer Aviation",
    "UFO": "Procure Space ETF", "SPCE": "Virgin Galactic", "HEI": "HEICO",
    "GE": "GE Aerospace", "BA": "Boeing", "LHX": "L3Harris",
    "LMT": "Lockheed Martin", "NOC": "Northrop Grumman", "RTX": "RTX Corp",
    "IRDM": "Iridium Comm", "VSAT": "Viasat",
    "SLV": "Silver ETF", "NLR": "Nuclear Energy ETF", "URA": "Uranium ETF",
    "IGV": "Software ETF", "QTUM": "Quantum ETF", "SOXX": "Semiconductor ETF",
    "GLD": "Gold ETF", "AIQ": "AI & Tech ETF", "VWO": "Emerging Mkts ETF",
    "IWM": "Russell 2000 ETF", "SSO": "2x S&P500 ETF", "ITA": "Defense ETF",
    "DBA": "Agriculture ETF", "USO": "Oil ETF", "SPY": "S&P500 ETF",
    "WEAT": "Wheat ETF", "PPA": "Aerospace ETF", "DIA": "Dow Jones ETF",
    "FXI": "China ETF", "TAN": "Solar ETF",
    "RIOT": "Riot Platforms", "MARA": "Marathon Digital", "BITF": "Bitfarms",
    "HUT": "Hut 8 Mining", "WULF": "TeraWulf",
    "SOUN": "SoundHound AI", "BBAI": "BigBear.ai", "OPEN": "Opendoor",
    "ZIM": "ZIM Integrated", "IONQ": "IonQ", "QBTS": "D-Wave Quantum",
    "RGTI": "Rigetti Computing", "QUBT": "Quantum Computing Inc",
    "EOSE": "Eos Energy", "NBIS": "Nebius Group", "CIFR": "Cipher Mining",
}


# ── אינדיקטורים ────────────────────────────────────────────────────────────────

def _ticker(symbol):
    return yf.Ticker(symbol)


def calc_ma20(close):
    return close.rolling(window=20).mean()

def calc_cci(high, low, close, period=14):
    tp  = (high + low + close) / 3
    sma = tp.rolling(period).mean()
    mad = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma) / (0.015 * mad)

def calc_atr(high, low, close, period=14):
    # Wilder's RMA — זהה ל-TradingView
    prev_close = close.shift(1)
    tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, adjust=False).mean()


# ── בנה סיבה קצרה ─────────────────────────────────────────────────────────────

def build_reason(signals_map, direction='bullish'):
    """מחזיר שורה אחת עם הסיבות לפי כיוון"""
    bull_labels = {
        'trend':  'מגמה עולה',
        'candle': 'נר בוליש',
        'volume': 'ווליום חזק',
        'ma':     'מעל MA20',
        'gap':    'גאפ פתוח',
        'cci':    'CCI חיובי',
    }
    bear_labels = {
        'trend':  'מגמה יורדת',
        'candle': 'נר בריש',
        'volume': 'ווליום ירידה',
        'ma':     'מתחת MA20',
        'gap':    'גאפ ירידה',
        'cci':    'CCI שלילי',
    }
    if direction == 'bearish':
        parts = [bear_labels[k] for k, v in signals_map.items() if v == 'bearish' and k in bear_labels]
    else:
        parts = [bull_labels[k] for k, v in signals_map.items() if v == 'bullish' and k in bull_labels]
    return ' · '.join(parts) if parts else ''


# ── ניתוח מהיר ────────────────────────────────────────────────────────────────

def quick_analyze(ticker):
    try:
        stock = _ticker(ticker)
        df    = stock.history(period='3mo')
        if df.empty or len(df) < 22:
            return None

        close  = df['Close']
        high   = df['High']
        low    = df['Low']
        volume = df['Volume']
        open_p = df['Open']

        current_price = round(float(close.iloc[-1]), 2)
        prev_close    = round(float(close.iloc[-2]), 2)
        change_pct    = round((current_price - prev_close) / prev_close * 100, 2)

        # ── 1. טרנד ──
        # לפי לייב 20: חייב שגם החודש וגם השבוע יהיו בוליש — לא מספיק אחד מהם
        monthly_chg = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] * 100
        weekly_chg  = (close.iloc[-1] - close.iloc[-5])  / close.iloc[-5]  * 100
        if monthly_chg > 3 and weekly_chg > 1:
            trend_sig = 'bullish'               # גם חודש וגם שבוע עולים
        elif monthly_chg < -3 and weekly_chg < -1:
            trend_sig = 'bearish'               # גם חודש וגם שבוע יורדים
        else:
            trend_sig = 'neutral'

        # ── 2. נרות ──
        o1,h1,l1,c1 = float(open_p.iloc[-1]), float(high.iloc[-1]), float(low.iloc[-1]), float(close.iloc[-1])
        o2,h2,l2,c2 = float(open_p.iloc[-2]), float(high.iloc[-2]), float(low.iloc[-2]), float(close.iloc[-2])
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
            candle_sig = 'bullish'
        elif c1 < o1 and c2 > o2 and c1 < o2 and o1 > c2:
            candle_sig = 'bearish'
        elif range1 > 0 and body1/range1 > 0.92:
            candle_sig = 'bullish' if c1 > o1 else 'bearish'

        # ── 3. ווליום ──
        avg_vol  = float(volume.iloc[-20:].mean())
        last_vol = float(volume.iloc[-1])
        ratio    = last_vol / avg_vol if avg_vol > 0 else 1
        up_day   = close.iloc[-1] > close.iloc[-2]
        vol_sig  = 'neutral'
        if up_day and ratio > 1.2:
            vol_sig = 'bullish'
        elif not up_day and last_vol < float(volume.iloc[-2]) and ratio < 1.0:
            vol_sig = 'bullish'
        elif not up_day and ratio > 1.3:
            vol_sig = 'bearish'

        # ── 4. MA20 ──
        # לפי לייב 20: מחיר חייב להיות מעל MA20 עולה — אין יוצא מן הכלל
        ma      = calc_ma20(close)
        curr_ma = float(ma.iloc[-1])
        prev_ma = float(ma.iloc[-2])
        dist    = (current_price - curr_ma) / curr_ma * 100
        ma_sig  = 'neutral'
        if current_price > curr_ma and curr_ma > prev_ma:
            ma_sig = 'bullish'   # מחיר מעל MA20 עולה
        elif current_price < curr_ma and curr_ma < prev_ma:
            ma_sig = 'bearish'   # מחיר מתחת MA20 יורד (תנאי שורט)
        elif current_price > curr_ma:
            ma_sig = 'neutral'   # מעל MA20 אך לא עולה
        else:
            ma_sig = 'bearish'   # מתחת ל-MA20

        # ── 5. גאפים ──
        gaps_up, gaps_down = 0, 0
        for i in range(max(0, len(open_p)-30), len(open_p)):
            if i == 0: continue
            ph, pl = float(high.iloc[i-1]), float(low.iloc[i-1])
            co = float(open_p.iloc[i])
            if co > ph * 1.002:
                if float(low.iloc[i:].min()) > ph:
                    gaps_up += 1
            elif co < pl * 0.998:
                if float(high.iloc[i:].max()) < pl:
                    gaps_down += 1
        gap_sig = 'bullish' if gaps_up and not gaps_down else \
                  'bearish' if gaps_down and not gaps_up else 'neutral'

        # ── 6. CCI ──
        cci    = calc_cci(high, low, close)
        val    = float(cci.iloc[-1])
        prev_v = float(cci.iloc[-2])
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

        # ── ציון לפי שיטת לייב 20 ──────────────────────────────────────────────
        signals_map = {
            'trend': trend_sig, 'candle': candle_sig, 'volume': vol_sig,
            'ma': ma_sig, 'gap': gap_sig, 'cci': cci_sig,
        }
        bullish = sum(1 for v in signals_map.values() if v == 'bullish')
        bearish = sum(1 for v in signals_map.values() if v == 'bearish')

        # ── strong-buy: חייב לעמוד בכל 3 תנאי הסף + 5 מתוך 6 בוליש ─────────────
        strong_buy = (
            ma_sig == 'bullish' and         # מחיר מעל MA20 עולה
            trend_sig == 'bullish' and      # גם חודש וגם שבוע עולים
            bearish == 0 and                # אפס סיגנלים בריש
            bullish >= 5                    # 5 או 6 מתוך 6 בוליש
        )

        # ── strong-sell: מראה של קניה בהיפוך — שורט ─────────────────────────────
        strong_sell = (
            ma_sig == 'bearish' and         # מחיר מתחת MA20 יורד
            trend_sig == 'bearish' and      # גם חודש וגם שבוע יורדים
            bullish == 0 and                # אפס סיגנלים בוליש
            bearish >= 5                    # 5 או 6 מתוך 6 בריש
        )

        if strong_buy:
            rec = 'strong-buy'
        elif strong_sell:
            rec = 'strong-sell'
        else:
            rec = 'neutral'

        direction = 'bearish' if strong_sell else 'bullish'

        atr = round(float(calc_atr(high, low, close).iloc[-1]), 2)
        atr_pct = round(atr / current_price * 100, 2)

        return {
            'ticker':     ticker,
            'name':       NAMES.get(ticker, ticker),
            'price':      current_price,
            'change_pct': change_pct,
            'bullish':    bullish,
            'bearish':    bearish,
            'rec':        rec,
            'reason':     build_reason(signals_map, direction),
            'ma20':       round(curr_ma, 2),
            'dist_ma':    round(dist, 1),
            'atr_pct':    atr_pct,
            'cci_val':    round(val, 0),
        }
    except Exception:
        return None


def run_scan(only_qualifying=True):
    """סורק את כל המניות — מחזיר רק strong-buy ו-strong-sell"""
    results = []
    for ticker in WATCHLIST:
        r = quick_analyze(ticker)
        if r:
            if only_qualifying:
                if r['rec'] in ('strong-buy', 'strong-sell'):
                    results.append(r)
            else:
                results.append(r)

    # strong-buy קודם, strong-sell אחרון
    order = {'strong-buy': 0, 'strong-sell': 1}
    results.sort(key=lambda x: (order.get(x['rec'], 5), -x['bullish']))
    return results


if __name__ == '__main__':
    print("סורק...")
    res = run_scan(only_qualifying=False)
    for r in res:
        print(f"{r['ticker']:6} {r['rec']:12} bull={r['bullish']} bear={r['bearish']} "
              f"price={r['price']} chg={r['change_pct']:+.1f}% | {r['reason']}")
