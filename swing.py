"""Daily, long-only trend screen; all calculations use completed daily bars."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import server as engine

def completed(bars, now):
    today = now.date()
    return [b for b in bars if datetime.fromtimestamp(b['t']/1000, ZoneInfo('America/New_York')).date() < today
            or (datetime.fromtimestamp(b['t']/1000, ZoneInfo('America/New_York')).date() == today and (now.hour, now.minute) >= (16,15))]

def daily(symbol, now):
    start = min(now.date()-timedelta(days=550), datetime(2025,1,1).date())
    data=engine.api_json(f'/v2/aggs/ticker/{engine.urllib.parse.quote(symbol)}/range/1/day/{start}/{now.date()}?adjusted=true&sort=asc&limit=50000')
    if data.get('next_url'):raise engine.APIError(422,'Неповна денна історія')
    return completed(data.get('results') or [],now)

def metrics(bars, benchmark):
    if len(bars)<201 or len(benchmark)<201:raise ValueError('Недостатньо 201 денних свічок')
    # Require matching dates: stale/halted stocks must not produce current signals.
    if bars[-1]['t']!=benchmark[-1]['t']:raise ValueError('Немає свічки останньої завершеної сесії')
    aligned={b['t']:b for b in bars}
    if benchmark[-127]['t'] not in aligned:raise ValueError('Немає даних для порівняння за 126 сесій')
    last=bars[-1];close=last['c'];sma=sum(b['c'] for b in bars[-200:])/200
    index_sma=sum(b['c'] for b in benchmark[-200:])/200
    stock_return=close/aligned[benchmark[-127]['t']]['c']-1
    index_return=benchmark[-1]['c']/benchmark[-127]['c']-1
    ranges=[max(b['h']-b['l'],abs(b['h']-bars[i-1]['c']),abs(b['l']-bars[i-1]['c'])) for i,b in enumerate(bars) if i]
    atr=sum(ranges[:14])/14
    for tr in ranges[14:]:atr=(atr*13+tr)/14
    high20=max(b['h'] for b in bars[-21:-1]);low10=min(b['l'] for b in bars[-11:-1])
    avg_volume=sum(b['v'] for b in bars[-20:])/20
    avg_dollar=sum(b['v']*b['c'] for b in bars[-20:])/20
    return dict(close=close,sma200=sma,atr=atr,high20=high20,low10=low10,
                avgVolume=avg_volume,avgDollar=avg_dollar,relativePercent=(stock_return-index_return)*100,
                marketOK=benchmark[-1]['c']>index_sma,trendOK=close>sma,
                strengthOK=stock_return>index_return,breakoutOK=close>high20,
                exitSignal=close<low10,date=datetime.fromtimestamp(last['t']/1000,ZoneInfo('America/New_York')).date().isoformat(),bars=bars[-90:])

def universe(limit):
    listings=engine.nasdaq_tickers()
    data=engine.api_json('/v2/snapshot/locale/us/markets/stocks/tickers')
    if not isinstance(data.get('tickers'),list):raise engine.APIError(502,'Не отримано список акцій')
    ranked=[]
    for item in data['tickers']:
        if item.get('ticker') not in listings:continue
        day=item.get('prevDay') or {}
        turnover=(day.get('v') or 0)*(day.get('c') or 0)
        if turnover>0:ranked.append((turnover,item['ticker']))
    ranked.sort(reverse=True)
    return dict(symbols=[s for _,s in ranked[:limit]],available=len(ranked),limit=limit)
