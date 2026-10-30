"""Reconcile saved daily signals against completed sessions; never invent a fill."""
from collections import defaultdict
import math
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import server as engine
import swing
import trend_backtest as model

def session_bounds(day):
    start=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=ZoneInfo('America/New_York'),hour=9,minute=30)
    # Nasdaq 2026 official early closes: nasdaqtrader.com/Trader.aspx?id=Calendar
    end=start.replace(hour=13 if day in ('2026-11-27','2026-12-24') else 16,minute=0)
    return int(start.timestamp()*1000),int(end.timestamp()*1000)

def regular_bars(symbol,day):
    start,end=session_bounds(day)
    data=engine.api_json(f'/v2/aggs/ticker/{engine.urllib.parse.quote(symbol)}/range/1/minute/{start}/{end-1}?adjusted=true&sort=asc&limit=1000')
    if data.get('next_url'):raise engine.APIError(422,'Неповна хвилинна історія сесії')
    return [b for b in data.get('results') or [] if start<=b['t']<end]

def resolve_exit(trade,bars,day,slippage):
    """First known crossing wins; unknown order inside one minute uses stop first."""
    for b in bars:
        stop=b['l']<=trade['stop'];take=b['h']>=trade['target']
        if b['o']<=trade['stop']:price=b['o'];reason='Стоп 1 ATR';ambiguous=False
        elif b['o']>=trade['target']:price=trade['target'];reason='Тейк 6R';ambiguous=False
        elif stop:price=trade['stop'];reason='Обидва рівні за хвилину: консервативно стоп' if take else 'Стоп 1 ATR';ambiguous=take
        elif take:price=trade['target'];reason='Тейк 6R';ambiguous=False
        else:continue
        return dict(exit=price*(1-slippage/100),exitTimestamp=b['t'],reason=reason,ambiguousBar=ambiguous)
    _,end=session_bounds(day)
    if bars and bars[-1]['t']==end-60000:
        return dict(exit=bars[-1]['c']*(1-slippage/100),exitTimestamp=end,reason='Кінець основної сесії',ambiguousBar=False)
    return None

def validate(records):
    if not isinstance(records,list) or not 1<=len(records)<=5000:raise ValueError('Потрібно від 1 до 5000 збережених сигналів')
    unique={}
    for item in records:
        symbol=item.get('symbol','');day=item.get('day','');raw=item.get('settings') or {}
        if not re.fullmatch(r'[A-Z0-9._-]{1,20}',symbol) or not re.fullmatch(r'2026-\d{2}-\d{2}',day):raise ValueError('Некоректний збережений сигнал')
        datetime.strptime(day,'%Y-%m-%d');settings={}
        for name,default,low,high in [('capital',10000,100,1e9),('riskUSD',0,0,1e8),('minVolume',500000,0,1e10),('minDollar',10000000,0,1e12)]:
            value=float(raw.get(name,default))
            if not math.isfinite(value) or not low<=value<=high:raise ValueError('Некоректний профіль сигналу')
            settings[name]=value
        unique[day+':'+symbol]={'symbol':symbol,'day':day,'settings':settings}
    if len({r['symbol'] for r in unique.values()})>1000:raise ValueError('До 1000 різних акцій в одному автоматичному журналі')
    return sorted(unique.values(),key=lambda r:(r['day'],r['symbol']))

