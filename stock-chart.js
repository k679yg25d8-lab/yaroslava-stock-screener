import {candleChart} from '/charts.js';
import {zakolBands} from '/bands.js';

let dialog,body,cleanup,request,serial=0;
const dayOf=t=>new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t));
export function openStockChart({symbol,day,archive=false}){
 if(!/^[-A-Z0-9._]{1,20}$/.test(symbol||'')||!/^\d{4}-\d{2}-\d{2}$/.test(day||''))return;
 if(!dialog){
  dialog=document.createElement('dialog');dialog.id='marketStockDetail';dialog.style.width='min(1100px,94vw)';dialog.setAttribute('aria-label','Графік акції Маркету');
  const form=document.createElement('form');form.method='dialog';const close=document.createElement('button');close.className='close';close.textContent='✕';close.setAttribute('aria-label','Закрити графік');form.append(close);
  body=document.createElement('div');dialog.append(form,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>{serial++;request?.abort();cleanup?.();cleanup=null});
 }
 serial++;request?.abort();cleanup?.();cleanup=null;
 body.innerHTML='<h2></h2><p class="stock-day"></p><div class="actions"><select aria-label="Таймфрейм графіка"><option value="minute">Графік · 1 хвилина</option><option value="day">Графік · 1 день</option></select><button type="button">Оновити графік</button></div><p class="note">Закол 10 · SMA(close, 10) · UpperBand +10% · зміщення 0. Період відповідає таймфрейму графіка.</p><p class="stock-bands"></p><p class="stock-status" role="status"></p><canvas aria-label="Свічковий графік акції" style="width:100%;height:300px;border-radius:8px;display:block"></canvas><small class="stock-caption"></small>';
 body.querySelector('h2').textContent=symbol;
 body.querySelector('.stock-day').textContent=`Polygon · ${archive?'Архів · ':''}${day} ET`;
 const select=body.querySelector('select'),canvas=body.querySelector('canvas'),status=body.querySelector('.stock-status'),values=body.querySelector('.stock-bands'),caption=body.querySelector('.stock-caption');
 async function load(){
  const current=++serial,frame=select.value;request?.abort();request=new AbortController();cleanup?.();cleanup=null;values.textContent='';status.textContent='Завантаження свічок Polygon…';
  try{
   const response=await fetch(`/api/bars?symbol=${encodeURIComponent(symbol)}&frame=${frame}&date=${encodeURIComponent(day)}`,{signal:request.signal}),data=await response.json();
   if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);
   if(current!==serial||!dialog.open)return;
   const fullBands=zakolBands(data.bars),indices=data.bars.map((_,i)=>i).filter(i=>!archive||dayOf(data.bars[i].t)===day),bars=indices.map(i=>data.bars[i]),bands=indices.map(i=>fullBands[i]);
   if(!bars.length)throw Error('Polygon не повернув свічки за цей день');
   status.textContent=`${symbol} · ${archive?'лише '+day+' ET':'останні доступні свічки'} · ${bars.length} свічок · остання свічка може бути незавершеною`;
   const last=bands.at(-1);values.textContent=last?.sma==null?'Недостатньо 10 свічок для смуг':`SMA 10: $${last.sma.toFixed(4)} · UpperBand: $${last.upper.toFixed(4)}`;
   cleanup=candleChart(canvas,bars,frame,caption,bands,{symbol,day,fitAll:archive});
  }catch(error){if(current===serial&&dialog.open)status.textContent=error.message}
 }
 select.onchange=load;body.querySelector('.actions button').onclick=load;
 if(!dialog.open)dialog.showModal();load();
}
