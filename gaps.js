(() => {
  const $ = id => document.getElementById(id);
  const stage = {
    waiting: 'Очікування', no_impulse: 'Без імпульсу', impulse: 'Імпульс',
    base: 'База M2', touched: 'Пробій рівня', breakout: 'Закриття над базою',
    missing_bars: 'Немає даних'
  };
  const strategy = {
    or10: 'Пробій 10 хв', or10_volume: 'Пробій 10 хв + RVOL',
    m2_touch: 'M2 · пробій рівня', m2_close: 'M2 · підтвердження закриттям'
  };
  const alerted = new Set();
  const log = [];
  const et = new Intl.DateTimeFormat('uk-UA', {
    timeZone: 'America/New_York', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit'
  });
  const number = (value, digits = 2) => Number.isFinite(value)
    ? value.toLocaleString('uk-UA', {maximumFractionDigits: digits}) : '—';
  const unit = (value, suffix, digits) => Number.isFinite(value)
    ? number(value, digits) + suffix : '—';
  let liveBusy = false;
  let loaded = false;
  let testTimer;
  let testRows = [];
  let lastRows = [];

  async function api(path, data) {
    const response = await fetch(path, data ? {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...data, nonce: document.querySelector('meta[name=local-nonce]').content})
    } : undefined);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Запит не виконано');
    return result;
  }

  function render(target, rows) {
    $(target).replaceChildren(...rows.map(values => {
      const row = document.createElement('tr');
      for (const value of values) {
        const cell = row.insertCell();
        cell.textContent = value;
      }
      return row;
    }));
  }

  function signal(row, type, message) {
    const key = [row.symbol, row.release, type].join('|');
    if (alerted.has(key)) return;
    alerted.add(key);
    log.unshift(row.symbol + ' · ' + message);
    $('gaps-log').textContent = log.slice(0, 6).join('  |  ');
    if ($('gaps-alerts').checked && window.Notification?.permission === 'granted') {
      new Notification(row.symbol + ' · Гепи', {body: message, tag: key});
    }
  }

  function updateLive(rows) {
    lastRows = rows;
    const minGap = $('gaps-min-gap').valueAsNumber;
    const sorted = rows.filter(row => !Number.isFinite(minGap) || row.gap == null || row.gap >= minGap)
      .sort((a,b) => (b.rvol ?? -1) - (a.rvol ?? -1));
    render('gaps-rows', sorted.map(row => {
      if (!row.closed && !row.stale && ['impulse', 'base', 'touched', 'breakout'].includes(row.stage)) {
        signal(row, 'impulse', 'Звіт підтверджено, є імпульс та підвищений обсяг');
        if (row.level != null && row.last != null &&
            (row.stage !== 'base' || row.last >= row.level - (row.level - row.stop) * 0.25)) {
          signal(row, 'base', 'База M2 · рівень ' + number(row.level));
        }
      }
      return [row.symbol, et.format(new Date(row.release)), unit(row.gap, '%'), unit(row.rvol, '×'),
        unit(row.turnover, ' $', 0), unit(row.spread, ' $', 4),
        row.error || (stage[row.stage] + (row.stale ? ' · дані відстають' : '')), number(row.level), number(row.stop),
        unit(row.stop != null && row.level ? 100 * (row.level - row.stop) / row.level : null, '%'),
        unit(row.green == null ? null : row.green * 100, '%', 0)];
    }));
  }

  async function scan() {
    if (liveBusy) return;
    liveBusy = true;
    $('gaps-refresh').disabled = true;
    $('gaps-status').textContent = 'Шукаю підтверджені звіти…';
    try {
      const {rows, asOf, source} = await api('/api/gaps/live');
      const done = [];
      const rvol = $('gaps-rvol').valueAsNumber;
      updateLive(rows.map(row => ({...row, stage: 'waiting'})));
      for (const event of rows) {
        try {
          done.push(await api('/api/gaps/live/' + encodeURIComponent(event.symbol) + '?rvol=' + rvol));
        } catch (error) {
          done.push({...event, error: error.message});
        }
        updateLive([...done, ...rows.slice(done.length).map(row => ({...row, stage: 'waiting'}))]);
        $('gaps-status').textContent = 'Перевірено ' + done.length + ' із ' + rows.length;
      }
      const status = rows.length
        ? rows.length + ' звітів · оновлено ' + et.format(new Date(asOf)) + ' ET'
        : 'Звітів для цієї сесії не знайдено';
      $('gaps-status').textContent = status + (source === 'news'
        ? ' · Ticker News: час пресрелізу, стрічка оновлюється щогодини' : '');
    } catch (error) {
      $('gaps-status').textContent = error.message;
    } finally {
      liveBusy = false;
      $('gaps-refresh').disabled = false;
    }
  }

  async function checkTest() {
    clearTimeout(testTimer);
    try {
      const job = await api('/api/gaps/test');
      if (job.status === 'idle') return;
      const counts = {};
      for (const row of job.rows) counts[row.status] = (counts[row.status] || 0) + 1;
      testRows = job.rows;
      $('gaps-export').disabled = !testRows.length;
      $('gaps-backtest').disabled = job.status === 'running';
      $('gaps-test-status').textContent = [
        job.error || (job.status === 'running' ? 'Тест триває' : 'Тест завершено'),
        job.done + '/' + job.total,
        ...(job.source === 'news' ? ['Джерело: пресрелізи Ticker News'] : []),
        'З даними ' + (counts.ok || 0),
        'Без підтвердження ' + (counts.unconfirmed || 0),
        'Без повних барів ' + (counts.missing_bars || 0),
        'Очікуються ' + (counts.pending || 0),
        'Помилки ' + (counts.error || 0)
      ].join(' · ');
      render('gaps-results', (job.summary || []).map(r =>
        [r.split, strategy[r.strategy], r.count, number(r.mean), number(r.drawdown)]));
      if (job.status === 'running') testTimer = setTimeout(checkTest, 4000);
    } catch (error) {
      $('gaps-test-status').textContent = error.message;
      $('gaps-backtest').disabled = false;
    }
  }

  $('gaps-backtest').onclick = async () => {
    $('gaps-backtest').disabled = true;
    try {
      await api('/api/gaps/test', {
        rvol: $('gaps-rvol').valueAsNumber, spread: $('gaps-spread').valueAsNumber,
        slippage: $('gaps-slip').valueAsNumber
      });
      await checkTest();
    } catch (error) {
      $('gaps-test-status').textContent = error.message;
      $('gaps-backtest').disabled = false;
    }
  };

  $('gaps-export').onclick = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(testRows, null, 2)], {type: 'application/json'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'gaps-backtest.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  $('gaps-alerts').onchange = async event => {
    if (!event.target.checked) return;
    if (!window.Notification || await Notification.requestPermission() !== 'granted') {
      event.target.checked = false;
      $('gaps-status').textContent = 'Сповіщення браузера недоступні. Алерти видно у вкладці.';
    }
  };
  $('gaps-refresh').onclick = scan;
  $('gaps-min-gap').oninput = () => updateLive(lastRows);
  $('gaps-auto').onchange = () => { if ($('gaps-auto').checked) scan(); };
  setInterval(() => {
    if ($('gaps-auto').checked && !$('gaps-section').hidden) scan();
  }, 60000);
  async function route() {
    const active = location.hash === '#gaps-section';
    $('gaps-section').hidden = !active;
    $('market-section').hidden = active;
    $('gaps-tab').setAttribute('aria-current', active ? 'page' : 'false');
    if (active) {
      $('gaps-section').scrollIntoView();
      if (!loaded) {
        loaded = true;
        await checkTest();
        try {
          const events = await api('/api/gaps/events');
          $('gaps-event-count').textContent = events.length + ' подій із файла';
        } catch (error) {
          $('gaps-event-count').textContent = error.message;
        }
      }
    }
  }
  addEventListener('hashchange', route);
  route();
})();
