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

def evaluate(symbol, previous, bars, now):
    bars = sorted((b for b in bars if finite(b.get('t')) and b['t'] <= now.timestamp()*1000), key=lambda b:b['t'])
    signal = engine.signal_from_bars(bars, now)
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
                **signal)

def candidate(symbol, previous, now):
    return evaluate(symbol, previous, engine.minute_bars(symbol, now.date()), now)
