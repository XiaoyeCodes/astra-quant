/* ASTRA panels: top rail, stage HUD, ledger, console, status line, emergency controls. */
(function () {
  const A = window.ASTRA;
  const $ = A.$;
  const P = { tab: A.store.get('tab', 'orders'), lastLogId: 0, logSource: null, focusOpts: '' };
  A.Panels = P;

  const MODES = {
    demo: ['演示 DEMO', 'demo'],
    paper: ['模拟盘 PAPER', 'paper'],
    live: ['实盘 LIVE', 'live'],
    watch: ['只读 WATCH', 'watch'],
    idle: ['待机 IDLE', 'idle'],
  };

  P.init = function () {
    setInterval(() => {
      const n = Date.now();
      $('clock').textContent = `${A.hhmmss(n, true)} UTC · ${A.hhmmss(n)}`;
    }, 1000);
    document.querySelectorAll('.tab').forEach((b) => {
      b.classList.toggle('on', b.dataset.tab === P.tab);
      b.addEventListener('click', () => {
        P.tab = b.dataset.tab; A.store.set('tab', P.tab);
        document.querySelectorAll('.tab').forEach((x) => x.classList.toggle('on', x === b));
        if (A.state) renderLedger(A.state);
      });
    });
    $('focusSel').addEventListener('change', (e) => A.feed.setFocus(e.target.value));
    setupKill();
    document.querySelectorAll('#indToggles .tg').forEach((b) => {
      const k = b.dataset.k;
      const sync = () => { b.classList.toggle('on', !!A.vis[k]); b.setAttribute('aria-pressed', String(!!A.vis[k])); };
      sync();
      b.addEventListener('click', () => { A.Views.setVisible(k, !A.vis[k]); sync(); });
    });
    const seg = (id, cur, fn) => {
      const box = $(id);
      const sync = (v) => box.querySelectorAll('button').forEach((x) => { x.classList.toggle('on', x.dataset.v === v); x.setAttribute('aria-pressed', String(x.dataset.v === v)); });
      sync(cur);
      box.querySelectorAll('button').forEach((x) => x.addEventListener('click', () => { fn(x.dataset.v); sync(x.dataset.v); }));
    };
    // settings drawer
    const dr = $('settings'), bg = $('settingsBg');
    const openS = () => { dr.hidden = false; bg.hidden = false; $('settingsClose').focus(); };
    const closeS = () => { dr.hidden = true; bg.hidden = true; $('settingsBtn').focus(); };
    $('settingsBtn').addEventListener('click', openS);
    $('settingsClose').addEventListener('click', closeS);
    bg.addEventListener('click', closeS);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !dr.hidden) closeS(); });
    seg('segStyle', A.viewStyle, (v) => A.Views.setStyle(v));
    seg('segCam', A.camMode, (v) => A.Views.setCam(v));
  };

  P.update = function (s) {
    renderRail(s);
    renderHud(s);
    renderLedger(s);
    renderConsole(s);
    renderStatus(s);
  };

  function chip(el, ok, text, warn) {
    el.className = 'chip ' + (ok ? 'ok' : warn ? 'warn' : 'bad');
    el.innerHTML = `<i class="dot"></i>${text}`;
  }

  function renderRail(s) {
    const [mtxt, mcls] = MODES[s.mode] || MODES.idle;
    const mc = $('modeChip');
    mc.className = 'chip mode ' + mcls; mc.textContent = mtxt;
    const L = s.links;
    const demo = s.source === 'demo';
    chip($('chipOcto'), L.octobot.ok, `OCTOBOT${L.octobot.ok && L.octobot.version ? ' · ' + A.esc(L.octobot.version.replace(/^OctoBot\s*/i, 'v')) : ''}`, demo);
    chip($('chipBitget'), L.bitget.ok, `BITGET${L.bitget.ok && L.bitget.latencyMs ? ' · ' + Math.round(L.bitget.latencyMs) + 'MS' : ''}`, demo);
    chip($('chipBridge'), L.bridge.ok, 'BRIDGE', demo && !L.bridge.version);
    $('chipOcto').title = L.octobot.error || '';
    $('chipBitget').title = L.bitget.error || '';
    $('killBtn').hidden = !s.controls;

    const b = $('banner');
    let msg = '';
    if (demo && !L.bridge.version) msg = '<b>界面预览</b> · 未连接任何交易所，订单和成交都是本地模拟。在你的电脑上按 README 启动后，这里显示 OctoBot 和 Bitget 的真实数据。';
    else if (demo) msg = `<b>演示数据</b> · 桥接服务在线，但没有连上 OctoBot（${A.esc(L.octobot.error || '未启动')}）。启动 OctoBot 并选择策略后自动切换为真实数据。`;
    else if (s.stale || !L.bridge.ok) msg = '<b>数据连接中断</b> · 正在重连，下面保留的是最后收到的账户数据。连接恢复后自动更新。';
    else if (s.mode === 'live') msg = '<b style="color:var(--bear)">实盘</b> · OctoBot 正在使用 Bitget 真实资金交易。紧急情况可在 OctoBot 控制台停止策略。';
    else if (s.strategy && s.strategy.stopped) msg = '<b style="color:var(--bear)">已触发止损</b> · 挂单已撤销、持币已卖成 USDT、交易已暂停。在「设置 → 交易方案」重新启动方案或手动选择策略后恢复。';
    else if (!L.octobot.ok) msg = `<b>OctoBot 重连中</b> · ${s.strategy && s.strategy.switching ? '正在切换策略，OctoBot 重启需要 1–3 分钟。' : '连接暂时中断。'}下面显示的是断开前的最后数据。`;
    else if (s.mode === 'idle') msg = '<b>待机</b> · OctoBot 已连接，但还没有挂单或成交。可以在「设置 → 交易方案」选择方案或策略。';
    b.hidden = !msg;
    if (msg) b.innerHTML = msg;
  }

  function renderHud(s) {
    const eq = s.equity || {};
    $('eqCcy').textContent = eq.currency || 'USDT';
    if (eq.value !== null && eq.value !== undefined && isFinite(eq.value)) {
      const [i, f] = A.fmt(eq.value, 2).split('.');
      $('eqValue').innerHTML = `${i}<span class="cents">.${f}</span>`;
    } else $('eqValue').textContent = '—';
    const ret = eq.value && eq.start ? ((eq.value - eq.start) / eq.start) * 100 : null;
    setPill('eqRet', ret === null ? '—' : A.fmtPct(ret), ret);
    setPill('eqReal', s.stats.closedCount ? A.fmtSigned(s.stats.realized) : '—', s.stats.realized);
    $('eqWin').textContent = s.stats.winRate === null || s.stats.winRate === undefined ? '—' : Math.round(s.stats.winRate * 100) + '%';
    $('eqDD').textContent = s.stats.maxDD ? '−' + (s.stats.maxDD * 100).toFixed(2) + '%' : '—';
    drawSpark(eq.history || [], eq.start);

    const rc = (s.closed || []).slice(-4).reverse();
    $('recentClosed').innerHTML = rc.length
      ? rc.map((c) => `<div><b class="${A.signCls(c.pnl)}">${A.fmtSigned(c.pnl)}</b><small>${c.time ? A.mdhm(c.time) : ''} · ${A.esc(c.symbol.replace(':USDT', ' 合约'))}</small></div>`).join('')
      : '<div><small>还没有平仓记录</small></div>';

    // focus selector
    const opts = (s.symbols || []).join('|');
    if (opts !== P.focusOpts) {
      P.focusOpts = opts;
      $('focusSel').innerHTML = (s.symbols || []).map((x) => `<option value="${A.esc(x)}">${A.esc(x.includes(':') ? x + '  永续' : x + '  现货')}</option>`).join('');
    }
    $('focusSel').value = s.focus.symbol;
    const t = s.ticker;
    const wait = $('stageWait');
    wait.hidden = !!t;
    if (!t) wait.textContent = `等待 ${s.focus.symbol} 行情数据……${s.links.bitget.error ? 'Bitget 连接失败：' + s.links.bitget.error + '。如果你在中国大陆，请在 .env 中配置 BITGET_PROXY。' : ''}`;
    if (t) {
      $('lastPx').textContent = A.fmtPx(t.last);
      $('lastChg').innerHTML = `<span class="${A.signCls(t.changePct)}">${A.fmtSigned(t.change, A.decimalsFor(t.last))} · ${A.fmtPct(t.changePct)}</span> 24H`;
      $('bidPx').textContent = A.fmtPx(t.bid);
      $('askPx').textContent = A.fmtPx(t.ask);
      $('spread').textContent = t.ask && t.bid ? A.fmtPx(t.ask - t.bid) : '—';
      $('range24').textContent = `${A.fmtCompact(t.low)} – ${A.fmtCompact(t.high)}`;
    }
    renderRisk(s);
    renderFlow(s);
    if (s.links.octobot.url) $('octoLink').href = s.links.octobot.url;
  }

  function renderFlow(s) {
    const since = (s.ts || Date.now()) - 3600 * 1000;
    const tr = s.trades.filter((t) => (t.time || 0) >= since);
    const buy = tr.filter((t) => t.side === 'buy'), sell = tr.filter((t) => t.side === 'sell');
    const val = (arr) => arr.reduce((a, t) => a + (t.cost || t.price * t.amount), 0);
    const qty = (arr) => arr.reduce((a, t) => a + t.amount, 0);
    const bv = val(buy), sv = val(sell), tot = bv + sv;
    const [i, f] = A.fmt(tot, 2).split('.');
    $('flowVol').innerHTML = `${i}<span class="cents">.${f}</span>`;
    $('flowCcy').textContent = (s.equity && s.equity.currency) || 'USDT';
    $('flBuyN').textContent = buy.length; $('flSellN').textContent = sell.length;
    $('flBuyAvg').textContent = buy.length ? A.fmtPx(bv / qty(buy)) : '—';
    $('flSellAvg').textContent = sell.length ? A.fmtPx(sv / qty(sell)) : '—';
    const bp = tot ? bv / tot : 0.5;
    $('flBuyBar').style.width = (bp * 100) + '%'; $('flSellBar').style.width = ((1 - bp) * 100) + '%';
    $('flBuyPct').textContent = tot ? `买 ${Math.round(bp * 100)}%` : '买 —';
    $('flSellPct').textContent = tot ? `卖 ${Math.round((1 - bp) * 100)}%` : '卖 —';
    const last = s.trades.slice(-3).reverse();
    $('flowList').innerHTML = last.length ? last.map((t) => `<div><b class="${t.side === 'buy' ? 'pos' : 'neg'}">${t.side === 'buy' ? '买' : '卖'}</b><span>${A.fmtAmt(t.amount)} @ ${A.fmtPx(t.price)}</span><span>${t.time ? A.hhmmss(t.time).slice(0, 5) : ''}</span></div>`).join('')
      : '<div><b>—</b><span>还没有成交</span><span></span></div>';
  }

  function renderRisk(s) {
    const pos = s.positions || [];
    const upnl = pos.reduce((a, p) => a + (+p.upnl || 0), 0);
    const el = $('upnlValue');
    if (pos.length) {
      const [i, f] = A.fmt(Math.abs(upnl), 2).split('.');
      el.innerHTML = `${upnl < 0 ? '−' : '+'}${i}<span class="cents">.${f}</span>`;
    } else el.textContent = '0.00';
    el.classList.toggle('neg-glow', upnl < 0);
    $('upnlCcy').textContent = (s.equity && s.equity.currency) || 'USDT';
    $('rkPos').textContent = pos.length ? pos.length + ' 个' : '无';
    const sym = s.focus.symbol, px = s.ticker ? s.ticker.last : null;
    const buys = s.orders.filter((o) => o.side === 'buy').length, sells = s.orders.filter((o) => o.side === 'sell').length;
    $('rkOrders').textContent = `${buys} / ${sells}`;
    // nearest liquidation distance across positions
    let liq = null;
    pos.forEach((p) => { const m = p.mark || (p.symbol === sym ? px : null); if (p.liq && m) { const d = Math.abs(m - p.liq) / m * 100; liq = liq === null ? d : Math.min(liq, d); } });
    $('rkLiq').textContent = liq === null ? '—' : liq.toFixed(1) + '%';
    $('rkLiq').className = liq !== null && liq < 10 ? 'neg' : '';
    const expo = pos.reduce((a, p) => a + Math.abs(p.size * (p.mark || p.entry || 0)), 0);
    $('rkExpo').textContent = pos.length ? A.fmtCompact(expo) : '—';
    const p = s.profile || {};
    renderStratBar(s, p, sym, px);
    const mode = p.label || (p.tradingMode || '').replace(/TradingMode/g, '').replace(/\s*·\s*/g, ' + ') || '未连接策略';
    $('stratMeta').textContent = `${mode} · ${p.exchangeType === 'future' ? '合约' : '现货'}`;
    renderSchemeLine(s.strategy);
  }

  // what the bar under the risk card shows depends on the kind of strategy running
  function stratFamily(p) {
    if (p.family) return p.family;
    const tm = p.tradingMode || '';
    return /Grid|Staggered/.test(tm) ? 'grid' : /DCA/.test(tm) ? 'dca' : 'other';
  }

  function setBar(lo, hi, t, label) {
    const has = t !== null;
    $('gbBuy').style.width = has ? (t * 100) + '%' : '0';
    $('gbSell').style.width = has ? ((1 - t) * 100) + '%' : '0';
    $('gbMark').style.left = has ? (t * 100) + '%' : '50%';
    $('gbLo').textContent = lo === null ? '—' : A.fmtCompact(lo);
    $('gbHi').textContent = hi === null ? '—' : A.fmtCompact(hi);
    $('gbPct').textContent = label;
  }

  function renderStratBar(s, p, sym, px) {
    const fam = stratFamily(p);
    const os = s.orders.filter((o) => o.symbol === sym);
    const pct = (v) => ((v - px) / px * 100);
    if (fam === 'grid') {
      const g = os.filter((o) => !/STOP|LOSS|TAKE/.test(o.type));
      if (g.length > 1 && px) {
        const lo = Math.min(...g.map((o) => o.price)), hi = Math.max(...g.map((o) => o.price));
        const t = A.clamp((px - lo) / (hi - lo || 1), 0, 1);
        return setBar(lo, hi, t, `网格位置 ${Math.round(t * 100)}%`);
      }
      return setBar(null, null, null, '当前交易对无网格挂单');
    }
    if (fam === 'dca') {
      const buys = os.filter((o) => o.side === 'buy').map((o) => o.price);
      const sells = os.filter((o) => o.side === 'sell').map((o) => o.price);
      if (!px || (!buys.length && !sells.length)) return setBar(null, null, null, '等待开仓信号');
      const below = buys.filter((b) => b < px), above = sells.filter((x) => x > px);
      const next = below.length ? Math.max(...below) : null, tp = above.length ? Math.min(...above) : null;
      const lo = buys.length ? Math.min(...buys) : px, hi = tp || px;
      const t = hi > lo ? A.clamp((px - lo) / (hi - lo), 0, 1) : null;
      const parts = [];
      if (next) parts.push(`下一档补仓 ${A.fmtPct(pct(next))}`);
      if (tp) parts.push(`止盈 ${A.fmtPct(pct(tp))}`);
      return setBar(lo, hi, t, parts.join(' · ') || `补仓档 ${buys.length}`);
    }
    const label = { idle: '观望中：不下新单', ai: 'AI 决定买卖与仓位', index: '按权重持有，偏离时再平衡' }[fam] || '按指标信号交易，无固定区间';
    if (os.length && px) {
      const near = os.reduce((a, o) => (Math.abs(o.price - px) < Math.abs(a.price - px) ? o : a));
      return setBar(null, null, null, `${label} · 最近挂单 ${A.fmtPct(pct(near.price))}`);
    }
    return setBar(null, null, null, label);
  }

  const SCHEME_SHORT = { manual: '手动', ai_trade: 'AI 交易', ai_regime: 'AI 选策略', rule_regime: '指标选策略' };
  function renderSchemeLine(st) {
    const el = $('rkScheme');
    if (!st) { el.textContent = '演示模式'; el.className = 'rk-scheme num'; return; }
    const pct = st.stopLoss[st.scheme] || 0;
    let txt = `方案 ${SCHEME_SHORT[st.scheme] || st.scheme}`, cls = '';
    if (st.stopped) { txt += ' · 已触发止损，交易已停止'; cls = 'neg'; }
    else if (st.stopping) { txt += ' · 正在执行止损'; cls = 'neg'; }
    else if (pct && st.stopLine) {
      const room = st.drawdown === null || st.drawdown === undefined ? null : pct - st.drawdown;
      txt += ` · 止损线 ${A.fmt(st.stopLine, 0)}${room !== null ? `（距 ${room.toFixed(1)}%）` : ''}`;
      if (room !== null && room < pct * 0.3) cls = 'neg';
    } else txt += ' · 未设止损';
    if (st.switching) txt += ` · 切换到 ${st.switching.label}…`;
    el.textContent = txt;
    el.className = 'rk-scheme num ' + cls;
  }

  function setPill(id, txt, v) {
    const el = $(id);
    el.textContent = txt;
    el.className = v ? A.signCls(v) : '';
  }

  function drawSpark(hist, start) {
    const { ctx, w, h } = A.prep($('eqSpark'));
    ctx.clearRect(0, 0, w, h);
    if (hist.length < 2) return;
    const vals = hist.map((x) => x[1]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (start) { lo = Math.min(lo, start); hi = Math.max(hi, start); }
    if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
    const X = (i) => (i / (vals.length - 1)) * (w - 4) + 2;
    const Y = (v) => 4 + (1 - (v - lo) / (hi - lo)) * (h - 8);
    const up = vals[vals.length - 1] >= (start || vals[0]);
    const colr = up ? A.C.bull : A.C.bear;
    if (start) {
      ctx.strokeStyle = A.alpha(A.C.ink, 0.25); ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(0, Y(start) + 0.5); ctx.lineTo(w, Y(start) + 0.5); ctx.stroke(); ctx.setLineDash([]);
    }
    const grd = ctx.createLinearGradient(0, 0, 0, h);
    grd.addColorStop(0, A.alpha(colr, 0.25)); grd.addColorStop(1, A.alpha(colr, 0));
    ctx.beginPath(); vals.forEach((v, i) => (i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v))));
    ctx.lineTo(X(vals.length - 1), h); ctx.lineTo(X(0), h); ctx.closePath(); ctx.fillStyle = grd; ctx.fill();
    ctx.beginPath(); vals.forEach((v, i) => (i ? ctx.lineTo(X(i), Y(v)) : ctx.moveTo(X(i), Y(v))));
    ctx.strokeStyle = colr; ctx.lineWidth = 1.4; ctx.stroke();
    const lx = X(vals.length - 1), ly = Y(vals[vals.length - 1]);
    ctx.fillStyle = colr; ctx.beginPath(); ctx.arc(lx, ly, 2.8, 0, Math.PI * 2); ctx.fill();
  }

  /* ----------------------------------------------------------- ledger --- */
  function renderLedger(s) {
    $('cntOrders').textContent = s.orders.length;
    $('cntPositions').textContent = (s.positions || []).length;
    $('cntTrades').textContent = s.stats.tradeCount;
    const thead = $('ledger').querySelector('thead'), tbody = $('ledger').querySelector('tbody');
    const px = s.ticker ? s.ticker.last : null;
    let head = '', rows = '';
    if (P.tab === 'orders') {
      head = '<tr><th>交易对</th><th>方向</th><th>类型</th><th class="r">价格</th><th class="r">数量</th><th class="r">距现价</th><th>来源</th></tr>';
      const list = s.orders.slice().sort((a, b) => (a.symbol === s.focus.symbol ? 0 : 1) - (b.symbol === s.focus.symbol ? 0 : 1) || b.price - a.price);
      rows = list.slice(0, 120).map((o) => {
        const d = px && o.symbol === s.focus.symbol ? A.fmtPct((o.price / px - 1) * 100) : '—';
        return `<tr><td>${A.esc(o.symbol)}</td><td class="side-${o.side}">${o.side === 'buy' ? '买入' : '卖出'}</td><td>${A.esc(o.type)}</td>
          <td class="r">${A.fmtPx(o.price)}</td><td class="r">${A.fmtAmt(o.amount)}</td><td class="r">${d}</td><td><span class="src">${A.esc(o.source)}</span></td></tr>`;
      }).join('') || '<tr><td colspan="7" class="empty">当前没有挂单</td></tr>';
    } else if (P.tab === 'positions') {
      head = '<tr><th>交易对</th><th>方向</th><th class="r">数量</th><th class="r">开仓均价</th><th class="r">标记价</th><th class="r">未实现盈亏</th><th class="r">强平价</th><th class="r">杠杆</th></tr>';
      rows = (s.positions || []).map((p) => `<tr><td>${A.esc(p.symbol)}</td><td class="side-${p.side === 'short' ? 'sell' : 'buy'}">${p.side === 'short' ? '空' : '多'}</td>
          <td class="r">${A.fmtAmt(p.size)}</td><td class="r">${A.fmtPx(p.entry)}</td><td class="r">${p.mark ? A.fmtPx(p.mark) : '—'}</td>
          <td class="r ${A.signCls(p.upnl)}">${A.fmtSigned(p.upnl)}</td><td class="r">${p.liq ? A.fmtPx(p.liq) : '—'}</td><td class="r">${p.leverage ? p.leverage + 'x' : '—'}</td></tr>`).join('')
        || '<tr><td colspan="8" class="empty">没有合约持仓。现货策略买入的币计入总权益，不算合约持仓。</td></tr>';
    } else {
      head = '<tr><th>时间</th><th>交易对</th><th>方向</th><th class="r">价格</th><th class="r">数量</th><th class="r">金额</th><th class="r">手续费</th></tr>';
      rows = s.trades.slice().reverse().slice(0, 120).map((t) => `<tr><td>${t.time ? A.mdhm(t.time) : '—'}</td><td>${A.esc(t.symbol)}</td>
          <td class="side-${t.side}">${t.side === 'buy' ? '买入' : '卖出'}</td><td class="r">${A.fmtPx(t.price)}</td><td class="r">${A.fmtAmt(t.amount)}</td>
          <td class="r">${A.fmt(t.cost || t.price * t.amount)}</td><td class="r">${A.fmt(t.fee, 4)}</td></tr>`).join('')
        || '<tr><td colspan="7" class="empty">还没有成交</td></tr>';
    }
    if (thead.innerHTML !== head) thead.innerHTML = head;
    tbody.innerHTML = rows;
  }

  /* ---------------------------------------------------------- console --- */
  const LV = { info: 'INFO', fill: 'FILL', ok: 'DONE', warn: 'WARN', risk: 'RISK' };
  function renderConsole(s) {
    const box = $('console');
    if (P.logSource !== s.source) { box.innerHTML = ''; P.lastLogId = 0; P.logSource = s.source; }
    const fresh = (s.log || []).filter((l) => l.id > P.lastLogId);
    fresh.forEach((l) => {
      const d = document.createElement('div');
      d.className = 'ln';
      d.innerHTML = `<span class="t">${A.hhmmss(l.t)}</span><span class="lv ${l.level}">${LV[l.level] || 'INFO'}</span><span class="m"></span>`;
      d.querySelector('.m').textContent = l.msg;
      box.insertBefore(d, box.firstChild);
      P.lastLogId = Math.max(P.lastLogId, l.id);
    });
    while (box.children.length > 140) box.removeChild(box.lastChild);
    $('consoleMeta').textContent = `${(s.log || []).length} 条 · 最新在上`;
  }

  function renderStatus(s) {
    const L = s.links;
    const parts = [
      [s.source === 'demo' ? 'AGENT · DEMO' : 'AGENT · ONLINE', ''],
      ['OCTOBOT', L.octobot.ok ? (L.octobot.version || 'OK') : 'OFFLINE'],
      ['BITGET', L.bitget.ok ? (L.bitget.latencyMs ? Math.round(L.bitget.latencyMs) + 'MS' : 'OK') : 'OFFLINE'],
      ['ORDERS', s.orders.length],
      ['FILLS', s.stats.tradeCount],
      ['CLOSED', s.stats.closedCount],
      ['FOCUS', s.focus.symbol],
      ['BRIDGE', L.bridge.version ? 'v' + L.bridge.version : '—'],
      ['UPDATED', A.hhmmss(s.ts)],
    ];
    $('statusline').innerHTML = parts.map(([k, v]) => `<span>${A.esc(k)}${v !== '' ? ' <b>' + A.esc(v) + '</b>' : ''}</span>`).join('');
  }

  /* ------------------------------------------------------------- kill --- */
  function setupKill() {
    const modal = $('killModal');
    const close = () => { modal.hidden = true; $('killConfirm').value = ''; $('killMsg').textContent = ''; };
    $('killBtn').addEventListener('click', () => { modal.hidden = false; $('killConfirm').focus(); });
    $('killCancel').addEventListener('click', close);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !modal.hidden) close(); });
    $('killGo').addEventListener('click', async () => {
      const word = $('killConfirm').value.trim();
      const msg = $('killMsg');
      if (word !== '确认') { msg.textContent = '请先在输入框中输入“确认”。'; return; }
      $('killGo').disabled = true;
      try {
        msg.textContent = '正在撤销挂单…';
        const r1 = await A.feed.control('cancel-all', word);
        msg.textContent = `撤单：${r1}。正在停止 OctoBot…`;
        await A.feed.control('stop-bot', word);
        msg.textContent = '已撤单并停止 OctoBot。持仓请到 Bitget 自行检查。';
      } catch (e) {
        msg.textContent = '操作失败：' + e.message;
      } finally {
        $('killGo').disabled = false;
      }
    });
  }
})();
