"""Public web frontend with separate in-memory API sessions for each visitor."""
import os,secrets,time,re,threading,importlib.util
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from flask import Flask,request,session,jsonify,send_from_directory,g
from werkzeug.exceptions import HTTPException
import server as engine
import swing,trend_backtest
ROOT=Path(__file__).resolve().parent
app=Flask(__name__,static_folder=None)
app.config.update(SECRET_KEY=os.environ.get('SESSION_SECRET') or secrets.token_hex(32),MAX_CONTENT_LENGTH=1000000,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('LOCAL_PREVIEW')!='1',PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
STATES={};LOCK=threading.Lock()
def new_bridge():
 spec=importlib.util.spec_from_file_location('visitor_telegram',ROOT/'telegram_bridge.py')
 obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
@app.before_request
def prepare():
 if request.path=='/api/health':return
 expected=os.environ.get('RENDER_EXTERNAL_HOSTNAME')
 if expected and request.host.split(':')[0]!=expected:return jsonify(error='Host not allowed'),403
 if request.method=='POST':
  origin=request.headers.get('Origin','')
  allowed='https://'+expected if expected else request.host_url.rstrip('/')
  if origin!=allowed:return jsonify(error='Origin not allowed'),403
 with LOCK:
  now=time.time()
  for sid in list(STATES):
   if now-STATES[sid]['seen']>28800:del STATES[sid]
  sid=session.get('sid')
  if sid not in STATES:
   if len(STATES)>=100:return jsonify(error='Сервер зайнятий. Спробуйте пізніше.'),503
   sid=secrets.token_urlsafe(32);session['sid']=sid;session.permanent=True
   STATES[sid]={'key':None,'nonce':secrets.token_urlsafe(32),'telegram':new_bridge(),'seen':now,'scan_at':0,'poll_at':0,'poll_data':None}
  g.state=STATES[sid];g.state['seen']=now
 g.key_token=engine.CURRENT_KEY.set(g.state['key'])
@app.teardown_request
def cleanup(exc):
 if hasattr(g,'key_token'):engine.CURRENT_KEY.reset(g.key_token)
@app.after_request
def headers(response):
 response.headers['Cache-Control']='no-store'
 response.headers['X-Content-Type-Options']='nosniff'
 response.headers['Referrer-Policy']='same-origin'
 response.headers['X-Frame-Options']='DENY'
 return response
@app.errorhandler(Exception)
def error(exc):
 if isinstance(exc,engine.APIError):return jsonify(error=exc.message),exc.status
 if isinstance(exc,HTTPException):return jsonify(error='Некоректний запит'),exc.code
 if isinstance(exc,(ValueError,TypeError)):return jsonify(error=str(exc)[:200]),400
 return jsonify(error='Сервер тимчасово недоступний. Спробуйте ще раз.'),500
@app.get('/api/health')
def health():return jsonify(app='yaroslava-stock-screener',ready=True)
@app.get('/')
@app.get('/terminal.html')
def index():
 text=(ROOT/'terminal.html').read_text().replace('<body>',f'<body><meta name="local-nonce" content="{g.state["nonce"]}">')
 text=text.replace('Ключ передається локальному серверу','Ключ передається серверу цього сайту через HTTPS').replace('Після закриття програми його потрібно ввести знову.','Після перезапуску сервера ключ потрібно ввести знову.').replace('лише в пам’яті сервера.','лише в пам’яті сервера для вашого браузера.')
 return text
@app.get('/market')
def swing_page():
 return (ROOT/'market.html').read_text().replace('<body>',f'<body><meta name="local-nonce" content="{g.state["nonce"]}">')
@app.get('/<name>')
def assets(name):
 if name not in ('market-journal.js','market.js','app.js','alerts.js','core.js','bands.js','charts.js','history.js','style.css','dark-forest.png'):return jsonify(error='Not found'),404
 return send_from_directory(ROOT,name)
def body():
 data=request.get_json()
 if not isinstance(data,dict) or not secrets.compare_digest(str(data.get('nonce','')),g.state['nonce']):raise ValueError('Оновіть сторінку та повторіть дію')
 return data
@app.post('/api/local-key')
def key():
 value=body().get('key')
 if not isinstance(value,str) or not 10<=len(value.strip())<=200:raise ValueError('Некоректний ключ')
 g.state['key']=value.strip();return jsonify(configured=True)
@app.post('/api/telegram/connect')
def telegram_connect():return jsonify(g.state['telegram'].configure(body().get('token')))
@app.post('/api/telegram/select')
def telegram_select():return jsonify(g.state['telegram'].select(int(body().get('channelId'))))
@app.get('/api/telegram/poll')
def telegram_poll():
 s=g.state
 if time.time()-s['poll_at']<8 and s['poll_data'] is not None:return jsonify(s['poll_data'])
 s['poll_data']=s['telegram'].poll();s['poll_at']=time.time();return jsonify(s['poll_data'])
@app.get('/api/market')
def market():
 tickers=request.args.get('tickers','').upper()
 if tickers and not re.fullmatch(r'[A-Z0-9.,_-]{1,1500}',tickers):raise ValueError('Некоректні тикери')
 low=float(request.args.get('priceMin','1'));high=float(request.args.get('priceMax','25'));volume=float(request.args.get('volumeMin','300000'))
 if not 0<=low<=high<=1000000 or not 0<=volume<=1e12:raise ValueError('Некоректні пороги')
 if not g.state['key']:raise engine.APIError(503,'Введіть ключ Polygon через кнопку «Ключ API»')
 with engine.SCAN_LOCK:
  if time.time()-g.state['scan_at']<10:raise engine.APIError(429,'Зачекайте 10 секунд між скануваннями')
  g.state['scan_at']=time.time()
  return jsonify(engine.scan_market(tickers,low,high,volume,request.args.get("profile","main")))
@app.get('/api/bars')
def bars():
 symbol=request.args.get('symbol','');frame=request.args.get('frame','day');date=request.args.get('date','')
 if not re.fullmatch(r'[A-Z0-9._-]{1,20}',symbol) or frame not in ('day','minute'):raise ValueError('Некоректний тикер або таймфрейм')
 now=datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=ZoneInfo('America/New_York')) if date else datetime.now(ZoneInfo('America/New_York'))
 start=now-timedelta(days=180 if frame=='day' else 7)
 result=engine.api_json(f'/v2/aggs/ticker/{engine.urllib.parse.quote(symbol)}/range/1/{frame}/{start.date()}/{now.date()}?adjusted=true&sort=asc&limit=50000')
 if result.get('next_url'):raise engine.APIError(422,'Історія неповна: потрібна пагінація')
 return jsonify(bars=(result.get('results') or [])[-5000:],frame=frame)

