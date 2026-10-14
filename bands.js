// Закол 10: supplied thinkScript, close, SIMPLE, displace=0.
export function zakolBands(bars,length=10,pctAbove=10,pctBelow=10){
 let sum=0,invalid=0;return bars.map((b,i)=>{const value=typeof b.c==='number'&&Number.isFinite(b.c)?b.c:null;if(value===null)invalid++;else sum+=value;if(i>=length){const old=bars[i-length].c;if(typeof old==='number'&&Number.isFinite(old))sum-=old;else invalid--}const sma=i>=length-1&&!invalid?sum/length:null;return {t:b.t,sma,upper:sma===null?null:sma*(1+pctAbove/100),lower:sma===null?null:sma*(1-pctBelow/100)}})
}
