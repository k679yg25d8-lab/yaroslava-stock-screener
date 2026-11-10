const frame=document.getElementById('marketFrame');
if(frame){
 window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==frame.contentWindow||event.data?.type!=='market-height')return;
  const height=event.data.height;
  if(Number.isFinite(height)&&height>=100&&height<=20000)frame.style.height=Math.ceil(height+12)+'px';
 });
 window.addEventListener('market-key-connected',()=>frame.contentWindow?.postMessage({type:'market-refresh'},location.origin));
}