def settle(records,now,progress=lambda done,total:None):
    benchmark=swing.daily('SPY',now);index=model.features(benchmark);days=sorted(index)
    if not days:raise ValueError('Немає повної історії SPY для денного відбору')
    prepared={};errors=[];symbols=sorted({r['symbol'] for r in records})
    ready_days=set(days[:-1]);to_fetch=sorted({r['symbol'] for r in records if r['day'] in ready_days})
    for i,symbol in enumerate(to_fetch):
        try:
            prepared[symbol]=model.features(swing.daily(symbol,now))
            if not prepared[symbol]:raise ValueError('Недостатньо історії для SMA 200')
        except (engine.APIError,ValueError) as exc:
            if isinstance(exc,engine.APIError) and exc.status in (401,403,429):raise
            prepared.pop(symbol,None)
            errors.append({'symbol':symbol,'error':getattr(exc,'message',str(exc))})
        progress(i+1,len(to_fetch))
    grouped=defaultdict(list)
    for r in records:grouped[r['day']].append(r)
    initial=records[0]['settings']['capital'];balance=initial;trades=[];pending=[];cost_skips=0;profile_days=[]
    unresolved_balance=False
    for day,items in sorted(grouped.items()):
        if unresolved_balance:
            pending.extend({'symbol':r['symbol'],'signalDate':day,'reason':'Очікується підтвердження попередніх угод портфеля'} for r in items);continue
        if day not in days or days.index(day)+1>=len(days):
            pending.extend({'symbol':r['symbol'],'signalDate':day,'reason':'Очікується завершена наступна сесія'} for r in items);continue
        entry_day=days[days.index(day)+1]
        ready=[];minutes={};execution={}
        for r in items:
            symbol=r['symbol']
            try:
                if entry_day not in prepared.get(symbol,{}):raise ValueError('Немає підтвердженої свічки дня входу')
                intraday=regular_bars(symbol,entry_day);start,_=session_bounds(entry_day)
                if not intraday or intraday[0]['t']!=start:raise ValueError('Немає хвилини відкриття 09:30 ET')
                minutes[symbol]=intraday
                execution[symbol]=dict(prepared[symbol])
                execution[symbol][entry_day]={**prepared[symbol][entry_day],'bar':{**prepared[symbol][entry_day]['bar'],'o':intraday[0]['o'],'h':max(b['h'] for b in intraday),'l':min(b['l'] for b in intraday),'c':intraday[-1]['c']}}
                ready.append(r)
            except (engine.APIError,ValueError) as exc:
                if isinstance(exc,engine.APIError) and exc.status in (401,403,429):raise
                pending.append({'symbol':symbol,'signalDate':day,'reason':getattr(exc,'message',str(exc))});unresolved_balance=True
        if not ready:continue
        # One cash account, carried between days; one saved profile governs each signal day.
        profile=items[0]['settings'];settings={**profile,'capital':balance,'fee':1,'slippage':.1,'start':day,'end':entry_day}
        allowed=defaultdict(set)
        for r in ready:allowed[r['symbol']].add(day)
        result=model.simulate(execution,benchmark,allowed,settings,prepared=True)
        for trade in result['trades']:
            exit_fill=resolve_exit(trade,minutes[trade['symbol']],entry_day,settings['slippage'])
            if not exit_fill:
                pending.append({'symbol':trade['symbol'],'signalDate':day,'reason':'Немає підтвердженого виходу або останньої хвилини сесії'});unresolved_balance=True;continue
            trade.update(exit_fill)
            trade['pnl']=(trade['exit']-trade['entry'])*trade['shares']-2*settings['fee'];trade['r']=trade['pnl']/trade['riskUSD']
            trades.append(trade);balance+=trade['pnl']
        cost_skips+=result['costSkips']
        profile_days.append({'signalDate':day,'riskUSD':profile['riskUSD'],'minVolume':profile['minVolume'],'minDollar':profile['minDollar']})
    totals=defaultdict(float)
    for t in trades:totals[t['exitDate']]+=t['pnl']
    equity=initial;peak=initial;max_dd=0;curve=[]
    for day in days:
        if day<records[0]['day']:continue
        equity+=totals[day];peak=max(peak,equity);dd=peak-equity;max_dd=max(max_dd,dd)
        curve.append({'date':day,'equity':equity,'profit':equity-initial,'drawdown':dd})
    wins=[t for t in trades if t['pnl']>0];losses=[t for t in trades if t['pnl']<0]
    return {'trades':trades,'curve':curve,'openPositions':[],'pending':pending,'costSkips':cost_skips,
        'net':sum(t['pnl'] for t in trades),'grossProfit':sum(t['pnl'] for t in wins),'grossLoss':sum(t['pnl'] for t in losses),
        'winPercent':100*len(wins)/len(trades) if trades else 0,'lossPercent':100*len(losses)/len(trades) if trades else 0,
        'breakeven':len(trades)-len(wins)-len(losses),'maxDrawdown':max_dd,'equityProfit':equity-initial,
        'coverage':{'loaded':len(prepared),'requested':len(symbols),'errors':errors},
        'settings':{'mode':'market-auto','executionData':'RTH-minute','strategyVersion':'day-atr1-take6R-cap1.4','capital':initial,'riskUSD':records[0]['settings']['riskUSD'],
            'fee':1,'slippage':.1,'start':records[0]['day'],'end':days[-1],'profiles':profile_days}}
