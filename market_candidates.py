"""Premarket candidates only. No entry or exit simulation."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
import server as engine

VERSION = 'market-pump-watch-v1'
ZONE = ZoneInfo('America/New_York')

def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)

def universe():
    listings = engine.nasdaq_tickers()
    data = engine.api_json('/v2/snapshot/locale/us/markets/stocks/tickers')
    if not isinstance(data.get('tickers'), list):
        raise engine.APIError(502, 'Не отримано список Nasdaq')
    rows = []
    for item in data['tickers']:
        symbol = item.get('ticker')
        if symbol not in listings:
            continue
        day, minute, trade = (item.get(k) or {} for k in ('day', 'min', 'lastTrade'))
        price = trade.get('p') or minute.get('c') or day.get('c')
        previous = (item.get('prevDay') or {}).get('c')
        high = day.get('h')
        if not finite(price) or not 0 < price <= 30 or not finite(previous) or previous <= 0:
            continue
        # A broad snapshot prefilter; actual growth and touch require PM minute bars.
        if finite(high) and high <= previous * 1.30:
            continue
        rows.append(dict(symbol=symbol, previousClose=previous))
    return dict(candidates=rows, available=len(listings), strategyVersion=VERSION)

def prior_activity(bars, prior_day):
    window, touches, sessions = [], [], {'market': [], 'postmarket': []}
    for bar in bars:
        window.append(bar.get('c'))
        if len(window) > 10:
            window.pop(0)
        stamp = datetime.fromtimestamp(bar['t']/1000, ZONE)
        minute = stamp.hour*60+stamp.minute
        if stamp.date() != prior_day or not 570 <= minute < 1200:
            continue
        sessions['market' if minute < 960 else 'postmarket'].append(bar)
        if len(window) == 10 and all(finite(c) for c in window) and finite(bar.get('h')) and bar['h'] >= sum(window)/10*1.10:
            touches.append(bar['t'])
    growths = {}
    for name, session in sessions.items():
        opening = session[0].get('o') if session else None
        high = max((b['h'] for b in session if finite(b.get('h'))), default=None)
        growths[name] = (high/opening-1)*100 if finite(opening) and opening > 0 and finite(high) else None
    active = bool(touches) or any(finite(g) and g >= 15-1e-9 for g in growths.values())
    return dict(previousActivity=active, previousActivityDate=str(prior_day),
                previousMarketGrowth=growths['market'], previousPostmarketGrowth=growths['postmarket'],
                previousBandTouch=bool(touches), previousBandTimestamp=touches[-1] if touches else None)

def evaluate(symbol, previous, bars, now, prior_day):
    bars = sorted((b for b in bars if finite(b.get('t')) and b['t'] <= now.timestamp()*1000), key=lambda b:b['t'])
    signal = engine.signal_from_bars(bars, now)
    activity = prior_activity(bars, prior_day)
    high = signal.get('premarketHigh')
    growth = (high / previous - 1) * 100 if finite(high) and finite(previous) and previous > 0 else None
    today = [b for b in bars if datetime.fromtimestamp(b['t']/1000, ZONE).date() == now.date()]
    price = today[-1].get('c') if today else None
    volume = signal.get('premarketVolume')
    eligible = (finite(price) and 0 < price <= 30 and finite(high) and
                high > previous * 1.30 and signal.get('bandTouch') is True and activity['previousActivity'])
    ready = eligible and finite(volume) and volume > 10_000_000
    return dict(symbol=symbol, date=str(now.date()), strategyVersion=VERSION,
                close=price, previousClose=previous, premarketGrowthPercent=growth,
                eligible=eligible, volumeReady=ready,
                status='Готова за обсягом' if ready else 'Потрібен обсяг' if eligible else 'Не відповідає',
                **signal, **activity)

def candidate(symbol, previous, now):
    prior_day = engine.previous_session(now)
    return evaluate(symbol, previous, engine.minute_bars(symbol, now.date()), now, prior_day)
