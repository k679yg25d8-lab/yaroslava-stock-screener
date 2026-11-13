"""Earnings opening-gap scanner and bar-based replay."""
from collections import defaultdict
from datetime import date, datetime, timedelta, time
from functools import lru_cache
from pathlib import Path
from statistics import median
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from server import APIError, api_json

NY = ZoneInfo('America/New_York')
MINUTE = 60_000
ROOT = Path(__file__).resolve().parent
STRATEGIES = ('or10', 'or10_volume', 'm2_touch', 'm2_close')


@lru_cache(maxsize=1)
def watchlist():
    return [dict(zip(('date', 'symbol'), line.split())) for line in
            (ROOT / 'earnings_2026_500k.txt').read_text().splitlines() if line.strip()]


def request(path, **params):
    url = f'https://api.massive.com{path}?{urlencode(params)}'
    seen = set()
    while url:
        if url in seen or len(seen) >= 20:
            raise APIError(502, 'Неповна відповідь Massive')
        seen.add(url)
        data = api_json(url)
        yield from data.get('results') or []
        url = data.get('next_url')


@lru_cache(maxsize=64)
def sessions(day):
    start = day - timedelta(days=50)
    end = min(day + timedelta(days=10), datetime.now(NY).date())
    bars = request(f'/v2/aggs/ticker/SPY/range/1/day/{start}/{end}',
                   adjusted='true', sort='asc', limit=100)
    return sorted({datetime.fromtimestamp(b['t'] / 1000, NY).date() for b in bars})


def releases(first, last, symbol=None):
    params = {'date.gte': first, 'date.lte': last, 'limit': 1000}
    if symbol:
        params['ticker'] = symbol
    for item in request('/benzinga/v1/earnings', **params):
        if item.get('date_status') != 'confirmed' or all(
                item.get(field) is None for field in ('actual_eps', 'actual_revenue')):
            continue
        try:
            published = datetime.fromisoformat(f"{item['date']}T{item['time']}").replace(tzinfo=NY)
        except (ValueError, KeyError):
            continue
        if published.time() == time(0):
            continue
        yield item['ticker'], published


def opening(day):
    return datetime.combine(day, time(9, 30), NY)


def belongs(published, previous, day):
    return datetime.combine(previous, time(16), NY) <= published < opening(day)


def bars(symbol, frame, first, last):
    multiplier, span = frame
    return list(request(f'/v2/aggs/ticker/{symbol}/range/{multiplier}/{span}/{first}/{last}',
                        adjusted='true', sort='asc', limit=50000))


