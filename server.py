#!/usr/bin/env python3
"""Local stock screener. API key stays in process memory."""
import json
import re
import time
import secrets
from contextvars import ContextVar, copy_context
import os
import telegram_bridge as telegram
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get('SCREENER_PORT', '8765'))
ORIGIN = f'http://127.0.0.1:{PORT}'
CURRENT_KEY = ContextVar('polygon_key', default=None)
NONCE = secrets.token_urlsafe(32)

class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)

NASDAQ_CACHE = {'expires': 0, 'tickers': {}}
BAR_CACHE = {}
SCAN_LOCK = __import__('threading').Lock()

def api_json(path):
    if CURRENT_KEY.get() is None:
        raise APIError(503, 'Введіть ключ Polygon через кнопку «Ключ API»')
    url = urllib.parse.urlsplit(path)
    if url.scheme:
        if url.scheme != 'https' or url.hostname not in ('api.polygon.io', 'api.massive.com') or url.username:
            raise APIError(502, 'Непідтримувана адреса пагінації Polygon')
        endpoint = path
    else:
        endpoint = 'https://api.polygon.io' + path
    request = urllib.request.Request(endpoint, headers={'Authorization': 'Bearer ' + CURRENT_KEY.get()})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        raise APIError(err.code, {401:'Ключ Polygon недійсний',403:'Тариф Polygon не дає доступу до цього endpoint',429:'Ліміт запитів Polygon. Дані не підміняються.'}.get(err.code, f'Polygon HTTP {err.code}')) from None
    except (OSError, ValueError):
        raise APIError(502, 'Не вдалося отримати дані Polygon') from None

def nasdaq_tickers():
    if time.time() < NASDAQ_CACHE['expires']:
        return NASDAQ_CACHE['tickers']
    path = '/v3/reference/tickers?market=stocks&exchange=XNAS&active=true&limit=1000'
    records, visited = {}, set()
    while path:
        if path in visited or len(visited) > 30:
            raise APIError(502, 'Некоректна пагінація довідника Nasdaq')
        visited.add(path)
        data = api_json(path)
        if not isinstance(data.get('results'), list):
            raise APIError(502, 'Polygon не повернув довідник Nasdaq')
        for item in data['results']:
            if item.get('primary_exchange') == 'XNAS' and item.get('type') in ('CS','ADRC') and item.get('active') is True:
                records[item['ticker']] = item
        path = data.get('next_url')
    NASDAQ_CACHE.update(expires=time.time()+43200, tickers=records)
    return records

def minute_bars(symbol, date):
    key = (symbol, str(date))
    cached = BAR_CACHE.get(key)
    ttl = 300 if datetime.now(ZoneInfo('America/New_York')).hour*60+datetime.now(ZoneInfo('America/New_York')).minute>=570 else 5
    if cached and time.time()-cached[0] < ttl:
        return cached[1]
    start = date - timedelta(days=7)
    data = api_json(f'/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/minute/{start}/{date}?adjusted=true&sort=asc&limit=50000')
    if data.get('next_url'):
        raise APIError(422, 'Неповна історія хвилин: потрібна пагінація')
    bars = data.get('results') or []
    BAR_CACHE[key] = (time.time(), bars[-5000:])
    if len(BAR_CACHE) > 1000:
        oldest = min(BAR_CACHE, key=lambda k: BAR_CACHE[k][0])
        del BAR_CACHE[oldest]
    return bars[-5000:]

