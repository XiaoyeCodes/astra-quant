/* ASTRA first-use wizard: check the connections, pick a strategy, set the stop lines, test
   notifications, then start — only when the person clicks. Skippable and reopenable. */
(function () {
  const A = window.ASTRA;
  const $ = A.$;
  const W = { step: 0, pick: null, health: null, busy: false };
  A.Wizard = W;

  const STEPS = ['检查连接', '选策略', '设止损', '通知', '启动'];
  const CHOICES = ['astra_spot_grid', 'astra_spot_dca', 'astra_index'];
  const RISK = { stopLoss: 10, trail: 8, dailyLoss: 4 };

  const live = () => A.feed.bridgeState;
  const done = () => A.store.get('wizardDone', false);

  W.init = function () {
    $('wzSkip').addEventListener('click', () => close(true));
    $('wzBack').addEventListener('click', () => go(W.step - 1));
    $('wzNext').addEventListener('click', next);
    $('smWizard').addEventListener('click', W.open);
    $('wizard').addEventListener('keydown', (e) => { if (e.key === 'Escape') close(false); });
  };

  W.open = function () {
    if (!live()) return;
    W.step = 0; W.pick = null; W.health = null;
    // start from the current stop lines when they are set, else the recommended ones
    const st = (A.state || {}).strategy || {};
    W.risk = Object.fromEntries(Object.entries(RISK).map(([k, d]) => [k, (st[k] || {}).manual || d]));
    $('wizard').hidden = false;
    render();
    $('wzNext').focus();
  };

  // first visit with a running bridge and nothing ever started: offer the wizard once
  W.maybeOffer = function (s) {
    if (W.offered || done() || !live() || s.source === 'demo') return;
    W.offered = true;
    const st = s.strategy || {};
    if (st.lastSwitchInfo || (st.baseline && st.current !== 'astra_idle')) { A.store.set('wizardDone', true); return; }
    W.open();
  };

  function close(skipped) {
    $('wizard').hidden = true;
    A.store.set('wizardDone', true);
    if (skipped) $('smWizard').focus();
  }

  function go(i) {
    if ($('wzStop')) W.risk = { stopLoss: $('wzStop').value, trail: $('wzTrail').value, dailyLoss: $('wzDaily').value };
    W.step = Math.max(0, Math.min(STEPS.length - 1, i));
    render();
  }

  async function next() {
    if (W.busy) return;
    if (W.step === 1 && !W.pick) { msg('先选一个策略'); return; }
    if (W.step === 2) {
      const bad = ['wzStop', 'wzTrail', 'wzDaily'].find((id) => { const v = Number($(id).value); return !isFinite(v) || v < 0 || v > (id === 'wzDaily' ? 50 : 90); });
      if (bad) { msg('止损比例需要是 0–90 之间的数字（单日上限 0–50）'); return; }
    }
    if (W.step === STEPS.length - 1) { await start(); return; }
    go(W.step + 1);
  }

  function msg(text, ok) { const el = $('wzMsg'); el.textContent = text || ''; el.className = 'modal-msg' + (ok ? ' ok' : ''); }

  async function start() {
    const s = A.state || {}, st = s.strategy || {};
    W.busy = true; $('wzNext').disabled = true;
    try {
      msg('正在保存止损…');
      await A.feed.strategy('risk', Object.assign({ scheme: 'manual' }, W.risk));
      if (st.current === W.pick) {
        msg('止损已保存。这个策略已经在运行，不需要切换。', true);
      } else {
        msg('正在切换策略…');
        const r = await A.feed.strategy('select', { profile: W.pick });
        msg(r.result + '。可以关掉向导，在首页看进度。', true);
      }
      A.store.set('wizardDone', true);
      $('wzNext').textContent = '完成';
      $('wzNext').onclick = () => { $('wzNext').onclick = null; close(false); };
    } catch (e) {
      msg('没有启动：' + e.message);
    } finally {
      W.busy = false; $('wzNext').disabled = false;
    }
  }

  function pct(v) { return v === null || v === undefined ? '—' : (v >= 0 ? '+' : '−') + Math.abs(v * 100).toFixed(1) + '%'; }

  async function render() {
    const s = A.state || {}, st = s.strategy || {};
    $('wzSteps').innerHTML = STEPS.map((t, i) => `<li class="${i === W.step ? 'on' : i < W.step ? 'done' : ''}">${i + 1}. ${t}</li>`).join('');
    $('wzBack').hidden = W.step === 0;
    $('wzNext').textContent = W.step === STEPS.length - 1 ? '在模拟盘启动' : '下一步';
    $('wzNext').onclick = null;
    msg('');
    const body = $('wzBody');
    if (W.step === 0) {
      body.innerHTML = '<p>先确认 OctoBot 和行情都连上了。</p><div class="health" id="wzHealth"><p class="hint">检查中…</p></div>';
      try { W.health = await A.feed.health(); } catch (e) { W.health = { status: 'bad', checks: [{ status: 'bad', label: '桥接服务', detail: e.message, fix: '' }] }; }
      if (W.step !== 0) return;
      const keep = ['octobot', 'bitget', 'mode', 'storage'];
      const items = W.health.checks.filter((c) => keep.includes(c.id));
      $('wzHealth').innerHTML = items.map((c) => `<div class="hl-row hl-${c.status}"><i></i><b>${A.esc(c.label)}</b><span>${A.esc(c.detail)}${c.fix && c.status !== 'ok' ? `<em>${A.esc(c.fix)}</em>` : ''}</span></div>`).join('')
        + (items.some((c) => c.status === 'bad') ? '<p class="hint warn-text">有项目异常：可以先按提示处理，处理好之前不建议启动。</p>' : '');
    } else if (W.step === 1) {
      const bt = st.backtest || {}, btc = (bt.btc || {}).ret;
      const rows = CHOICES.map((id) => (st.list || []).find((x) => x.id === id)).filter(Boolean);
      if (!W.pick) W.pick = (rows.find((x) => x.recommended) || rows[0] || {}).id;
      body.innerHTML = `<p>选一个在模拟盘运行的策略。回测是 ${A.esc((bt.span || '').slice(0, 7))} 起两年、含手续费的结果，同期持有 BTC ${pct(btc)}。</p>
        <div class="wz-pick">${rows.map((x) => {
          const r = (bt.results || {})[x.id];
          return `<label class="wz-opt${W.pick === x.id ? ' on' : ''}"><input type="radio" name="wzPick" value="${x.id}"${W.pick === x.id ? ' checked' : ''}>
            <span><b>${A.esc(x.label)}</b>${x.recommended ? ' <i class="b new">新手推荐</i>' : ''}${x.caution ? ' <i class="b warn">谨慎</i>' : ''}<br>
            <small>${A.esc(x.summary)}</small>${r ? `<br><small>回测 <b class="${r.ret >= 0 ? 'pos' : 'neg'}">${pct(r.ret)}</b>，最大回撤 ${(r.dd * 100).toFixed(0)}%</small>` : ''}
            ${x.caution ? `<br><small class="warn-text">${A.esc(x.caution)}</small>` : ''}</span></label>`;
        }).join('')}</div>`;
      body.querySelectorAll('input[name=wzPick]').forEach((r) => r.addEventListener('change', () => { W.pick = r.value; render(); }));
    } else if (W.step === 2) {
      const val = (k) => A.esc(String(W.risk[k]));
      body.innerHTML = `<p>止损触发后会自动撤单、卖出全部持币、核实只剩 USDT，然后停在观望。推荐值已经填好，0 表示关闭这一条。</p>
        <div class="risk-grid">
          <label for="wzStop">最大止损</label><input id="wzStop" type="number" min="0" max="90" step="0.5" value="${val('stopLoss')}"><span>%</span><span class="hint-inline">比启动时的资金少这么多就停</span>
          <label for="wzTrail">回撤止损</label><input id="wzTrail" type="number" min="0" max="90" step="0.5" value="${val('trail')}"><span>%</span><span class="hint-inline">从赚到的最高点回落这么多就停，保住利润</span>
          <label for="wzDaily">单日亏损上限</label><input id="wzDaily" type="number" min="0" max="50" step="0.5" value="${val('dailyLoss')}"><span>%</span><span class="hint-inline">一天之内亏这么多就停</span>
        </div>`;
    } else if (W.step === 3) {
      const n = s.notify || (live() && live().notify) || { channels: [] };
      body.innerHTML = n.channels.length
        ? `<p>已配置通知：${A.esc(n.channels.join('、'))}。止损、OctoBot 离线、每日报告都会推送到手机。</p><button type="button" class="act" id="wzNotify">发送一条测试通知</button>`
        : `<p>还没有配置通知渠道。<b>不影响交易</b>，但止损或 OctoBot 离线时不会提醒你。</p>
           <p class="hint">在 .env 里填 Telegram（NOTIFY_TELEGRAM_TOKEN / NOTIFY_TELEGRAM_CHAT_ID）或企业微信、飞书、钉钉、Server 酱的 NOTIFY_WEBHOOK_URL，重启桥接服务后生效。可以先跳过。</p>`;
      const b = $('wzNotify');
      if (b) b.addEventListener('click', async () => { try { msg(await A.feed.notifyTest(), true); } catch (e) { msg(e.message); } });
    } else {
      const x = (st.list || []).find((y) => y.id === W.pick) || {};
      body.innerHTML = `<p>确认一下：</p><ul class="wz-sum">
        <li>策略：<b>${A.esc(x.label || W.pick)}</b>（模拟盘，不动真钱）</li>
        <li>止损：最大 ${A.esc(String(W.risk.stopLoss))}% · 回撤 ${A.esc(String(W.risk.trail))}% · 单日 ${A.esc(String(W.risk.dailyLoss))}%</li>
        <li>点「在模拟盘启动」后 OctoBot 会重启 1–3 分钟，之后自动开始挂单</li></ul>`;
    }
  }

})();
