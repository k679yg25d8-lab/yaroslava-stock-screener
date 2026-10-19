export const defaults={shFloatMin:0,shFloatMax:100000000,avgVolumeMax:10000000,atrMax:1,priceMin:1,priceMax:25,zakolMin:10,potentialMin:.5,premarketVolumeMin:300000,pumpPriceMin:1,pumpProfileConfirmed:false,pumpExcludePreviousDays:true,pumpExcludeRepeat:true,staleSeconds:120,refreshSeconds:10,autoRefresh:true,tickers:'',profileVersion:8,signalFreshSeconds:120,growthMin:20,minuteVolumeMin:300000,premarketStart:240,premarketEnd:570,disabledRules:['signalFreshness','shFloat','zakol','potential','repeatPump'],setupAliases:{'звичайний сетап':'normal','2памп':'second_pump','п/т':'pt','швидкий трейд':'fast','памп-тренд':'pump_trend'}};
export const setupNames={normal:'Звичайний',second_pump:'2памп',pt:'п/т',fast:'Швидкий трейд',pump_trend:'Памп-тренд',unknown:'Невідомий'};
export const numericFields=['price','shFloat','avgVolume','atr','zakol','potential','premarketVolume'];
export const eventFields=['activePreviousDays','hadPremarketTrade','repumpedAround0900'];
export function number(x){if(x===null||x===undefined||x===''||typeof x==='boolean')return null;const n=Number(x);return Number.isFinite(n)?n:null}
export function boolean(x){return x===true||x==='true'?true:x===false||x==='false'?false:null}
export function tradingDate(date=new Date()){const p=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);const g=k=>p.find(x=>x.type===k).value;return `${g('year')}-${g('month')}-${g('day')}`}
export function etMinutes(date){const parts=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(date));return Number(parts.find(p=>p.type==='hour').value)*60+Number(parts.find(p=>p.type==='minute').value)}
export function isPremarket(now=Date.now(),c=defaults){const m=etMinutes(now);return m>=c.premarketStart&&m<c.premarketEnd}
export function evaluate(s,c=defaults,now=Date.now()){const type=c.setupAliases[s.setupType]||s.setupType||'unknown',checks=[];
 const add=(rule,value,reason)=>checks.push({rule,state:c.disabledRules.includes(rule)?'skip':value===null?'unknown':value?'pass':'fail',reason});
 const num=(field,min,max=null,strict=false)=>{const v=number(s[field]);add(field,v===null?null:(min===null||v>=min)&&(max===null||(strict?v<max:v<=max)),`${field}: ${v??'—'}; ${min===null?'':`≥ ${min}`} ${max===null?'':`${strict?'<':'≤'} ${max}`}`)};
 num('price',c.priceMin,c.priceMax);
 const growth=growthFromClose(s,c.growthMin,true);add('growth',growth.passed,`Максимальний ріст премаркету від попереднього закриття: ${growth.percent===null?'—':growth.percent.toFixed(2)+'%'}; ≥ ${c.growthMin}% (закриття ${s.previousClose??'—'})`);
 const exchange=s.exchange??s.primary_exchange;add('nasdaq',exchange==null?null:exchange==='XNAS',`Лістинг: ${exchange??'—'}; потрібен Nasdaq / XNAS`);
 const volume=number(s.premarketVolume);add('premarketVolume',volume===null?null:volume>c.premarketVolumeMin,`Накопичений обсяг премаркету: ${volume??'—'}; > ${c.premarketVolumeMin}`);
 const high=number(s.signalHigh),upper=number(s.upperBand);add('bandTouch',s.bandTouch===true?true:s.bandTouch===false?false:high===null||upper===null?null:high>=upper,`Торкання верхньої смуги: High ${high??'—'} ≥ UpperBand ${upper??'—'} (SMA 10 × 1.10)`);
 const eventStamp=number(s.signalTimestamp);add('signalSession',eventStamp===null?null:tradingDate(eventStamp)===tradingDate(now)&&isPremarket(eventStamp,c),`Час торкання: ${eventStamp===null?'—':new Date(eventStamp).toLocaleTimeString('uk-UA',{timeZone:'America/New_York'})} ET; цей премаркет`);
 const stamp=number(s.signalTimestamp)??number(s.sourceUpdatedAt),age=stamp===null?null:now-stamp;
 const inSession=stamp!==null&&tradingDate(stamp)===tradingDate(now)&&isPremarket(stamp,c);
 add('signalFreshness',stamp===null?null:inSession&&age>=-5000&&age<=c.signalFreshSeconds*1000,`Свіжість пампу: ${age===null?'час невідомий':Math.round(age/1000)+' с'}; останні ${c.signalFreshSeconds} с у премаркеті`);
 add('premarketSession',isPremarket(now,c),`Премаркет: ${String(Math.floor(c.premarketStart/60)).padStart(2,'0')}:${String(c.premarketStart%60).padStart(2,'0')}–${String(Math.floor(c.premarketEnd/60)).padStart(2,'0')}:${String(c.premarketEnd%60).padStart(2,'0')} ET`);
 for(const[field,min,max,strict]of [['shFloat',c.shFloatMin,c.shFloatMax,false],['avgVolume',null,c.avgVolumeMax,true],['atr',null,c.atrMax,true],['zakol',c.zakolMin,null,false],['potential',c.potentialMin,null,false]])if(!c.disabledRules.includes(field))num(field,min,max,strict);
 if(!c.disabledRules.includes('activePreviousDays')){const active=boolean(s.activePreviousDays);add('activePreviousDays',active===null?null:!active,`Попередній премаркет ${s.previousActivityDate||'—'}: сетап ${active===true?'був':active===false?'не було':'невідомо'}; обсяг ${s.previousPremarketVolume??'—'}; торкання UpperBand ${s.previousBandTouch??'—'}`)}
 if(!c.disabledRules.includes('repeatPump')){const trade=boolean(s.hadPremarketTrade),repeat=boolean(s.repumpedAround0900);add('repeatPump',trade===false||repeat===false?true:trade===true&&repeat===true?false:null,`PM-угода: ${trade??'—'}; повторний памп: ${repeat??'—'}`)}
 return {type,checks,status:checks.some(x=>x.state==='fail')?'fail':checks.some(x=>x.state==='unknown')?'review':'pass'};
}
export function validateConfig(c){for(const [k,v]of Object.entries(defaults)){if(typeof v==='number'&&(typeof c[k]!=='number'||!Number.isFinite(c[k])||c[k]<0))throw Error(`Некоректний поріг ${k}`);if(typeof v==='boolean'&&typeof c[k]!=='boolean')throw Error(`Некоректна ознака ${k}`)}if(!Number.isInteger(c.premarketStart)||!Number.isInteger(c.premarketEnd))throw Error('Час премаркету задається цілими хвилинами');if(c.premarketStart>=c.premarketEnd||c.premarketEnd>1440)throw Error('Некоректний інтервал премаркету');if(c.priceMin>c.priceMax||c.shFloatMin>c.shFloatMax)throw Error('Мінімум перевищує максимум');if(c.refreshSeconds<10||c.staleSeconds<1)throw Error('Оновлення мінімум 10 с; актуальність мінімум 1 с');if(!c.setupAliases||typeof c.setupAliases!=='object'||Array.isArray(c.setupAliases)||!Array.isArray(c.disabledRules))throw Error('Некоректні винятки');return c}
export function parseCSV(text){const rows=[];let row=[],value='',quoted=false;for(let i=0;i<text.length;i++){const ch=text[i];if(ch==='"'){if(quoted&&text[i+1]==='"'){value+='"';i++}else quoted=!quoted}else if(ch===','&&!quoted){row.push(value);value=''}else if((ch==='\n'||ch==='\r')&&!quoted){if(ch==='\r'&&text[i+1]==='\n')i++;row.push(value);if(row.some(x=>x))rows.push(row);row=[];value=''}else value+=ch}if(quoted)throw Error('Незакриті лапки CSV');row.push(value);if(row.some(x=>x))rows.push(row);const keys=(rows.shift()||[]).map(x=>x.trim().replace(/^\uFEFF/,''));if(!keys.includes('symbol'))throw Error('CSV потребує колонки symbol');return rows.map(r=>Object.fromEntries(keys.map((k,i)=>[k,r[i]??''])))}

