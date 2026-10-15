"""Deterministic minute-bar short simulation; no future bars in entry levels."""
from datetime import datetime
from zoneinfo import ZoneInfo
import math
ZONE=ZoneInfo('America/New_York')
def simulate(bars, previous_close, day):
    pmh=None; volume=0; last=None; trade=None; last_bar=None; flat_bars=[]; closes=[]; band_touch=None
    for b in sorted(bars,key=lambda x:x['t']):
        dt=datetime.fromtimestamp(b['t']/1000,ZONE)
        if dt.date().isoformat()!=day:continue
        minute=dt.hour*60+dt.minute
        if minute<240:continue
        if minute>=600:
            if trade and not trade.get('exit') and minute==600:
                trade.update(exit=b['o'],exitTime=b['t'],reason='10:00 ET')
            break
        if trade:
            if not trade.get('exit'):
                active_stop=trade['entry'] if trade.get('breakeven') else trade['stop']
                if b['h']>=active_stop:trade.update(exit=max(b['o'],active_stop),exitTime=b['t'],reason='Стоп БУ' if trade.get('breakeven') else 'Стоп',stopAtExit=active_stop)
                elif b['l']<=trade['target']:trade.update(exit=trade['target'],exitTime=b['t'],reason='Ціль 3,7R')
                else:
                    # Five complete consecutive minute bars after the entry candle.
                    if flat_bars and b['t']-flat_bars[-1]['t']!=60000:flat_bars=[]
                    flat_bars=(flat_bars+[b])[-5:]
                    if len(flat_bars)==5:
                        width=max(x['h'] for x in flat_bars)-min(x['l'] for x in flat_bars)
                        limit=(trade['stop']-trade['entry'])*.1
                        if width<=limit or math.isclose(width,limit,rel_tol=1e-10,abs_tol=1e-12):
                            trade.update(exit=b['c'],exitTime=b['t']+60000,exitBarTime=b['t'],reason='5 хв у діапазоні',flatRange=width,flatRangeLimit=limit)
            if not trade.get('exit') and not trade.get('breakeven') and b['l']<=trade['entry']-2*(trade['stop']-trade['entry']):
                trade.update(breakeven=True,breakevenTime=b['t']+60000)
            last_bar=b
            continue
        if minute>=570:continue
        volume+=b['v']
        # Levels use only completed bars before this minute.
        if pmh and previous_close>0:
            entry=previous_close+(pmh-previous_close)*.25
            if (360<=minute<570 and (pmh/previous_close-1)*100>20+1e-9 and band_touch is not None and 1000<=volume-b['v']<=10000000
                and 0<b['o']<=20 and .5<=entry<=15 and last is not None and last>entry and b['o']>=entry and b['l']<=entry):
                stop=entry*1.1;target=entry-(stop-entry)*3.7
                shares=min(100000,math.floor(175/(stop-entry)))
                trade=dict(day=day,entry=entry,stop=stop,target=target,shares=shares,entryTime=b['t'],premarketVolume=volume-b['v'],pmHigh=pmh,previousClose=previous_close,bandTouchTime=band_touch['time'],bandTouchHigh=band_touch['high'],bandTouchUpper=band_touch['upper'],growthPercent=(pmh/previous_close-1)*100)
                # Intrabar sequence unknown: allow adverse stop, never same-minute target.
                if b['h']>=stop:trade.update(exit=stop,exitTime=b['t'],reason='Стоп у свічці входу')
                elif b['l']<=entry-2*(stop-entry):trade.update(breakeven=True,breakevenTime=b['t']+60000)
        # A completed touch must precede the entry minute. SMA includes the touch candle close.
        closes=(closes+[b['c']])[-10:]
        if len(closes)==10:
            upper=sum(closes)/10*1.1
            if b['h']>=upper and band_touch is None:
                band_touch=dict(time=b['t'],high=b['h'],upper=upper)
        pmh=max(pmh or b['h'],b['h']);last=b['c'];last_bar=b
    if trade:
        if not trade.get('exit'):
            trade.update(reason='Немає свічки 10:00 — результат невідомий',pnl=None,r=None)
        else:
            trade['pnl']=round((trade['entry']-trade['exit'])*trade['shares'],2)
            trade['r']=round((trade['entry']-trade['exit'])/(trade['stop']-trade['entry']),3)
    return trade
