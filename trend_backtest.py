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
    index=features(benchmark);stock=series if prepared else {s:features(b) for s,b in series.items()}
    days=sorted(d for d in index if settings["start"]<=d<=settings["end"])
    cash=settings["capital"];initial=cash;pending={};trades=[];curve=[];peak=initial;max_dd=0;cost_skips=0
    fee=settings["fee"];slip=settings["slippage"]/100
    for day in days:
        # Allocate ALL opening orders before closing any trade: no reuse of future profits.
        opening_equity=cash;positions=[]
        for symbol,signal in sorted(pending.items(),key=lambda item:(-item[1]["relative"],item[0])):
            today=stock[symbol].get(day)
            if not today:continue
            bar=today["bar"];entry=bar["o"]*(1+slip);risk=signal["atr"];stop=entry-risk
            if risk<=0 or stop<=0:continue
            budget=settings.get("riskUSD",0) or opening_equity*.005
            shares=max(0,min(math.floor(max(0,budget)/risk),math.floor(max(0,cash-2*fee)/entry)))
            if not shares:continue
            actual_risk=shares*risk
            estimated_loss=(entry-min(bar["o"],stop)*(1-slip))*shares+2*fee
            if estimated_loss>actual_risk*1.4+1e-8:
                cost_skips+=1;continue
            cash-=entry*shares+fee
            positions.append((symbol,bar,{"entry":entry,"entryDate":day,"signalDate":signal["date"],"stop":stop,"target":entry+6*risk,"shares":shares,"riskUSD":actual_risk,"requestedRiskUSD":budget,"maxLossUSD":actual_risk*1.4,"estimatedStopLoss":estimated_loss}))
        for symbol,bar,p in positions:
            stop_hit=bar["l"]<=p["stop"];target_hit=bar["h"]>=p["target"]
            if stop_hit:
                price=min(bar["o"],p["stop"]);reason="Стоп 1 ATR" if not target_hit else "Обидва рівні за день: консервативно стоп"
            elif target_hit:price=p["target"];reason="Тейк 6R"
            else:price=bar["c"];reason="Кінець основної сесії"
            price=max(0,price*(1-slip));cash+=p["shares"]*price-fee
            pnl=(price-p["entry"])*p["shares"]-2*fee
            trades.append({**p,"symbol":symbol,"exit":price,"exitDate":day,"reason":reason,"pnl":pnl,"r":pnl/p["riskUSD"],"ambiguousBar":stop_hit and target_hit})
        pending={};market=index[day]["bar"]["c"]>index[day]["sma"]
        for symbol,data in stock.items():
            f=data.get(day)
            if f and market and day in allowed.get(symbol,set()) and f["baseDate"]==index[day]["baseDate"] and f["bar"]["c"]>f["sma"] and f["return"]>index[day]["return"] and f["bar"]["c"]>f["high20"] and f["avgVol"]>=settings["minVolume"] and f["avgDollar"]>=settings["minDollar"]:
                pending[symbol]={"atr":f["atr"],"date":day,"relative":f["return"]-index[day]["return"]}
        peak=max(peak,cash);dd=peak-cash;max_dd=max(max_dd,dd)
        curve.append({"date":day,"equity":cash,"profit":cash-initial,"drawdown":dd})
    wins=[t for t in trades if t["pnl"]>0];losses=[t for t in trades if t["pnl"]<0]
    return {"trades":trades,"curve":curve,"openPositions":[],"costSkips":cost_skips,
        "net":sum(t["pnl"] for t in trades),"grossProfit":sum(t["pnl"] for t in wins),"grossLoss":sum(t["pnl"] for t in losses),
        "winPercent":len(wins)/len(trades)*100 if trades else 0,"lossPercent":len(losses)/len(trades)*100 if trades else 0,
        "breakeven":len(trades)-len(wins)-len(losses),"maxDrawdown":max_dd,"equityProfit":curve[-1]["profit"] if curve else 0,"settings":{**settings,"strategyVersion":"day-atr1-take6R-cap1.4","stopATR":1,"targetR":6,"maxLossR":1.4,"closeAt":"regular-session-close"}}