def signal_from_bars(bars, now):
    now_ms = int(now.timestamp()*1000)
    start = now.replace(hour=4, minute=0, second=0, microsecond=0)
    end = min(now, now.replace(hour=9, minute=30, second=0, microsecond=0))
    start_ms, end_ms = int(start.timestamp()*1000), int(end.timestamp()*1000)
    ordered = sorted((b for b in bars if isinstance(b.get('t'), (int,float)) and b['t'] <= now_ms), key=lambda b:b['t'])
    session = [b for b in ordered if start_ms <= b['t'] < end_ms]
    volume = sum(b['v'] for b in session if isinstance(b.get('v'), (int,float)))
    valid_volume = bool(session) and all(isinstance(b.get('v'), (int,float)) for b in session)
    window, total, touches = [], 0, []
    last_band = None
    complete = bool(session)
    for bar in ordered:
        window.append(bar.get('c'))
        if len(window)>10:
            window.pop(0)
        valid = len(window)==10 and all(isinstance(c,(int,float)) for c in window)
        sma = sum(window)/10 if valid else None
        band = {'bar':bar, 'sma':sma, 'upper':sma*1.10 if sma is not None else None}
        last_band = band
        if start_ms <= bar['t'] < end_ms:
            high = bar.get('h')
            if not valid or not isinstance(high,(int,float)):
                complete = False
            elif high >= band['upper']:
                touches.append(band)
    chosen = touches[-1] if touches else last_band
    bar = chosen['bar'] if chosen else {}
    sma = chosen['sma'] if chosen else None
    return {'premarketHigh': max((b['h'] for b in session if isinstance(b.get('h'),(int,float))),default=None),
        'premarketVolume': volume if valid_volume else None,
        'volumeProvenance': 'Сума minute.v за 04:00–09:30 ET цього дня',
        'upperBand': chosen['upper'] if chosen else None,
        'lowerBand': sma*.90 if sma is not None else None, 'sma10':sma,
        'signalHigh':bar.get('h'), 'signalTimestamp':bar.get('t') if touches else None,
        'bandTouch':True if touches else False if complete else None,
        'signalProvenance':'Торкання High ≥ SMA її 10 close × 1.10 будь-коли за цей премаркет',
        'touchCount':len(touches)}


DAILY_CACHE = {}
def daily_metrics(symbol, now, current_bars):
    """TOS iATR = mean daily high-low (not Wilder True Range)."""
    import math
    from decimal import Decimal, ROUND_HALF_UP
    key = (symbol, str(now.date()))
    cached = DAILY_CACHE.get(key)
    if cached and time.time()-cached[0] < 30:
        bars = cached[1]
    else:
        start = now.date()-timedelta(days=60)
        data = api_json(f'/v2/aggs/ticker/{urllib.parse.quote(symbol)}/range/1/day/{start}/{now.date()}?adjusted=true&sort=asc&limit=50000')
        if data.get('next_url'):
            raise APIError(422, 'Неповна денна історія ATR / AvgVol')
        bars = data.get('results') or []
        DAILY_CACHE[key] = (time.time(), bars)
        if len(DAILY_CACHE)>250:
            del DAILY_CACHE[min(DAILY_CACHE,key=lambda k:DAILY_CACHE[k][0])]
    zone = ZoneInfo('America/New_York')
    valid = lambda x:isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
    previous = sorted((b for b in bars if valid(b.get('t')) and datetime.fromtimestamp(b['t']/1000,zone).date()<now.date()),key=lambda b:b['t'])
    # Prior-session as-of value: skip the latest completed daily candle.
    volume_bars = previous[-15:-1]
    volumes = [b.get('v') for b in volume_bars]
    average = sum(volumes)/14 if len(volumes)==14 and all(valid(v) and v>=0 for v in volumes) else None
    # Current day's range uses all available minute bars through now, including premarket.
    today = [b for b in current_bars if valid(b.get('t')) and b['t']<=now.timestamp()*1000 and datetime.fromtimestamp(b['t']/1000,zone).date()==now.date()]
    ranges = [b['h']-b['l'] if valid(b.get('h')) and valid(b.get('l')) and b['h']>=b['l'] else None for b in previous[-13:]]
    if today and all(valid(b.get('h')) and valid(b.get('l')) and b['h']>=b['l'] for b in today):
        ranges.append(max(b['h'] for b in today)-min(b['l'] for b in today))
    value = sum(ranges)/14 if len(ranges)==14 and all(valid(v) for v in ranges) else None
    rounded = lambda value,places:float(Decimal(str(value)).quantize(Decimal(places),rounding=ROUND_HALF_UP)) if value is not None else None
    return {'atr':rounded(value,'0.01'),'avgVolume':rounded(average,'1'),
        'dailyMetricsDate':str(now.date()),
        'atrProvenance':'TOS: середній High − Low 14 денних свічок, включно з поточним днем; округлення 2 знаки',
        'avgVolumeAsOf':datetime.fromtimestamp(previous[-1]['t']/1000,zone).date().isoformat() if previous else None,
        'avgVolumeProvenance':'14 завершених денних свічок станом на попередній торговий день (затримка 1 сесія); округлення до акцій'}


