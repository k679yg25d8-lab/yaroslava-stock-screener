"""Deterministic minute-bar short simulation; no future bars in entry levels."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
ZONE=ZoneInfo('America/New_York')
def simulate(bars, previous_close, day):
    pmh=None; volume=0; last=None; trade=None; last_bar=None
    for b in sorted(bars,key=lambda x:x['t']):
        dt=datetime.fromtimestamp(b['t']/1000,ZONE)
        if dt.date().isoformat()!=day:continue
        minute=dt.hour*60+dt.minute
        if minute<240:continue
        if minute>=600:
            if trade and not trade.get('exit'):
                trade.update(exit=b['o'],exitTime=b['t'],reason='10:00 ET')
            break
        if trade:
            if not trade.get('exit'):
                if b['h']>=trade['stop']:trade.update(exit=max(b['o'],trade['stop']),exitTime=b['t'],reason='Стоп')
                elif b['l']<=trade['target']:trade.update(exit=trade['target'],exitTime=b['t'],reason='Ціль 3,7R')
            last_bar=b
            continue
        if minute>=570:continue
        volume+=b['v']
        # Levels use only completed bars before this minute.
        if pmh and previous_close>0:
            entry=previous_close+(pmh-previous_close)*.25
            if (360<=minute<570 and pmh>=previous_close*1.25 and 500000<=volume-b['v']<=10000000
                and 0<b['o']<=20 and .5<=entry<=15 and last is not None and last>entry and b['o']>=entry and b['l']<=entry):
                stop=entry*1.1;target=entry-(stop-entry)*3.7
                shares=min(100000,math.floor(175/(stop-entry)))
                trade=dict(day=day,entry=entry,stop=stop,target=target,shares=shares,entryTime=b['t'],premarketVolume=volume-b['v'],pmHigh=pmh,previousClose=previous_close)
                # Intrabar sequence unknown: allow adverse stop, never same-minute target.
                if b['h']>=stop:trade.update(exit=max(b['o'],stop),exitTime=b['t'],reason='Стоп у свічці входу')
        pmh=max(pmh or b['h'],b['h']);last=b['c'];last_bar=b
    if trade:
        if not trade.get('exit'):
            trade.update(reason='Немає свічки 10:00 — результат невідомий',pnl=None,r=None)
        else:
            trade['pnl']=round((trade['entry']-trade['exit'])*trade['shares'],2)
            trade['r']=round((trade['entry']-trade['exit'])/(trade['stop']-trade['entry']),3)
    return trade
