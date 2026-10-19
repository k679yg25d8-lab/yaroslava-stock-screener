const SEEN_KEY="screener.kushAlerts.seen",PREFS_KEY="screener.kushAlerts.preferences";
const read=(storage,key,fallback)=>{try{return JSON.parse(storage.getItem(key))??fallback}catch{return fallback}};
const write=(storage,key,value)=>{try{storage.setItem(key,JSON.stringify(value))}catch{}};
export function claimKushAlerts(storage,day,items,memory=new Set()){
 const saved=read(storage,SEEN_KEY,null),seen=new Set(saved?.day===day&&Array.isArray(saved.symbols)?saved.symbols:[]),fresh=[];
 for(const item of items){if(!item||item.tradingDate!==day||item.dataSource==="demo"||typeof item.symbol!=="string")continue;
  const key=day+":"+item.symbol;if(seen.has(item.symbol)||memory.has(key))continue;
  seen.add(item.symbol);memory.add(key);fresh.push(item);
 }
 write(storage,SEEN_KEY,{day,symbols:[...seen]});return fresh;
}
export function createKushAlerts({day,seed=[],onOpen}){
 let storage;try{storage=window.localStorage}catch{storage={getItem:()=>null,setItem:()=>{}}}
 let prefs={enabled:true,desktop:false,sound:false,...read(storage,PREFS_KEY,{})},audio=null;const memory=new Set();
 claimKushAlerts(storage,day(),seed,memory);
 const headerButton=document.createElement("button");headerButton.id="kushAlertsBtn";headerButton.textContent="Сповіщення";headerButton.title="Сповіщення про нові акції в Угодах куща";document.querySelector("header").append(headerButton);
 const dialog=document.createElement("dialog");dialog.id="kushAlertsSettings";
 dialog.innerHTML=`<form method="dialog"><button class="close" aria-label="Закрити сповіщення">✕</button></form><h2>Сповіщення · Угоди куща</h2><p>Коли нова акція вперше потрапляє до відбору, ви отримаєте сповіщення. Повторні оновлення та відкриття журналу не створюють повторів.</p><label><input id="kushAlertEnabled" type="checkbox"> Сповіщення на сайті</label><label><input id="kushAlertSound" type="checkbox"> Звуковий сигнал</label><label><input id="kushAlertDesktop" type="checkbox"> Системні сповіщення браузера</label><p id="kushAlertStatus" role="status"></p><button id="kushAlertTest" class="primary">Перевірити сповіщення</button><p class="note">Працює, доки скринер відкритий і отримує дані. Для системних сповіщень потрібна підтримка та дозвіл браузера; у деяких вбудованих браузерах доступні лише сповіщення на сайті.</p>`;
 document.body.append(dialog);
 const enabled=dialog.querySelector("#kushAlertEnabled"),sound=dialog.querySelector("#kushAlertSound"),desktop=dialog.querySelector("#kushAlertDesktop"),status=dialog.querySelector("#kushAlertStatus");
 const supported=()=>typeof window.Notification==="function";
 function persist(){write(storage,PREFS_KEY,prefs);headerButton.textContent=prefs.enabled||prefs.desktop||prefs.sound?"Сповіщення":"Сповіщення вимкнені"}
 function showStatus(){status.textContent=!supported()?"У цьому браузері використовуйте сповіщення на сайті та звук.":Notification.permission==="denied"?"Системні сповіщення заблоковані. Дозвіл можна змінити в налаштуваннях сайту у браузері.":prefs.desktop&&Notification.permission==="granted"?"Системні сповіщення дозволені.":"Налаштування зберігаються у цьому браузері."}
 async function unlockAudio(){try{const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;audio??=new Audio();if(audio.state==="suspended")await audio.resume()}catch{}}
 function beep(){try{if(!audio||audio.state!=="running")return;const oscillator=audio.createOscillator(),gain=audio.createGain(),now=audio.currentTime;oscillator.type="sine";oscillator.frequency.setValueAtTime(740,now);oscillator.frequency.setValueAtTime(990,now+.12);gain.gain.setValueAtTime(.0001,now);gain.gain.exponentialRampToValueAtTime(.13,now+.02);gain.gain.exponentialRampToValueAtTime(.0001,now+.32);oscillator.connect(gain);gain.connect(audio.destination);oscillator.start();oscillator.stop(now+.34)}catch{}}
 document.addEventListener("pointerdown",()=>{if(prefs.sound)void unlockAudio()},{passive:true});
 headerButton.onclick=()=>{enabled.checked=!!prefs.enabled;sound.checked=!!prefs.sound;desktop.checked=!!prefs.desktop;showStatus();dialog.showModal()};
 enabled.onchange=()=>{prefs.enabled=enabled.checked;persist()};
 sound.onchange=async()=>{prefs.sound=sound.checked;if(prefs.sound)await unlockAudio();persist()};
 desktop.onchange=async()=>{if(!desktop.checked){prefs.desktop=false;persist();showStatus();return}try{if(!supported())throw Error("Системні сповіщення не підтримуються цим браузером.");const permission=Notification.permission==="default"?await Notification.requestPermission():Notification.permission;prefs.desktop=permission==="granted";desktop.checked=prefs.desktop;persist();showStatus()}catch(e){prefs.desktop=false;desktop.checked=false;persist();status.textContent=e.message}};
 const host=document.createElement("div");host.id="kushAlertToasts";host.setAttribute("role","status");host.setAttribute("aria-live","polite");host.setAttribute("aria-relevant","additions");if(typeof host.showPopover==="function")host.setAttribute("popover","manual");document.body.append(host);
 function toast(title,body){const card=document.createElement("section");card.className="kush-toast";const heading=document.createElement("strong"),text=document.createElement("p"),actions=document.createElement("div"),open=document.createElement("button"),close=document.createElement("button");heading.textContent=title;text.textContent=body;actions.className="actions";open.textContent="Відкрити Угоди куща";open.className="primary";close.textContent="✕";close.setAttribute("aria-label","Закрити сповіщення");
  const remove=()=>{card.remove();if(!host.children.length&&host.hasAttribute("popover")&&host.matches(":popover-open"))host.hidePopover()};open.onclick=()=>{remove();onOpen()};close.onclick=remove;actions.append(open,close);card.append(heading,text,actions);host.append(card);while(host.children.length>3)host.firstElementChild.remove();if(host.hasAttribute("popover"))try{host.showPopover()}catch{};setTimeout(remove,20000);
 }
 function deliver(title,body,test=false){if(prefs.enabled||test)toast(title,body);if(prefs.sound)beep();if(prefs.desktop&&supported()&&Notification.permission==="granted")try{const n=new Notification(title,{body,tag:test?"kush-alert-test":"kush-"+day()+"-"+body,silent:true});n.onclick=()=>{window.focus();onOpen();n.close()};setTimeout(()=>n.close(),20000)}catch{status.textContent="Системне сповіщення недоступне; сповіщення на сайті продовжують працювати."}}
 dialog.querySelector("#kushAlertTest").onclick=async()=>{if(prefs.sound)await unlockAudio();deliver("Сповіщення працюють","Тест: тут з’явиться тикер нової акції з Угод куща.",true)};
 persist();
 return {notify(items,date){if(!items.length)return;const run=()=>{const fresh=claimKushAlerts(storage,date,items,memory);if(!fresh.length)return;const symbols=fresh.map(s=>s.symbol),title=symbols.length===1?"Нова акція · Угоди куща":"Нові акції · Угоди куща";deliver(title,symbols.slice(0,8).join(", ")+(symbols.length>8?" · ще "+(symbols.length-8):""))};if(navigator.locks?.request)navigator.locks.request("screener-kush-alerts",run).catch(()=>{});else run()}};
}