PREVIOUS_SESSION_CACHE = {}
def previous_session(now):
    key = str(now.date())
    if key not in PREVIOUS_SESSION_CACHE:
        start, end = now.date()-timedelta(days=14), now.date()-timedelta(days=1)
        data = api_json(f'/v2/aggs/ticker/SPY/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=100')
        if data.get('next_url'):
            raise APIError(502, 'Неповний календар торгових днів')
        days = [datetime.fromtimestamp(b['t']/1000,ZoneInfo('America/New_York')).date() for b in data.get('results',[]) if isinstance(b.get('t'),(int,float))]
        days = [d for d in days if d<now.date()]
        if not days:
            raise APIError(502, 'Попередній торговий день не підтверджено')
        PREVIOUS_SESSION_CACHE.clear()
        PREVIOUS_SESSION_CACHE[key] = max(days)
    return PREVIOUS_SESSION_CACHE[key]

def previous_activity(bars, day):
    at = datetime.combine(day,datetime.min.time(),tzinfo=ZoneInfo('America/New_York')).replace(hour=9,minute=30)
    day_bars = [b for b in bars if isinstance(b.get('t'),(int,float)) and datetime.fromtimestamp(b['t']/1000,at.tzinfo).date()==day]
    if not day_bars:
        return {'activePreviousDays':None,'previousActivityDate':str(day),'previousActivityError':'Немає історії попереднього торгового дня'}
    result = signal_from_bars(bars,at)
    pm = [b for b in day_bars if 240<=datetime.fromtimestamp(b['t']/1000,at.tzinfo).hour*60+datetime.fromtimestamp(b['t']/1000,at.tzinfo).minute<570]
    volume = result['premarketVolume'] if pm else 0
    active = False if volume is not None and volume<=300000 else result['bandTouch'] if volume is not None else None
    return {'activePreviousDays':active,'previousActivityDate':str(day),'previousPremarketVolume':volume,'previousBandTouch':result['bandTouch']}

