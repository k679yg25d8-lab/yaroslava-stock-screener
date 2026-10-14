"""Read-only Telegram Bot API integration; token stays in memory."""
import json,re,threading,urllib.request,urllib.error
TOKEN=None
OFFSET=0
CHANNELS={}
SELECTED=None
POSTS={}
LOCK=threading.Lock()

def call(method,data=None):
    if not TOKEN:raise ValueError('Telegram ще не підключено')
    req=urllib.request.Request('https://api.telegram.org/bot'+TOKEN+'/'+method,data=json.dumps(data or {}).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=15) as response:r=json.load(response)
    except (OSError,ValueError):raise ValueError('Telegram API недоступний або токен недійсний') from None
    if not r.get('ok'):raise ValueError('Telegram відхилив запит. Перевірте токен і доступ бота.')
    return r['result']

def configure(token):
    global TOKEN,OFFSET,SELECTED
    if not isinstance(token,str) or not re.fullmatch(r'\d{5,}:[A-Za-z0-9_-]{20,}',token.strip()):raise ValueError('Некоректний формат токена BotFather')
    with LOCK:
        old=TOKEN;TOKEN=token.strip()
        try:
            bot=call('getMe')
            if call('getWebhookInfo').get('url'):raise ValueError('Цей бот уже використовує webhook. Потрібен окремий бот або вимкнення попередньої інтеграції.')
        except Exception:
            TOKEN=old;raise
        OFFSET=0;SELECTED=None;CHANNELS.clear();POSTS.clear()
        return {'username':bot.get('username'),'configured':True}

def tickers(text):
    result=set(re.findall(r'\$([A-Z][A-Z0-9.\-]{0,14})\b',text))
    result.update(re.findall(r'(?i)(?:ticker|тикер)\s*[:=]\s*([A-Z][A-Z0-9.\-]{0,14})\b',text))
    for line in text.splitlines():
        if re.fullmatch(r'\s*[A-Z][A-Z0-9.\-]{0,14}\s*',line):result.add(line.strip())
    return sorted(s.upper() for s in result)

def status():
    posts=[p for p in POSTS.values() if p['channelId']==SELECTED]
    return {'configured':TOKEN is not None,'channels':list(CHANNELS.values()),'selected':SELECTED,'posts':posts[-200:]}

def poll():
    global OFFSET
    with LOCK:
        if not TOKEN:return status()
        updates=call('getUpdates',{'offset':OFFSET,'timeout':0,'limit':100,'allowed_updates':['channel_post','edited_channel_post']})
        for u in updates:
            OFFSET=max(OFFSET,u['update_id']+1)
            p=u.get('channel_post') or u.get('edited_channel_post')
            if not p:continue
            chat=p['chat'];cid=chat['id'];CHANNELS[cid]={'id':cid,'title':chat.get('title','Канал'),'username':chat.get('username')}
            text=p.get('text') or p.get('caption') or ''
            key=str(cid)+':'+str(p['message_id'])
            POSTS[key]={'id':key,'channelId':cid,'messageId':p['message_id'],'date':p['date'],'text':text[:1000],'tickers':tickers(text),'link':'https://t.me/'+chat['username']+'/'+str(p['message_id']) if chat.get('username') else None}
        while len(POSTS)>200:POSTS.pop(next(iter(POSTS)))
        return status()

def select(cid):
    global SELECTED
    with LOCK:
        if cid not in CHANNELS:raise ValueError('Спочатку дочекайтеся нової публікації цього каналу')
        SELECTED=cid
        return status()
