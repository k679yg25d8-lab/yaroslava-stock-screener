"""Premarket candidates only. No entry or exit simulation."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
import time
import server as engine

VERSION = 'market-pump-watch-v1'
ZONE = ZoneInfo('America/New_York')
LISTINGS_CACHE = {'expires': 0, 'rows': {}}

def listings():
    if time.time() < LISTINGS_CACHE['expires']:
        return LISTINGS_CACHE['rows']
    rows = dict(engine.nasdaq_tickers())
    for exchange in ('XNYS', 'XASE'):
        path = f'/v3/reference/tickers?market=stocks&exchange={exchange}&active=true&limit=1000'
        visited = set()
        while path:
            if path in visited or len(visited) > 30:
                raise engine.APIError(502, 'Некоректна пагінація біржового довідника')
            visited.add(path)
            data = engine.api_json(path)
            if not isinstance(data.get('results'), list):
                raise engine.APIError(502, 'Не отримано довідник біржі')
            for row in data['results']:
                if row.get('active') is True and row.get('primary_exchange') == exchange and row.get('type') in ('CS','ADRC'):
                    rows[row['ticker']] = row
            path = data.get('next_url')
    LISTINGS_CACHE.update(expires=time.time()+43200, rows=rows)
    return rows

def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)

def universe():
    stocks = listings()
    data = engine.api_json('/v2/snapshot/locale/us/markets/stocks/tickers')
    if not isinstance(data.get('tickers'), list):
        raise engine.APIError(502, 'Не отримано список Nasdaq')
    rows = []
    today = datetime.now(ZONE).date()
    for item in data['tickers']:
        symbol = item.get('ticker')
        if symbol not in stocks:
            continue
        day, minute, trade = (item.get(k) or {} for k in ('day', 'min', 'lastTrade'))
        price = trade.get('p') or minute.get('c') or day.get('c')
        previous = (item.get('prevDay') or {}).get('c')
        if not finite(price) or not 0 < price <= 30 or not finite(previous) or previous <= 0:
            continue
        stamps = [minute.get('t')]
        if finite(trade.get('t')):
            stamps.append(trade['t']/1e6 if trade['t'] > 1e15 else trade['t'])
        if not any(finite(t) and t > 0 and datetime.fromtimestamp(t/1000, ZONE).date() == today for t in stamps):
            continue
        # Snapshot day.h describes regular hours and is often zero before 09:30.
        # It cannot rule out a premarket high, including a pump already retraced.
        rows.append(dict(symbol=symbol, previousClose=previous, priority=price/previous))
    rows.sort(key=lambda row:row['priority'], reverse=True)
    return dict(candidates=rows, available=len(stocks), strategyVersion=VERSION)

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
                high > previous * 1.30 and signal.get('bandTouch') is True)
    ready = eligible and finite(volume) and volume > 10_000_000
    return dict(symbol=symbol, date=str(now.date()), strategyVersion=VERSION,
                close=price, previousClose=previous, premarketGrowthPercent=growth,
                eligible=eligible, volumeReady=ready,
                status='Готова за обсягом' if ready else 'Потрібен обсяг' if eligible else 'Не відповідає',
                **signal, **activity)

def candidate(symbol, previous, now):
    prior_day = engine.previous_session(now)
    return evaluate(symbol, previous, engine.minute_bars(symbol, now.date()), now, prior_day)