def scan_market(tickers, price_min, price_max, volume_min, profile="main"):
    from concurrent.futures import ThreadPoolExecutor
    if profile not in ("main","kush"):
        raise APIError(400, "Невідомий профіль")
    listings = nasdaq_tickers()
    data = api_json('/v2/snapshot/locale/us/markets/stocks/tickers?' + urllib.parse.urlencode({'tickers':tickers}))
    if not isinstance(data.get('tickers'), list):
        raise APIError(502, 'Polygon не надіслав список акцій')
    now = datetime.now(ZoneInfo('America/New_York'))
    now_ms = int(now.timestamp()*1000)
    try:
        prior_day = previous_session(now)
        prior_error = None
    except APIError as err:
        prior_day, prior_error = None, err.message
    rows, candidates = [], []
    start_ms = int(now.replace(hour=4,minute=0,second=0,microsecond=0).timestamp()*1000)
    end_ms = int(now.replace(hour=9,minute=30,second=0,microsecond=0).timestamp()*1000)
    for t in data['tickers']:
        listing = listings.get(t.get('ticker'))
        if not listing:
            continue
        trade, minute, day = t.get('lastTrade') or {}, t.get('min') or {}, t.get('day') or {}
        price_sources=[]
        if isinstance(trade.get('p'),(int,float)) and trade['p']>0 and trade.get('t'):
            price_sources.append((int(trade['t']/1e6),trade['p'],'Остання угода'))
        if isinstance(minute.get('c'),(int,float)) and minute['c']>0 and minute.get('t'):
            price_sources.append((minute['t'],minute['c'],'Остання хвилинна свічка'))
        if price_sources:
            stamp,price,basis=max(price_sources,key=lambda item:item[0])
        else:
            price=day.get('c') if isinstance(day.get('c'),(int,float)) and day['c']>0 else None
            stamp,basis=None,'Денна свічка, час ціни не підтверджено'
        prev = (t.get('prevDay') or {}).get('c')
        min_time = minute.get('t')
        estimate = minute.get('av') if isinstance(min_time,(int,float)) and start_ms <= min_time < end_ms else None
        row = {'symbol':t['ticker'],'exchange':'XNAS','assetType':listing['type'],
            'exchangeProvenance':'Polygon reference primary_exchange', 'price':price,
            'sourceUpdatedAt':stamp,'previousClose':prev,
            'changePercent':(price/prev-1)*100 if price is not None and isinstance(prev,(int,float)) and prev>0 else None,
            'minuteVolume':minute.get('v'),'minuteTimestamp':min_time,
            'premarketVolume':estimate,'volumeProvenance':'Snapshot min.av за премаркет цього дня' if estimate is not None else 'Не підтверджено',
            'upperBand':None,'lowerBand':None,'bandTouch':None,'signalTimestamp':None,
            'dataSource':'polygon','receivedAt':now_ms,
            'priceBasis':basis}
        rows.append(row)
        main_candidate = price is not None and price_min <= price <= price_max and stamp is not None and start_ms <= stamp < end_ms and (estimate is None or estimate > volume_min)
        cumulative = minute.get('av') if isinstance(min_time,(int,float)) and start_ms<=min_time<=now_ms else None
        kush_candidate = profile=='kush' and price is not None and 1.5<=price<=20 and stamp is not None and start_ms<=stamp<=now_ms and (cumulative is None or cumulative>=500000)
        if main_candidate or kush_candidate:
            candidates.append(row)
    def enrich(row):
        try:
            bars = minute_bars(row['symbol'],now.date())
            row.update(signal_from_bars(bars,now))
            high,previous=row.get('premarketHigh'),row.get('previousClose')
            row['premarketGrowthPercent']=(high/previous-1)*100 if isinstance(high,(int,float)) and isinstance(previous,(int,float)) and previous>0 else None
            main_setup = row.get('bandTouch') is True and isinstance(row.get('premarketVolume'),(int,float)) and row['premarketVolume']>volume_min
            kush_setup = profile=='kush' and 1.5<=row['price']<=20 and isinstance(row.get('premarketVolume'),(int,float)) and 500000<=row['premarketVolume']<=10000000
            if main_setup or kush_setup:
                try:
                    row.update(daily_metrics(row['symbol'],now,bars))
                    if prior_day is not None:
                        row.update(previous_activity(bars,prior_day))
                    else:
                        row.update(activePreviousDays=None,previousActivityError=prior_error)
                except APIError as err:
                    row['dailyMetricsError']=err.message
            row['scanStatus']='Перевірено свічки'
        except APIError as err:
            row['scanError']=err.message
            row['scanStatus']='Свічки не отримано'
        return row
    # Exchange and price are verified before per-symbol history requests.
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(copy_context().run, enrich, row) for row in candidates]
        for job in jobs:
            job.result()
    return {'rows':rows,'receivedAt':now_ms,'scanSummary':{
        'snapshotTotal':len(data['tickers']),'nasdaqStocks':len(rows),
        'priceCandidates':sum(r['price'] is not None and price_min<=r['price']<=price_max for r in rows),
        'candleCandidates':len(candidates),'candlesChecked':sum(r.get('scanStatus')=='Перевірено свічки' for r in candidates),
        'candleErrors':sum(bool(r.get('scanError')) for r in candidates),
        'priceVolumeCandidates':sum(r['price'] is not None and price_min<=r['price']<=price_max and isinstance(r.get('premarketVolume'),(int,float)) and r['premarketVolume']>volume_min for r in rows),
        'premarketTouches':sum(r.get('bandTouch') is True for r in candidates)}}

