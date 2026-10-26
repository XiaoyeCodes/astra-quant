/* ASTRA strategy desk (settings drawer): trading schemes, stop-loss, market radar, manual strategy switch. */
(function () {
  const A = window.ASTRA;
  const $ = A.$;
  const S = { pick: null, aiPick: null, slFor: null, sel: null, confirm: null, listKey: '', busy: false, last: null };
  A.Strategy = S;

  const SCHEME_LABEL = { manual: '手动', ai_trade: 'AI 交易', ai_regime: 'AI 选策略', rule_regime: '指标选策略' };
  const REGIME_TAG = { range: '震荡', up: '上涨', down: '下跌', volatile: '剧烈波动' };
  const FAMILY_TAG = { grid: '网格', dca: '补仓', signal: '指标信号', index: '指数', idle: '观望', ai: 'AI' };

  const labelOf = (st, id) => ((st.list || []).find((x) => x.id === id) || {}).label || id || '—';
  const timeOf = (ms) => (ms ? A.mdhm(ms) : '—');

  function desc(scheme, st) {
    const a = st.auto || {}, ai = st.ai || {};
    return {
      manual: '在下方「策略」列表里自己挑一个运行，不会自动切换。',
      ai_trade: 'AI 信号：大模型每小时判断看涨/看跌并买卖；AI 仓位：多个 AI 智能体决定 BTC/ETH/SOL/USDT 的比例并调仓。需要在 .env 填 DeepSeek Key。',
      ai_regime: `每 ${ai.periodMin || 30} 分钟让 ${ai.model || '大模型'} 分析一次行情，连续 2 次判断一致且置信度 ≥ 50% 时切换到它挑选的策略；每个策略至少运行 ${a.minHoldHours || 12} 小时。`,
      rule_regime: `每 10 分钟用 ADX、ATR、均线斜率、布林带宽度判断行情，连续 ${a.confirmNeeded || 3} 次一致时切换到对应策略；每个策略至少运行 ${a.minHoldHours || 12} 小时。不需要 API Key。`,
    }[scheme] || '';
  }

  S.init = function () {
    S.initKeys();
    document.querySelectorAll('#schemeList button').forEach((b) => b.addEventListener('click', () => {
      S.pick = b.dataset.v; S.slFor = null; msg('schemeMsg', ''); if (S.last) render(S.last);
    }));
    document.querySelectorAll('#aiTradePick button').forEach((b) => b.addEventListener('click', () => {
      S.aiPick = b.dataset.v; if (S.last) render(S.last);
    }));
    $('schemeGo').addEventListener('click', () => act('schemeMsg', 'scheme', {
      scheme: S.pick, aiProfile: S.pick === 'ai_trade' ? S.aiPick : undefined, stopLoss: $('slInput').value,
    }));
    $('slSave').addEventListener('click', () => act('schemeMsg', 'stoploss', { scheme: S.pick, pct: $('slInput').value }));
    $('aiRefresh').addEventListener('click', () => act('schemeMsg', 'ai-refresh', {}));
    $('slInput').addEventListener('input', () => { S.slFor = S.pick; });
    $('stList').addEventListener('click', (e) => {
      const go = e.target.closest('[data-go]');
      if (go) {
        if (S.confirm !== go.dataset.go) { S.confirm = go.dataset.go; S.listKey = ''; if (S.last) render(S.last); return; }
        S.confirm = null;
        act('stMsg', 'select', { profile: go.dataset.go });
        return;
      }
      const card = e.target.closest('[data-id]');
      if (card && e.target.closest('.st-desc')) return; // let people select the description text
      if (card) { S.sel = S.sel === card.dataset.id ? null : card.dataset.id; S.confirm = null; S.listKey = ''; if (S.last) render(S.last); }
    });
  };

  S.initKeys = function () {
    $('stList').addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-id]')) { e.preventDefault(); e.target.click(); }
    });
  };

  function msg(id, text, bad) {
    const el = $(id);
    el.textContent = text;
    el.classList.toggle('bad', !!bad);
  }

  async function act(msgId, action, body) {
    if (S.busy) return;
    S.busy = true;
    msg(msgId, '处理中…');
    try {
      const r = await A.feed.strategy(action, body);
      msg(msgId, r.result || '完成');
      S.slFor = null; S.listKey = '';
    } catch (e) {
      msg(msgId, e.message, true);
    } finally {
      S.busy = false;
    }
  }

  S.update = function (s) {
    S.last = s;
    if ($('settings').hidden && S.pick) return; // drawer closed: skip the heavy part, keep the first render
    render(s);
  };

  function render(s) {
    const st = s.strategy;
    const off = !st;
    ['schemeGo', 'slSave', 'aiRefresh', 'slInput'].forEach((id) => { $(id).disabled = off || !!(st && st.stopping); });
    document.querySelectorAll('#schemeList button, #aiTradePick button').forEach((b) => { b.disabled = off; });
    if (off) {
      $('schemeDesc').textContent = '演示模式：连接 OctoBot 后可以选择方案、设置止损和切换策略。';
      $('schemeStatus').innerHTML = '';
      $('radarRule').innerHTML = '<p class="hint">连接桥接服务后显示行情判断。</p>';
      $('radarAi').innerHTML = '';
      $('stList').innerHTML = '';
      return;
    }
    if (!S.pick) S.pick = st.scheme;
    if (!S.aiPick) S.aiPick = st.aiTradeProfile;
    document.querySelectorAll('#schemeList button').forEach((b) => {
      b.classList.toggle('on', b.dataset.v === S.pick);
      b.classList.toggle('running', b.dataset.v === st.scheme);
      b.setAttribute('aria-pressed', String(b.dataset.v === S.pick));
    });
    $('aiTradePick').hidden = S.pick !== 'ai_trade';
    document.querySelectorAll('#aiTradePick button').forEach((b) => b.classList.toggle('on', b.dataset.v === S.aiPick));
    $('schemeDesc').textContent = desc(S.pick, st);
    if (S.slFor !== S.pick) $('slInput').value = st.stopLoss[S.pick] ?? 0;
    $('schemeGo').textContent = S.pick === st.scheme ? '重新启动方案（以当前资金为起点）' : `启动「${SCHEME_LABEL[S.pick]}」方案`;
    if (st.blocked && !st.switching) msg('schemeMsg', st.blocked, true);
    renderStatus(s, st);
    renderRadar(s, st);
    renderList(s, st);
  }

  function renderStatus(s, st) {
    const rows = [];
    const base = st.baseline;
    const pct = st.stopLoss[st.scheme] || 0;
    rows.push(`<div><span>运行中</span><b>${A.esc(SCHEME_LABEL[st.scheme])} · ${A.esc(labelOf(st, st.current))}</b></div>`);
    if (base && base.equity) rows.push(`<div><span>起点资金</span><b>${A.fmt(base.equity, 2)} USDT · ${timeOf(base.ts)}</b></div>`);
    if (pct && st.stopLine) {
      const dd = st.drawdown;
      const room = dd === null || dd === undefined ? null : pct - dd;
      rows.push(`<div><span>止损线</span><b>${A.fmt(st.stopLine, 2)}（−${pct}%）</b></div>`);
      rows.push(`<div><span>当前盈亏</span><b class="${dd > 0 ? 'neg' : 'pos'}">${dd === null || dd === undefined ? '—' : A.fmtPct(-dd)}${room !== null ? ` · 距止损 ${room.toFixed(2)}%` : ''}</b></div>`);
    } else rows.push('<div><span>止损</span><b>未设置</b></div>');
    if (st.auto && st.auto.enabled) rows.push(`<div><span>自动切换</span><b>${A.esc(st.auto.status)}</b></div>`);
    let html = `<div class="kv2">${rows.join('')}</div>`;
    if (st.switching) html += `<div class="note-box">正在切换到 ${A.esc(st.switching.label)}，OctoBot 重启需要 1–3 分钟…</div>`;
    if (st.stopping) html += '<div class="note-box bad">正在执行止损：撤单、卖出全部持币、暂停交易…</div>';
    if (st.stopped) {
      const x = st.stopped;
      const what = x.by === 'octobot'
        ? `OctoBot 内置备用止损先触发（起点资金 ${A.fmt(x.baseline || 0, 2)}，上限 ${x.pct}%）。`
        : `资金从 ${A.fmt(x.baseline || 0, 2)} 跌到 ${A.fmt(x.equity || 0, 2)}（${x.loss ? '−' + x.loss.toFixed(2) + '%' : '—'}，上限 ${x.pct}%）。`;
      html += `<div class="note-box bad"><b>已触发止损</b> · ${timeOf(x.ts)}<br>${what}已撤单、卖成 USDT 并暂停交易，OctoBot 配置已改为观望。重新启动一个方案或手动选择策略后恢复。</div>`;
    }
    $('schemeStatus').innerHTML = html;
  }

  function verdictHtml(title, v, extra) {
    if (!v) return '';
    const tag = `<span class="rg rg-${v.regime}">${A.esc(v.label)}</span>`;
    const reasons = (v.reasons || []).map((r) => `<li>${A.esc(r)}</li>`).join('');
    return `<div class="radar-head"><span>${title}</span>${tag}<span class="conf">置信度 ${Math.round((v.confidence || 0) * 100)}%</span></div>
      ${extra || ''}<ul>${reasons}</ul><div class="radar-foot">${timeOf(v.ts)}</div>`;
  }

  function renderRadar(s, st) {
    const v = s.regime;
    $('radarSym').textContent = (v && v.symbol) || 'BTC/USDT';
    $('radarRule').innerHTML = v
      ? verdictHtml('指标判断', v, `<div class="radar-pick">推荐：${v.prefer.map((id) => A.esc(labelOf(st, id))).join(' / ')}</div>`)
      : '<p class="hint">指标雷达首次判断需要约 1 分钟（拉取 200 根 1 小时 K 线）。</p>';
    const ai = st.ai || {};
    let h;
    if (!ai.available) h = '<p class="hint">AI 判断未启用：在 .env 填写 LLM_API_KEY（DeepSeek）后重新运行 start.bat。</p>';
    else if (ai.verdict) h = verdictHtml(`AI 判断 · ${A.esc(ai.verdict.model || ai.model)}`, ai.verdict,
      `<div class="radar-pick">AI 选择：${A.esc(labelOf(st, ai.verdict.strategy))}${ai.verdict.tokens ? ` · ${ai.verdict.tokens} tokens` : ''}</div>`);
    else h = `<p class="hint">AI 尚未判断（每 ${ai.periodMin || 30} 分钟一次，可点下方按钮立即分析）。</p>`;
    if (ai.error) h += `<p class="hint bad">上次失败：${A.esc(ai.error)}</p>`;
    $('radarAi').innerHTML = h;
    $('aiRefresh').hidden = !ai.available;
  }

  function renderList(s, st) {
    const v = s.regime || {}, aiv = (st.ai && st.ai.verdict) || {};
    const key = JSON.stringify([st.list.map((x) => [x.id, x.blocked]), st.current, v.prefer, aiv.strategy, S.sel, S.confirm, !!st.switching, st.blocked]);
    if (key === S.listKey) return;
    S.listKey = key;
    $('stList').innerHTML = st.list.map((x) => {
      const badges = [];
      if (x.id === st.current) badges.push('<i class="b cur">当前</i>');
      if ((v.prefer || []).includes(x.id)) badges.push('<i class="b rec">指标推荐</i>');
      if (aiv.strategy === x.id) badges.push('<i class="b ai">AI 推荐</i>');
      if (x.blocked) badges.push('<i class="b off">不可用</i>');
      const fits = x.regimes.length ? '适合：' + x.regimes.map((r) => REGIME_TAG[r]).join(' / ') : (x.family === 'ai' ? '由 AI 决定' : '不择时');
      const open = S.sel === x.id;
      let foot = '';
      if (open) {
        if (x.blocked) foot = `<p class="hint bad">${A.esc(x.blocked)}</p>`;
        else if (x.id === st.current) foot = '<p class="hint">正在运行这个策略。</p>';
        else if (st.blocked) foot = `<p class="hint bad">${A.esc(st.blocked)}</p>`;
        else foot = `<button type="button" class="act primary" data-go="${x.id}">${S.confirm === x.id ? '再点一次确认：OctoBot 将重启 1–3 分钟，方案改为手动' : `切换到「${A.esc(x.label)}」`}</button>`;
        foot = `<p class="st-desc">${A.esc(x.description)}</p>${foot}`;
      }
      return `<div class="st-card${open ? ' open' : ''}${x.blocked ? ' blocked' : ''}" data-id="${x.id}" role="button" tabindex="0">
        <div class="st-top"><b>${A.esc(x.label)}</b><span class="tag">${FAMILY_TAG[x.family] || x.family}</span>${badges.join('')}</div>
        <p class="st-sum">${A.esc(x.summary)}</p><p class="st-fit">${fits}</p>${foot}</div>`;
    }).join('');
  }
})();
