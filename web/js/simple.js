/* ASTRA simple home: one number, one sentence, one light, two buttons — for people who just want it to run. */
(function () {
  const A = window.ASTRA;
  const $ = A.$;
  const H = { mode: A.store.get('homeMode', 'simple'), healthAt: 0, health: null };
  A.Simple = H;

  const TONE = { ok: '一切正常', info: '提示', warn: '注意', bad: '需要处理' };
  const HEALTH_TAG = { ok: '系统体检正常', warn: '系统体检有提醒', bad: '系统体检发现问题' };

  function apply() {
    document.body.classList.toggle('simple-mode', H.mode === 'simple');
    $('simpleHome').hidden = H.mode !== 'simple';
    document.querySelectorAll('#homeMode button').forEach((b) => {
      b.classList.toggle('on', b.dataset.v === H.mode);
      b.setAttribute('aria-pressed', String(b.dataset.v === H.mode));
    });
    // the 3D stage measures itself on resize: let it lay out again when it reappears
    if (H.mode === 'pro') window.dispatchEvent(new Event('resize'));
  }

  H.init = function () {
    document.querySelectorAll('#homeMode button').forEach((b) => b.addEventListener('click', () => {
      H.mode = b.dataset.v; A.store.set('homeMode', H.mode); apply(); if (A.state) H.update(A.state);
    }));
    $('smSettings').addEventListener('click', () => $('settingsBtn').click());
    $('smHealth').addEventListener('click', () => {
      $('settingsBtn').click();
      const sec = $('secHealth');
      if (sec) setTimeout(() => sec.scrollIntoView({ block: 'start' }), 50);
    });
    $('smStop').addEventListener('click', () => { $('smConfirm').hidden = false; $('smWord').focus(); });
    $('smCancel').addEventListener('click', closeConfirm);
    $('smGo').addEventListener('click', async () => {
      const word = $('smWord').value.trim();
      if (word !== '确认') { $('smMsg').textContent = '请先输入“确认”。'; return; }
      $('smGo').disabled = true;
      $('smMsg').textContent = '正在提交…';
      try {
        const r = await A.feed.strategy('halt', { confirm: word });
        $('smMsg').textContent = r.result;
        setTimeout(closeConfirm, 2500);
      } catch (e) {
        $('smMsg').textContent = '没有执行：' + e.message;
      } finally {
        $('smGo').disabled = false;
      }
    });
    apply();
  };

  function closeConfirm() {
    $('smConfirm').hidden = true; $('smWord').value = ''; $('smMsg').textContent = '';
  }

  function signed(v, pct, ccy) {
    if (v === null || v === undefined) return '—';
    const cls = v > 0 ? 'pos' : v < 0 ? 'neg' : '';
    return `<b class="${cls}">${A.fmtSigned(v, 2)} ${A.esc(ccy)}</b>${pct !== null && pct !== undefined ? ` <span class="${cls}">${A.fmtPct(pct)}</span>` : ''}`;
  }

  async function checkHealth() {
    if (H.mode !== 'simple' || !A.feed.bridgeState || Date.now() - H.healthAt < 30000) return;
    H.healthAt = Date.now();
    try { H.health = await A.feed.health(); } catch (e) { H.health = { status: 'bad', checks: [{ status: 'bad', label: '桥接服务', detail: e.message }] }; }
    renderHealth();
  }

  function renderHealth() {
    const h = H.health, el = $('smHealth');
    if (!h) { el.className = 'sm-health'; el.innerHTML = '<i></i>系统体检检查中…'; return; }
    const bad = h.checks.filter((c) => c.status !== 'ok');
    el.className = 'sm-health hl-' + h.status;
    el.innerHTML = `<i></i>${HEALTH_TAG[h.status]}${bad.length ? '：' + bad.map((c) => A.esc(c.label)).join('、') : ''} ›`;
  }

  H.update = function (s) {
    if (H.mode !== 'simple') return;
    // the bridge is up but OctoBot is not: never show a beginner the demo money, say what is wrong
    const live = A.feed.bridgeState;
    const blind = !!live && (s.source === 'demo' || s.stale);
    const demo = s.source === 'demo' && !live;
    const n = blind ? {} : (s.numbers || {});
    const eq = blind ? null : n.equity !== undefined ? n.equity : (s.equity || {}).value;
    const ccy = n.currency || (s.equity || {}).currency || 'USDT';
    const [i, f] = A.fmt(eq, 2).split('.');
    $('smEquity').innerHTML = eq === null || eq === undefined ? '—' : `${i}<small>.${f || '00'}</small>`;
    $('smCcy').textContent = blind ? '暂时读不到 OctoBot 的账户' : ccy + (demo ? ' · 演示数据' : s.mode === 'paper' ? ' · 模拟盘' : '');
    $('smToday').innerHTML = signed(n.today, n.todayPct, ccy);
    $('smSince').innerHTML = signed(n.sinceStart, n.sinceStartPct, ccy);
    $('smSinceLabel').textContent = n.startedAt ? `自方案启动（${A.mdhm(n.startedAt)}）` : '自方案启动';

    const st = (blind && live.story) || s.story || (demo
      ? { tone: 'info', title: '演示模式', lines: ['没有连上真实的 OctoBot，下面都是本地模拟数据'], next: '按 README 启动后这里显示真实状态' }
      : { tone: 'info', title: '正在读取状态…', lines: [], next: '' });
    const card = $('smStory');
    card.className = 'sm-story tone-' + st.tone;
    card.innerHTML = `<div class="sm-tag"><i></i>${TONE[st.tone] || ''}</div>
      <h2>${A.esc(st.title)}</h2>
      ${st.lines.length ? `<ul>${st.lines.map((l) => `<li>${A.esc(l)}</li>`).join('')}</ul>` : ''}
      ${st.next ? `<p class="sm-next">下一步：${A.esc(st.next)}</p>` : ''}`;

    const strat = s.strategy || {};
    const idle = (s.profile || {}).id === 'astra_idle';
    $('smStop').disabled = demo || blind || s.mode !== 'paper' || !!strat.stopping || !!strat.switching;
    $('smStop').title = idle ? '观望中也可以用：会再检查一次并卖出剩余持币' : '';
    $('smSettings').textContent = idle || strat.stopped ? '开始交易（选方案）' : '设置方案与止损';
    if (demo) { $('smHealth').className = 'sm-health'; $('smHealth').innerHTML = '<i></i>演示模式不做体检'; }
    else { renderHealth(); checkHealth(); }
  };
})();