export function evaluateKush(s){
 const checks=[];
 const add=(rule,value,reason)=>checks.push({rule,state:value===null?'unknown':value?'pass':'fail',reason});
 const price=number(s.price),avg=number(s.avgVolume),volume=number(s.premarketVolume);
 const growth=growthFromClose(s,25);add('growth',growth.passed,`Ріст від попереднього закриття: ${growth.percent===null?'—':growth.percent.toFixed(2)+'%'}; ≥ 25% (закриття ${s.previousClose??'—'})`);
 add('price',price===null?null:price>=1.5&&price<=20,`Ціна: ${price??'—'}; $1.50–20 включно`);
 add('avgVolume',avg===null?null:avg<2000000,`AvgVol 14: ${avg??'—'}; строго < 2 000 000`);
 add('premarketVolume',volume===null?null:volume>=500000&&volume<=10000000,`Обсяг премаркету: ${volume??'—'}; 500 000–10 000 000 включно; понад 10 млн — виключення`);
 return {type:s.setupType||'unknown',checks,status:checks.some(x=>x.state==='fail')?'fail':checks.some(x=>x.state==='unknown')?'review':'pass',profile:'kush'};
}

export function retainDay(state, rows, day){
 if(!state||state.day!==day)state={day,main:{},kush:{}};
 for(const {profile,s,r} of rows){
  if(s.tradingDate!==day||s.dataSource==='demo'||r.status!=='pass')continue;
  const group=state[profile];if(!group)continue;
  if(!group[s.symbol])group[s.symbol]={s:structuredClone(s),r:structuredClone(r),selectedAt:Date.now()};
 }
 return state;
}

export function growthFromClose(s,minimum,premarket=false){
 const price=number(premarket?s.premarketHigh:s.price),previous=number(s.previousClose);
 if(price===null||previous===null||previous<=0)return {passed:null,percent:null};
 return {passed:price>=previous*(1+minimum/100),percent:(price/previous-1)*100};
}