@app.get('/api/swing/universe')
def swing_universe():
 limit=int(request.args.get('limit','100'))
 if limit not in (50,100,300):raise ValueError('Оберіть 50, 100 або 300 акцій')
 return jsonify(swing.universe(limit))

@app.get('/api/swing/batch')
def swing_batch():
 symbols=request.args.get('symbols','').upper().split(',')
 if not 1<=len(symbols)<=8 or any(not re.fullmatch(r'[A-Z0-9._-]{1,20}',s) for s in symbols):raise ValueError('Некоректні тикери')
 now=datetime.now(ZoneInfo('America/New_York'));state=g.state
 benchmark_stamp=now.date().isoformat()+str((now.hour,now.minute)>=(16,15))
 if state.get('swing_benchmark_day')!=benchmark_stamp:
  state['swing_benchmark']=swing.daily('SPY',now);state['swing_benchmark_day']=benchmark_stamp
 benchmark=state['swing_benchmark'];rows=[];errors=[]
 for symbol in symbols:
  try:rows.append(dict(symbol=symbol,**swing.metrics(swing.daily(symbol,now),benchmark)))
  except (engine.APIError,ValueError) as exc:errors.append({'symbol':symbol,'error':getattr(exc,'message',str(exc))})
 return jsonify(rows=rows,errors=errors)