def m2_openings(symbol, prior, day):
    start = int(opening(prior[0]).timestamp() * 1000)
    end = int(opening(day).timestamp() * 1000) + 60 * MINUTE - 1
    wanted = set(prior) | {day}
    grouped = defaultdict(dict)
    for bar in bars(symbol, (2, 'minute'), start, end):
        dt = datetime.fromtimestamp(bar['t'] / 1000, NY)
        if dt.date() not in wanted:
            continue
        delta = (bar['t'] - int(opening(dt.date()).timestamp() * 1000)) // MINUTE
        if 0 <= delta < (60 if dt.date() == day else 10) and delta % 2 == 0:
            grouped[dt.date()][delta // 2] = bar
    return grouped


def close_before(symbol, previous):
    daily = bars(symbol, (1, 'day'), previous - timedelta(days=10), previous)
    return daily[-1]['c'] if daily and datetime.fromtimestamp(daily[-1]['t'] / 1000, NY).date() == previous else None


def market_spread(symbol, now):
    start = int((now - timedelta(minutes=2)).timestamp() * 1e9)
    try:
        path = f'https://api.massive.com/v3/quotes/{symbol}?'
        params = urlencode({'timestamp.gte': start, 'sort': 'timestamp', 'order': 'desc', 'limit': 1})
        quotes = (api_json(path + params).get('results') or [])
    except APIError as exc:
        if exc.status in (403, 404):
            return None
        raise
    if not quotes:
        return None
    bid, ask = quotes[0].get('bid_price'), quotes[0].get('ask_price')
    return ask - bid if bid and ask and ask >= bid else None


def read_inputs(symbol, day, calendar):
    prior = [d for d in calendar if d < day][-20:]
    if len(prior) < 20:
        return None, None, None
    grouped = m2_openings(symbol, prior, day)
    history = [sum(b['v'] for b in grouped[d].values()) for d in prior]
    baseline = median(history) if all(history) else None
    return grouped[day], baseline, close_before(symbol, prior[-1])


def evaluate(bars_by_slot, baseline, count, threshold=2):
    available = [(i, bars_by_slot[i]) for i in sorted(bars_by_slot) if i < count]
    turnover = sum(b['v'] * b.get('vw', b['c']) for _, b in available)
    state = dict(stage='waiting', turnover=turnover, rvol=None, level=None, stop=None,
                 green=None, entries={}, last=available[-1][1]['c'] if available else None)
    if count < 5 or any(i not in bars_by_slot for i in range(5)):
        return state
    impulse = [bars_by_slot[i] for i in range(5)]
    volume = sum(b['v'] for b in impulse)
    rvol = volume / baseline if baseline else None
    state['rvol'] = rvol
    impulse_high = max(b['h'] for b in impulse)
    impulse_low = min(b['l'] for b in impulse)
    valid = bool(rvol is not None and rvol >= threshold and impulse[-1]['c'] > impulse[0]['o'])
    state['stage'] = 'impulse' if valid else 'no_impulse'
    entries = state['entries']
    base = None
    total_volume = volume
    total_value = sum(b['v'] * b.get('vw', b['c']) for b in impulse)
    vwaps = {}
    for slot, bar in available:
        if slot < 5:
            continue
        if bar['h'] > impulse_high:
            entries.setdefault('or10', (slot, max(bar['o'], impulse_high), impulse_low))
            if rvol is not None and rvol >= threshold:
                entries.setdefault('or10_volume', (slot, max(bar['o'], impulse_high), impulse_low))
        if not valid:
            continue
        if base and slot == base[0] + 1:
            _, high, low, green = base
            if bar['h'] > high:
                entries.setdefault('m2_touch', (slot, max(bar['o'], high), low))
            if bar['c'] > high and slot + 1 < count and slot + 1 in bars_by_slot:
                entries.setdefault('m2_close', (slot + 1, bars_by_slot[slot + 1]['o'], low))
            if bar['c'] > high and state['stage'] != 'breakout':
                state.update(stage='breakout', level=high, stop=low, green=green)
            elif bar['h'] > high and state['stage'] not in ('touched', 'breakout'):
                state.update(stage='touched', level=high, stop=low, green=green)
            elif bar['l'] < low and state['stage'] == 'base':
                state.update(stage='impulse', level=None, stop=None, green=None)
        if slot + 1 not in bars_by_slot and slot < count - 1:
            base = None
        total_volume += bar['v']
        total_value += bar['v'] * bar.get('vw', bar['c'])
        vwaps[slot] = total_value / total_volume
        base = None
        if state['stage'] == 'base':
            state.update(stage='impulse', level=None, stop=None, green=None)
        for size in range(6, 2, -1):
            indices = range(slot - size + 1, slot + 1)
            if any(i not in vwaps for i in indices):
                continue
            window = [bars_by_slot[i] for i in indices]
            low = min(b['l'] for b in window)
            if low < impulse_high - (impulse_high - impulse_low) / 2:
                continue
            if any(b['l'] < vwaps[i] for i, b in zip(indices, window)):
                continue
            if sum(b['v'] for b in window) / size >= volume / 5:
                continue
            high = max(b['h'] for b in window)
            green = sum(b['c'] > b['o'] for b in window) / size
            base = (slot, high, low, green)
            if state['stage'] not in ('touched', 'breakout'):
                state.update(stage='base', level=high, stop=low, green=green)
            break
    return state


def simulate(entries, bars_by_slot, spread_bps, slip_bps):
    result = {}
    cost = (spread_bps / 2 + slip_bps) / 10000
    for name, (slot, price, stop) in entries.items():
        entry = price * (1 + cost)
        risk = entry - stop
        if price <= stop or 29 not in bars_by_slot:
            continue
        exit_price = bars_by_slot[29]['c']
        for i in range(slot, 30):
            bar = bars_by_slot.get(i)
            if bar and bar['l'] <= stop:
                exit_price = stop if i == slot and name != 'm2_close' else min(stop, bar['o'])
                break
        result[name] = round((exit_price * (1 - cost) - entry) / risk, 4)
    return result


def replay(event, threshold, spread_bps, slip_bps):
    symbol = event['symbol']
    report_day = date.fromisoformat(event['date'])
    calendar = sessions(report_day)
    candidates = [d for d in calendar if d >= report_day][:2]
    for _, published in sorted(releases(report_day, report_day, symbol), key=lambda x: x[1]):
        for day in candidates:
            index = calendar.index(day)
            if not index or not belongs(published, calendar[index - 1], day):
                continue
            if datetime.now(NY) < opening(day) + timedelta(hours=1):
                return dict(event, status='pending')
            series, baseline, previous = read_inputs(symbol, day, calendar)
            if not series or not baseline or not previous or any(i not in series for i in (*range(5), 29)):
                return dict(event, status='missing_bars')
            result = evaluate(series, baseline, 30, threshold)
            return dict(event, status='ok', session=str(day), release=published.isoformat(),
                        gap=100 * (series[0]['o'] / previous - 1), rvol=result['rvol'],
                        results=simulate(result['entries'], series, spread_bps, slip_bps))
    return dict(event, status='unconfirmed')


def live_universe(now):
    day = now.date()
    calendar = sessions(day - timedelta(days=1))
    past = [d for d in calendar if d < day]
    if not past or day.weekday() >= 5 or (now >= opening(day) + timedelta(hours=7) and day not in calendar):
        return []
    previous = past[-1]
    confirmed = {}
    for symbol, published in releases(previous, day):
        if published <= now and belongs(published, previous, day):
            confirmed[symbol] = min(published, confirmed.get(symbol, published))
    return [dict(symbol=symbol, release=published.isoformat())
            for symbol, published in sorted(confirmed.items())]


def live_row(event, now, threshold):
    day = now.date()
    opened = opening(day)
    count = max(0, min(30, int((now - opened).total_seconds() // 120)))
    if not count:
        return dict(event, stage='waiting')
    series, baseline, previous = read_inputs(event['symbol'], day, sessions(day))
    if series is None:
        return dict(event, stage='missing_bars')
    state = evaluate(series, baseline, count, threshold)
    spread = market_spread(event['symbol'], now) if count < 30 else None
    return dict(event, **{k: v for k, v in state.items() if k != 'entries'},
                gap=100 * (series[0]['o'] / previous - 1) if previous and 0 in series else None,
                spread=spread, closed=count == 30, stale=count - 1 not in series,
                signalTime=(opened + timedelta(minutes=2 * count)).isoformat())


def summarize(rows):
    valid = sorted((r for r in rows if r['status'] == 'ok'), key=lambda r: (r['session'], r['symbol']))
    report = []
    for split, subset in (('До вересня', [r for r in valid if r['session'] < '2026-09-01']),
                          ('Вересень і далі', [r for r in valid if r['session'] >= '2026-09-01']),
                          ('Усі дати', valid)):
        for name in STRATEGIES:
            results = [r['results'][name] for r in subset if name in r['results']]
            running = peak = drawdown = 0
            for r in results:
                running += r
                peak = max(peak, running)
                drawdown = max(drawdown, peak - running)
            report.append(dict(split=split, strategy=name, count=len(results),
                               mean=running / len(results) if results else None,
                               drawdown=drawdown if results else None))
    return report
