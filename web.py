"""Public web frontend with separate in-memory API sessions for each visitor."""
import os,secrets,time,re,threading,importlib.util
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from flask import Flask,request,session,jsonify,send_from_directory,g
from werkzeug.exceptions import HTTPException
import server as engine
ROOT=Path(__file__).resolve().parent
app=Flask(__name__,static_folder=None)
app.config.update(SECRET_KEY=os.environ.get('SESSION_SECRET') or secrets.token_hex(32),MAX_CONTENT_LENGTH=3000,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('LOCAL_PREVIEW')!='1',PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
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
@app.get('/<name>')
def assets(name):
 if name not in ('app.js','core.js','bands.js','charts.js','history.js','style.css'):return jsonify(error='Not found'),404
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
  return jsonify(engine.scan_market(tickers,low,high,volume))
@app.get('/api/bars')
def bars():
 symbol=request.args.get('symbol','');frame=request.args.get('frame','day');date=request.args.get('date','')
 if not re.fullmatch(r'[A-Z0-9._-]{1,20}',symbol) or frame not in ('day','minute'):raise ValueError('Некоректний тикер або таймфрейм')
 now=datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=ZoneInfo('America/New_York')) if date else datetime.now(ZoneInfo('America/New_York'))
 start=now-timedelta(days=180 if frame=='day' else 7)
 result=engine.api_json(f'/v2/aggs/ticker/{engine.urllib.parse.quote(symbol)}/range/1/{frame}/{start.date()}/{now.date()}?adjusted=true&sort=asc&limit=50000')
 if result.get('next_url'):raise engine.APIError(422,'Історія неповна: потрібна пагінація')
 return jsonify(bars=(result.get('results') or [])[-5000:],frame=frame)
