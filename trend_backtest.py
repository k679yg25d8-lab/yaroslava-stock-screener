"""Chronological cash portfolio simulation; signals restricted to CSV dates."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math

def date_of(bar):
    return datetime.fromtimestamp(bar['t']/1000,ZoneInfo('America/New_York')).date().isoformat()

def features(bars):
    out={};atr=None;trs=[];total=0
    for i,b in enumerate(bars):
        total+=b['c']
        if i>=200:total-=bars[i-200]['c']
        if i:
            tr=max(b['h']-b['l'],abs(b['h']-bars[i-1]['c']),abs(b['l']-bars[i-1]['c']))
            trs.append(tr)
            if len(trs)==14:atr=sum(trs)/14
            elif len(trs)>14:atr=(atr*13+tr)/14
        if i>=199:
            out[date_of(b)]={'bar':b,'sma':total/200,'atr':atr,'return':b['c']/bars[i-126]['c']-1,'baseDate':date_of(bars[i-126]),
                'high20':max(x['h'] for x in bars[i-20:i]),'low10':min(x['l'] for x in bars[i-10:i]),
                'avgVol':sum(x['v'] for x in bars[i-19:i+1])/20,
                'avgDollar':sum(x['v']*x['c'] for x in bars[i-19:i+1])/20}
    return out

def simulate(series,benchmark,allowed,settings,prepared=False):
    index=features(benchmark);stock=series if prepared else {s:features(b) for s,b in series.items()};days=sorted(d for d in index if settings['start']<=d<=settings['end'])
    cash=settings['capital'];initial=cash;positions={};pending={};trades=[];curve=[];peak=initial;max_dd=0
    fee=settings['fee'];slip=settings['slippage']/100
    def close(symbol,price,day,reason):
        nonlocal cash
        p=positions.pop(symbol);price=max(0,price*(1-slip));cash+=p['shares']*price-fee
        pnl=(price-p['entry'])*p['shares']-2*fee
        trades.append({**p,'symbol':symbol,'exit':price,'exitDate':day,'reason':reason,'pnl':pnl,'r':pnl/p['riskUSD'] if p['riskUSD'] else 0})
    previous=None
    for day in days:
        # Execute only on the very next benchmark session, never a later available bar.
        for symbol,p in list(positions.items()):
            today=stock[symbol].get(day)
            if not today:continue
            if p.pop('exitNext',False):close(symbol,today['bar']['o'],day,'Мінімум 10 сесій');continue
            if today['bar']['o']<=p['stop']:close(symbol,today['bar']['o'],day,'Геп за стоп');continue
            if today['bar']['l']<=p['stop']:close(symbol,p['stop'],day,'Стоп 2 ATR')
        for symbol,signal in sorted(pending.items(),key=lambda item:(-item[1]['relative'],item[0])):
            today=stock[symbol].get(day)
            if symbol in positions or not today:continue
            entry=today['bar']['o']*(1+slip);risk=2*signal['atr'];stop=entry-risk
            if risk<=0 or stop<=0:continue
            equity=cash+sum(p['shares']*(stock[s].get(previous,{}).get('bar',{}).get('c',p['entry'])) for s,p in positions.items())
            shares=max(0,min(math.floor(max(0,equity)*.005/risk),math.floor(max(0,cash-2*fee)/(entry))))
            if not shares:continue
            cash-=entry*shares+fee
            positions[symbol]={'entry':entry,'entryDate':day,'signalDate':signal['date'],'stop':stop,'shares':shares,'riskUSD':shares*risk}
            if today['bar']['l']<=stop:close(symbol,min(today['bar']['o'],stop),day,'Стоп у день входу')
        pending={}
        market=index[day]['bar']['c']>index[day]['sma']
        for symbol,data in stock.items():
            f=data.get(day)
            if not f:continue
            if symbol in positions:
                positions[symbol]['exitNext']=f['bar']['c']<f['low10']
                positions[symbol]['lastClose']=f['bar']['c'];positions[symbol]['lastDate']=day
            elif market and day in allowed.get(symbol,set()) and f['baseDate']==index[day]['baseDate'] and f['bar']['c']>f['sma'] and f['return']>index[day]['return'] and f['bar']['c']>f['high20'] and f['avgVol']>=settings['minVolume'] and f['avgDollar']>=settings['minDollar']:
                pending[symbol]={'atr':f['atr'],'date':day,'relative':f['return']-index[day]['return']}
        equity=cash+sum(p['shares']*p.get('lastClose',p['entry']) for p in positions.values());peak=max(peak,equity);dd=peak-equity;max_dd=max(max_dd,dd)
        curve.append({'date':day,'equity':equity,'profit':equity-initial,'drawdown':dd});previous=day
    # Open positions remain open at the test boundary; unrealized P&L is shown separately.
    wins=[t for t in trades if t['pnl']>0];losses=[t for t in trades if t['pnl']<0]
    return {'trades':trades,'curve':curve,'openPositions':[{'symbol':s,**p} for s,p in positions.items()],
        'net':sum(t['pnl'] for t in trades),'grossProfit':sum(t['pnl'] for t in wins),'grossLoss':sum(t['pnl'] for t in losses),
        'winPercent':len(wins)/len(trades)*100 if trades else 0,'lossPercent':len(losses)/len(trades)*100 if trades else 0,
        'breakeven':len(trades)-len(wins)-len(losses),'maxDrawdown':max_dd,'equityProfit':curve[-1]['profit'] if curve else 0,'settings':settings}