BACKTEST_LOCK=threading.Lock()
@app.post('/api/trend-test')
def start_trend_test():
 data=body();raw=data.get('rows');settings=data.get('settings',{})
 if not isinstance(raw,list) or not 1<=len(raw)<=20000:raise ValueError('Завантажте CSV, до 20 000 записів')
 allowed={}
 for row in raw:
  symbol=row.get('ticker','');day=row.get('date','')
  if not re.fullmatch(r'[A-Z0-9._-]{1,20}',symbol) or not re.fullmatch(r'2026-\d{2}-\d{2}',day):raise ValueError('Потрібні тикери й дати 2026 року')
  datetime.strptime(day,'%Y-%m-%d');allowed.setdefault(symbol,set()).add(day)
 if len(allowed)>2000:raise ValueError('До 2000 унікальних тикерів')
 clean={}
 for name,default,low,high in [('capital',10000,100,1e9),('riskUSD',0,0,1e8),('fee',1,0,1000),('slippage',.1,0,10),('minVolume',500000,0,1e10),('minDollar',10000000,0,1e12)]:
  value=float(settings.get(name,default))
  if not __import__('math').isfinite(value) or not low<=value<=high:raise ValueError('Некоректне значення '+name)
  clean[name]=value
 now=datetime.now(ZoneInfo('America/New_York'));clean.update(start='2026-01-01',end=now.date().isoformat())
 if not g.state['key']:raise engine.APIError(503,'Спочатку підключіть ключ Polygon')
 if not BACKTEST_LOCK.acquire(blocking=False):raise engine.APIError(409,'На сервері вже виконується бектест. Дочекайтеся завершення.')
 state=g.state;job={'status':'running','done':0,'total':len(allowed),'errors':[],'cancel':False};state['trend_job']=job
 key=state['key']
 def work():
  token=engine.CURRENT_KEY.set(key)
  try:
   benchmark=swing.daily('SPY',now);prepared={}
   if not any(clean['start']<=d<=clean['end'] for d in trend_backtest.features(benchmark)):raise ValueError('Немає повної історії SPY для SMA 200 та періоду 2026')
   for symbol in sorted(allowed):
    if job['cancel']:job['status']='cancelled';return
    try:
     bars=swing.daily(symbol,now)
     f=trend_backtest.features(bars)
     if not f:raise ValueError('Недостатньо історії для SMA 200')
     filtered={d:v for d,v in f.items() if clean['start']<=d<=clean['end']}
     if not filtered:raise ValueError('Немає денних даних за період тесту')
     prepared[symbol]=filtered
    except (engine.APIError,ValueError) as exc:
     if isinstance(exc,engine.APIError) and exc.status in (401,403,429):raise
     job['errors'].append({'symbol':symbol,'error':getattr(exc,'message',str(exc))})
    job['done']+=1
   job['result']=trend_backtest.simulate(prepared,benchmark,allowed,clean,prepared=True)
   job['result']['coverage']={'loaded':len(prepared),'requested':len(allowed),'errors':job['errors']}
   job['status']='done'
  except Exception as exc:job.update(status='error',error=getattr(exc,'message','Помилка обчислення: '+str(exc)[:160]))
  finally:engine.CURRENT_KEY.reset(token);BACKTEST_LOCK.release()
 threading.Thread(target=work,daemon=True).start()
 return jsonify(status='running',total=job['total'])

@app.get('/api/trend-test')
def trend_status():return jsonify(g.state.get('trend_job') or {'status':'idle'})

@app.post('/api/trend-test/cancel')
def trend_cancel():
 body()
 if g.state.get('trend_job'):g.state['trend_job']['cancel']=True
 return jsonify(cancelled=True)
