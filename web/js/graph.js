/* ASTRA strategy sand table: a holographic table read left to right —
   exchange → engine → strategy → trading pair → result. Pads glow on the table, info cards float above,
   light packets travel the links (thicker = more capital). Fills fire a coloured packet down the chain.
   Fixed front view, auto-fitted to the panel; click a pad or card to inspect. */
(function () {
  const A = window.ASTRA;
  const C = A.C;
  const TYPES = {
    exchange: { name: '交易所', color: C.ink },
    engine: { name: '执行引擎', color: C.gold },
    strategy: { name: '策略', color: '#aad2ff' },
    symbol: { name: '交易对', color: C.ink2 },
    result: { name: '结果', color: C.ok },
  };
  const STATIONS = ['在哪交易', '谁在执行', '用什么方法', '交易什么', '结果如何'];
  const SX = [-5, -2.5, 0, 2.5, 5];
  const G = { fitX: 0, fitY: 0, fitK: 1, cv: null, nodes: new Map(), edges: [], hover: null, sel: null, drag: null, packets: [], flashes: [], s: null, seenFills: null, t: 0 };
  A.Graph = G;
  const DEF = { yaw: 0, pitch: 0 };
  const cam = { yaw: DEF.yaw, pitch: DEF.pitch, tYaw: DEF.yaw, tPitch: DEF.pitch, zoom: 1, last: -1e9 };

  G.init = function (cv) {
    G.cv = cv;
    A.$('graphLegend').innerHTML =
      '<span class="sd-flow">从左往右读：交易所 → 引擎 → 策略 → 交易对 → 结果</span>' +
      `<span class="hint3d">线越粗 = 资金越多 · 光点流动 = 运行中 · <b style="color:${C.bull};font-weight:400">蓝</b>/<b style="color:${C.bear};font-weight:400">橙</b> 光球 = 买/卖成交 · 点击看详情</span>`;
    cv.addEventListener('pointermove', onMove);
    cv.addEventListener('pointerdown', onDown);
    window.addEventListener('pointerup', onUp);
    cv.addEventListener('pointerleave', () => { G.hover = null; });
    requestAnimationFrame(loop);
  };

  function point(e) { const r = G.cv.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
  function pick(p) {
    let best = null, bd = Infinity;
    G.nodes.forEach((n) => {
      if (n.card && p.x >= n.card.x && p.x <= n.card.x + n.card.w && p.y >= n.card.y && p.y <= n.card.y + n.card.h) { if (n.depth < bd) { bd = n.depth; best = n; } return; }
      const d = Math.hypot(n.sx - p.x, n.sy - p.y);
      if (d < n.sr + 6 && n.depth < bd) { bd = n.depth; best = n; }
    });
    return best;
  }
  function onMove(e) {
    const p = point(e);
    G.hover = pick(p);
    G.cv.style.cursor = G.hover ? 'pointer' : 'default';
  }
  function onDown(e) {
    const p = point(e);
    G.drag = { x: p.x, y: p.y, moved: false, node: pick(p) };
    G.cv.setPointerCapture && G.cv.setPointerCapture(e.pointerId);
  }
  function onUp() {
    if (G.drag && G.drag.node) { G.sel = G.drag.node.id; renderInspector(); }
    G.drag = null;
  }

  /* ------------------------------------------------------ build model -- */
  const isSwap = (sym) => sym.includes(':');
  function stratLabel(prof, swap) {
    const tm = (prof.tradingMode || '');
    const grid = /Grid/i.test(tm), dca = /DCA/i.test(tm);
    if (grid && dca) return swap ? '合约分批补仓' : '现货网格';
    if (grid) return swap ? '合约网格' : '现货网格';
    if (dca) return swap ? '合约分批补仓' : '现货分批补仓';
    return swap ? '合约策略' : '现货策略';
  }
  const notional = (o) => (o.price || 0) * (o.amount || 0);

  G.update = function (s) {
    G.s = s;
    const prof = s.profile || {};
    const pos = s.positions || [];
    const pairs = new Set(prof.pairs || []);
    // traded symbols: in profile, or with orders / positions
    let syms = (s.symbols || []).filter((x) => pairs.has(x) || s.orders.some((o) => o.symbol === x) || pos.some((p) => p.symbol === x));
    pos.forEach((p) => { if (!syms.includes(p.symbol)) syms.push(p.symbol); });
    if (!syms.length && s.focus) syms = [s.focus.symbol];
    syms = syms.sort((a, b) => isSwap(a) - isSwap(b)).slice(0, 4);
    const n = syms.length, gap = n > 1 ? Math.min(3.2, 4.4 / (n - 1)) : 0;
    const zOf = (i) => (i - (n - 1) / 2) * gap;

    const want = new Map(), edges = [];
    const add = (id, type, x, z, title, lines, data) => want.set(id, { id, type, x, z, title, lines, data });
    const oct = s.links.octobot || {}, bg = s.links.bitget || {};
    const demo = s.source === 'demo';
    const octOk = demo || !!oct.ok, bgOk = demo || !!bg.ok;
    const ccy = (s.equity && s.equity.currency) || 'USDT';
    add('ex', 'exchange', SX[0], 0, 'Bitget', [
      [s.equity ? `${s.mode === 'paper' ? '模拟' : ''}权益 ${A.fmt(s.equity.value, 0)} ${ccy}` : '行情连接'],
      [bgOk ? `● 在线${bg.latencyMs ? ' · ' + bg.latencyMs + ' ms' : ''}` : '● 连接中断', bgOk ? 'ok' : 'neg']], { kind: 'exchange' });
    add('eng', 'engine', SX[1], 0, 'OctoBot', [
      [demo ? '● 演示模式' : !oct.ok ? '● 未连接' : s.mode === 'live' ? '● 实盘运行中' : s.mode === 'paper' ? '● 模拟盘运行中' : '● 已连接 · 待机', demo || oct.ok ? (s.mode === 'live' ? 'neg' : 'ok') : 'neg']], { kind: 'engine' });
    edges.push({ a: 'ex', b: 'eng', rel: 'API', w: 1 });

    // weights: capital per symbol = open order notional + position notional
    const cap = {};
    syms.forEach((sym) => {
      cap[sym] = s.orders.filter((o) => o.symbol === sym).reduce((a, o) => a + notional(o), 0) +
        pos.filter((p) => p.symbol === sym).reduce((a, p) => a + Math.abs(p.size * (p.mark || p.entry || 0)), 0);
    });
    const capMax = Math.max(1, ...Object.values(cap)), capTot = Object.values(cap).reduce((a, b) => a + b, 0) || 1;
    const wOf = (v) => 0.3 + 0.7 * (v / capMax);

    const since = (s.ts || Date.now()) - 3600e3;
    const mkts = [...new Set(syms.map(isSwap))];
    mkts.forEach((swap) => {
      const mine = syms.filter((x) => isSwap(x) === swap);
      const z = mine.reduce((a, x) => a + zOf(syms.indexOf(x)), 0) / mine.length;
      const ord = s.orders.filter((o) => isSwap(o.symbol) === swap).length;
      const c = mine.reduce((a, x) => a + cap[x], 0);
      const id = 'st:' + (swap ? 'swap' : 'spot');
      add(id, 'strategy', SX[2], z, stratLabel(prof, swap), [[`占用 ${A.fmt(c, 0)} ${ccy}`], [`${ord} 个挂单`]], { kind: 'strategy', swap });
      edges.push({ a: 'eng', b: id, rel: '运行', w: wOf(c), live: octOk });
    });
    syms.forEach((sym, i) => {
      const z = zOf(i), swap = isSwap(sym);
      const px = s.focus.symbol === sym && s.ticker ? s.ticker.last : null;
      add('s:' + sym, 'symbol', SX[3], z, swap ? sym.replace(':USDT', '') + ' 永续' : sym,
        [[px ? `现价 ${A.fmtPx(px)}` : (swap ? 'USDT 本位合约' : '现货')]], { kind: 'symbol', symbol: sym });
      edges.push({ a: 'st:' + (swap ? 'swap' : 'spot'), b: 's:' + sym, rel: '交易', w: wOf(cap[sym]), live: octOk });
      const os = s.orders.filter((o) => o.symbol === sym);
      const nb = os.filter((o) => o.side === 'buy').length, ns = os.length - nb;
      const p = pos.find((q) => q.symbol === sym);
      const tr = s.trades.filter((t) => t.symbol === sym && (t.time || 0) >= since);
      const cl = (s.closed || []).filter((c) => c.symbol === sym && (c.time || 0) >= since);
      const rp = cl.reduce((a, c) => a + c.pnl, 0);
      const lines = [[`挂单 买 ${nb} · 卖 ${ns}`]];
      if (p) lines.push([`${p.side === 'short' ? 'SHORT' : 'LONG'} ${A.fmtAmt(p.size)} · ${A.fmtSigned(p.upnl)}`, p.upnl >= 0 ? 'ok' : 'neg']);
      else lines.push([`近 1 小时成交 ${tr.length} 笔${cl.length ? ' · ' + A.fmtSigned(rp) : ''}`, cl.length ? (rp >= 0 ? 'ok' : 'neg') : null]);
      add('r:' + sym, 'result', SX[4], z, p ? '持仓中' : tr.length ? '近 1 小时' : '等待成交', lines,
        { kind: 'result', symbol: sym, nb, ns, nt: tr.length, pos: p || null, rp, ncl: cl.length });
      edges.push({ a: 's:' + sym, b: 'r:' + sym, rel: '结果', w: wOf(cap[sym]), live: octOk });
    });
    edges[0].live = bgOk && (demo || !!oct.ok);

    want.forEach((m, id) => {
      const old = G.nodes.get(id);
      if (old) { Object.assign(old, { title: m.title, lines: m.lines, data: m.data, tx: m.x, tz: m.z, type: m.type }); return; }
      G.nodes.set(id, Object.assign(m, { tx: m.x, tz: m.z, born: performance.now(), sx: 0, sy: 0, sr: 0, depth: 0, card: null }));
    });
    G.nodes.forEach((m, id) => { if (!want.has(id)) G.nodes.delete(id); });
    G.edges = edges.filter((e) => G.nodes.has(e.a) && G.nodes.has(e.b));

    // new fills → coloured packet down the chain + flash on the result pad
    const ids = s.trades.map((t) => t.id);
    if (G.seenFills === null) G.seenFills = new Set(ids);
    else s.trades.forEach((t) => {
      if (G.seenFills.has(t.id)) return;
      G.seenFills.add(t.id);
      if (!G.nodes.has('s:' + t.symbol)) return;
      const col = t.side === 'buy' ? C.bull : C.bear;
      const path = ['eng', 'st:' + (isSwap(t.symbol) ? 'swap' : 'spot'), 's:' + t.symbol, 'r:' + t.symbol];
      for (let i = 0; i < path.length - 1; i++) G.packets.push({ a: path[i], b: path[i + 1], t: -i * 0.9, color: col, big: true });
      G.flashes.push({ id: 'r:' + t.symbol, t: -2.7, color: col, text: `成交 · ${t.side === 'buy' ? '买入' : '卖出'} ${A.fmtPx(t.price)}` });
    });

    if (!G.sel || !G.nodes.has(G.sel)) G.sel = G.nodes.has('s:' + s.focus.symbol) ? 's:' + s.focus.symbol : 'ex';
    A.$('graphMeta').textContent = `交易对 ${syms.length} · 挂单 ${s.orders.length} · 持仓 ${pos.length} · 成交 ${s.stats.tradeCount}`;
    renderInspector();
  };

  function neighbors(id) {
    const out = [];
    G.edges.forEach((e) => { if (e.a === id) out.push({ id: e.b, rel: e.rel, dir: 1 }); else if (e.b === id) out.push({ id: e.a, rel: e.rel, dir: -1 }); });
    return out;
  }
  // every node upstream and downstream of id (a whole route through the table)
  function route(id) {
    const set = new Set([id]);
    const walk = (cur, dir) => G.edges.forEach((e) => {
      const from = dir > 0 ? e.a : e.b, to = dir > 0 ? e.b : e.a;
      if (from === cur && !set.has(to)) { set.add(to); walk(to, dir); }
    });
    walk(id, 1); walk(id, -1);
    return set;
  }

  /* ------------------------------------------------------------- render -- */
  const HOLO = '#aad2ff';
  const toneCol = (t) => (t === 'ok' ? C.ok : t === 'neg' ? C.bear : null);
  function arc(a, b, t) { return [A.lerp(a.x, b.x, t), 0.12 + Math.sin(t * Math.PI) * 0.45, A.lerp(a.z, b.z, t)]; }

  function loop(ts) {
    requestAnimationFrame(loop);
    if (!G.cv || document.hidden || !G.s) return;
    if (G.radial) { const rn = G.nodes.get(G.radial.id); if (rn) drawRadial(rn, neighbors(rn.id)); }
    const { ctx, w, h } = A.prep(G.cv);
    const dt = G.lastTs ? Math.min(0.05, (ts - G.lastTs) / 1000) : 0.016;
    G.lastTs = ts; G.t += dt;
    // fixed front view: no orbit, no zoom
    cam.yaw = DEF.yaw; cam.pitch = DEF.pitch;
    G.nodes.forEach((n) => { n.x = A.lerp(n.x, n.tx, 0.08); n.z = A.lerp(n.z, n.tz, 0.08); });

    // camera fit: table (12 × 6 units) fills the canvas width
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const focal = h * 1.25;
    const dist = Math.max(7, (6.6 * focal) / (0.42 * w)) * cam.zoom * G.fitK;
    const oy = h * 0.62;
    // 0° eye-level front view: the table stands upright — table depth (z) becomes height on screen
    const ROW = 0.85, f0 = focal / dist;
    const P = (x, y, z) => ({ x: w / 2 + G.fitX + x * f0, y: h / 2 + G.fitY - (y - z * ROW) * f0, z: dist, f: f0 });
    const line = (a, b, col, al, lw) => { const p = P(...a), q = P(...b); ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.strokeStyle = A.alpha(col, al); ctx.lineWidth = lw || 1; ctx.stroke(); };
    const poly = (pts, fill, stroke, lw) => { ctx.beginPath(); pts.forEach((v, i) => { const p = P(...v); i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); }); ctx.closePath(); if (fill) { ctx.fillStyle = fill; ctx.fill(); } if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw || 1; ctx.stroke(); } };
    const glow = (x, y, z, r, col, al) => { const p = P(x, y, z), R = r * p.f; if (R < 1) return; const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, R); g.addColorStop(0, A.alpha(col, al)); g.addColorStop(1, A.alpha(col, 0)); ctx.fillStyle = g; ctx.fillRect(p.x - R, p.y - R, R * 2, R * 2); };

    ctx.clearRect(0, 0, w, h);
    ctx.textBaseline = 'alphabetic';
    const TW = 6.1, TD = 3.0;
    // station captions on the front edge
    ctx.font = `10px ${C.mono}`; ctx.textAlign = 'center';
    SX.forEach((x, i) => { const p = P(x, 0, TD - 0.25); ctx.fillStyle = A.alpha(C.ink, 0.42); ctx.fillText(`0${i + 1} ${STATIONS[i]}`, p.x, p.y); });

    G.nodes.forEach((n) => { const p = P(n.x, 0, n.z); n.sx = p.x; n.sy = p.y; n.sr = 0.48 * p.f; n.depth = p.z; });
    const focusId = G.hover ? G.hover.id : G.sel;
    const hot = focusId ? route(focusId) : new Set();

    // links + ambient light packets
    G.edges.forEach((e) => {
      const a = G.nodes.get(e.a), b = G.nodes.get(e.b);
      const on = hot.has(e.a) && hot.has(e.b);
      ctx.beginPath();
      for (let i = 0; i <= 28; i++) { const p = P(...arc(a, b, i / 28)); i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y); }
      ctx.setLineDash(e.live === false ? [4, 5] : []);
      ctx.strokeStyle = e.live === false ? A.alpha(C.bear, 0.45) : A.alpha(HOLO, 0.24 + e.w * 0.22);
      ctx.lineWidth = 1 + e.w * 3; ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1;
      if (e.live === false || A.reduceMotion) return;
      ctx.globalCompositeOperation = 'lighter';
      const cnt = Math.round(2 + e.w * 4);
      for (let k = 0; k < cnt; k++) {
        const p = P(...arc(a, b, (G.t * 0.32 + k / cnt) % 1)), r = 6;
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
        g.addColorStop(0, 'rgba(220,236,255,0.85)'); g.addColorStop(1, 'rgba(170,210,255,0)');
        ctx.fillStyle = g; ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
      }
      ctx.globalCompositeOperation = 'source-over';
    });
    // fill packets
    ctx.globalCompositeOperation = 'lighter';
    for (let i = G.packets.length - 1; i >= 0; i--) {
      const k = G.packets[i]; k.t += dt * 1.1;
      const a = G.nodes.get(k.a), b = G.nodes.get(k.b);
      if (!a || !b || k.t > 1) { G.packets.splice(i, 1); continue; }
      if (k.t < 0) continue;
      const p = P(...arc(a, b, k.t)), r = 13;
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      g.addColorStop(0, A.alpha(k.color, 1)); g.addColorStop(0.35, A.alpha(k.color, 0.6)); g.addColorStop(1, A.alpha(k.color, 0));
      ctx.fillStyle = g; ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
    }
    ctx.globalCompositeOperation = 'source-over';

    // pads, beams, bars — far to near
    const sorted = Array.from(G.nodes.values()).sort((a, b) => b.depth - a.depth);
    const now = performance.now();
    sorted.forEach((n) => {
      const col = TYPES[n.type].color;
      const fade = Math.min(1, (now - n.born) / 500);
      const dim = 1;
      ctx.globalAlpha = fade * dim;
      const fl = G.flashes.find((f) => f.id === n.id && f.t > 0);
      const boost = fl ? Math.max(0, 1 - fl.t) : 0;
      const hex = [];
      for (let i = 0; i < 6; i++) { const a = (i / 6) * Math.PI * 2 + (A.reduceMotion ? 0 : G.t * 0.2); hex.push([n.x + Math.cos(a) * 0.48, 0.02, n.z + Math.sin(a) * 0.48]); }
      glow(n.x, 0.05, n.z, 0.85 + boost * 0.8, fl ? fl.color : col, 0.22 + boost * 0.5);
      poly(hex, A.alpha(col, 0.1), A.alpha(col, n.id === G.sel ? 1 : 0.75), n.id === G.sel ? 1.8 : 1.2);
      if (n.id === G.sel) { const sel = []; for (let i = 0; i < 40; i++) { const a = (i / 40) * Math.PI * 2; sel.push([n.x + Math.cos(a) * 0.68, 0.02, n.z + Math.sin(a) * 0.68]); } ctx.setLineDash([2, 4]); poly(sel, null, A.alpha(C.ink, 0.5)); ctx.setLineDash([]); }
      // result bars: buy orders / sell orders, gold block for a position
      if (n.type === 'result') {
        const d = n.data, mx = Math.max(6, d.nb, d.ns);
        const bar = (bx, v, c) => { const hh = 0.08 + (v / mx) * 0.7; poly([[bx - 0.09, 0, n.z], [bx + 0.09, 0, n.z], [bx + 0.09, hh, n.z], [bx - 0.09, hh, n.z]], A.alpha(c, 0.35), A.alpha(c, 0.9)); };
        bar(n.x - 0.2, d.nb, C.bull); bar(n.x + 0.04, d.ns, C.bear);
        if (d.pos) { const s2 = 0.1; poly([[n.x + 0.2, 0, n.z], [n.x + 0.2 + s2 * 2, 0, n.z], [n.x + 0.2 + s2 * 2, 0.32, n.z], [n.x + 0.2, 0.32, n.z]], A.alpha(C.gold, 0.4), A.alpha(C.gold, 0.95)); }
      }
      line([n.x, 0.05, n.z], [n.x, 0.78, n.z], col, 0.35);
      ctx.globalAlpha = 1;
    });
    // floating info cards, far to near (near cards on top)
    let bx0 = Infinity, bx1 = -Infinity, by0 = Infinity;
    sorted.forEach((n) => {
      const col = TYPES[n.type].color;
      const dim = 1;
      const p = P(n.x, 0.78, n.z);
      ctx.font = `12px ${C.ui || 'sans-serif'}`;
      let cw = ctx.measureText(n.title).width;
      ctx.font = `10px ${C.mono}`;
      n.lines.forEach((l) => { cw = Math.max(cw, ctx.measureText(l[0]).width); });
      cw = Math.min(cw + 18, w * 0.2 + 40);
      const ch = 22 + n.lines.length * 14, x = A.clamp(p.x - cw / 2, 2, w - cw - 2), y = p.y - ch;
      bx0 = Math.min(bx0, p.x - cw / 2, n.sx - n.sr); bx1 = Math.max(bx1, p.x + cw / 2, n.sx + n.sr); by0 = Math.min(by0, p.y - ch);
      n.card = { x, y, w: cw, h: ch };
      ctx.globalAlpha = dim * Math.min(1, (now - n.born) / 500);
      ctx.fillStyle = 'rgba(9,12,18,0.95)'; ctx.fillRect(x, y, cw, ch);
      ctx.strokeStyle = A.alpha(col, n.id === focusId ? 0.9 : 0.5); ctx.strokeRect(x + 0.5, y + 0.5, cw - 1, ch - 1);
      ctx.fillStyle = col; ctx.fillRect(x, y, 2, ch);
      ctx.textAlign = 'left';
      ctx.font = `12px ${C.ui || 'sans-serif'}`; ctx.fillStyle = C.ink; ctx.fillText(n.title, x + 10, y + 16);
      ctx.font = `10px ${C.mono}`;
      n.lines.forEach((l, i) => { const tc = toneCol(l[1]); ctx.fillStyle = tc || A.alpha(C.ink, 0.62); ctx.fillText(l[0], x + 10, y + 31 + i * 14); });
      ctx.globalAlpha = 1;
    });
    // auto-frame: keep the whole table (pads + cards) centred and inside the canvas
    if (isFinite(bx0)) {
      const cs = [[-TW, 0, -TD], [TW, 0, -TD], [TW, 0, TD], [-TW, 0, TD]].map((v) => P(...v).x);
      const t0 = Math.min(...cs), t1 = Math.max(...cs);
      G.fitX += (w / 2 - (t0 + t1) / 2) * 0.12;
      const capY = P(0, 0, TD - 0.25).y + 6;
      G.fitY += (h / 2 + 30 - (by0 + capY) / 2) * 0.12;
      const span = Math.max(bx1, t1) - Math.min(bx0, t0), vspan = capY - by0;
      if (span > w - 28 || vspan > h - 110) G.fitK = Math.min(1.8, G.fitK * 1.012);
      else if (span < w - 90 && vspan < h - 160 && G.fitK > 1) G.fitK = Math.max(1, G.fitK * 0.996);
    }
    // fill captions rising from the result pad
    for (let i = G.flashes.length - 1; i >= 0; i--) {
      const f = G.flashes[i]; f.t += dt * 0.55;
      const n = G.nodes.get(f.id);
      if (!n || f.t > 1) { G.flashes.splice(i, 1); continue; }
      if (f.t < 0) continue;
      const p = P(n.x, 0.1 + f.t * 0.5, n.z);
      ctx.globalAlpha = Math.min(1, (1 - f.t) * 2);
      ctx.font = `12px ${C.ui || 'sans-serif'}`; ctx.textAlign = 'center'; ctx.fillStyle = f.color;
      ctx.fillText(f.text, p.x, (n.card ? n.card.y : p.y) - 8 - f.t * 18);
      ctx.globalAlpha = 1; ctx.textAlign = 'left';
    }
  }

  /* ---------------------------------------------------------- inspector -- */
  function rowsFor(n) {
    const s = G.s, d = n.data;
    const st = (ok) => (ok ? '<span class="pos">在线</span>' : '<span class="neg">离线</span>');
    switch (d.kind) {
      case 'exchange': return [
        ['行情连接', s.source === 'demo' ? '演示数据' : st(s.links.bitget.ok)],
        ['延迟', s.links.bitget.latencyMs ? s.links.bitget.latencyMs + ' ms' : '—'],
        ['账户读取', s.links.bitget.private ? '已配置只读密钥' : '未配置（只看公开行情）'],
        ['关注交易对', s.focus.symbol],
        ['监控交易对', (s.symbols || []).length + ' 个']];
      case 'engine': return [
        ['状态', s.source === 'demo' ? '演示模式' : st(s.links.octobot.ok)],
        ['版本', s.links.octobot.version || '—'],
        ['运行时间', s.links.octobot.since || '—'],
        ['挂单', s.orders.length], ['历史成交', s.stats.tradeCount]];
      case 'strategy': {
        const p = s.profile || {};
        return [['配置', p.name || '—'], ['交易模式', p.tradingMode || '—'], ['市场类型', d.swap ? 'USDT 本位永续合约' : '现货'],
          ['挂单', s.orders.filter((o) => o.symbol.includes(':') === !!d.swap).length],
          ['交易对', (p.pairs || []).join(', ') || '—'], ['资金', s.mode === 'live' ? '<span class="neg">实盘</span>' : s.mode === 'paper' ? '模拟盘' : s.mode === 'demo' ? '演示' : '—']];
      }
      case 'market': {
        const list = (s.symbols || []).filter((x) => (d.type === 'swap') === x.includes(':'));
        return [['类型', d.type === 'swap' ? 'USDT 本位永续合约' : '现货'], ['交易对', list.join(', ')],
          ['挂单', s.orders.filter((o) => (d.type === 'swap') === o.symbol.includes(':')).length]];
      }
      case 'symbol': {
        const sym = d.symbol;
        const os = s.orders.filter((o) => o.symbol === sym);
        const cl = (s.closed || []).filter((c) => c.symbol === sym);
        const pnl = cl.reduce((a, c) => a + c.pnl, 0);
        const isFocus = s.focus.symbol === sym && s.ticker;
        return [['最新价', isFocus ? A.fmtPx(s.ticker.last) : '切换关注后显示'], ['24H 涨跌', isFocus ? `<span class="${A.signCls(s.ticker.changePct)}">${A.fmtPct(s.ticker.changePct)}</span>` : '—'],
          ['买单 / 卖单', `${os.filter((o) => o.side === 'buy').length} / ${os.filter((o) => o.side === 'sell').length}`],
          ['平仓笔数', cl.length], ['已实现盈亏', cl.length ? `<span class="${A.signCls(pnl)}">${A.fmtSigned(pnl)}</span>` : '—']];
      }
      case 'result': {
        const os = s.orders.filter((o) => o.symbol === d.symbol);
        const px = s.focus.symbol === d.symbol && s.ticker ? s.ticker.last : null;
        const nb = px ? os.filter((o) => o.side === 'buy').sort((a, b) => b.price - a.price)[0] : null;
        const ns = px ? os.filter((o) => o.side === 'sell').sort((a, b) => a.price - b.price)[0] : null;
        const p = d.pos;
        const rows = [['交易对', d.symbol], ['买单 / 卖单', `${d.nb} / ${d.ns}`],
          ['最近的买单', nb ? `${A.fmtPx(nb.price)} <span class="pos">${A.fmtPct((nb.price / px - 1) * 100)}</span>` : '—'],
          ['最近的卖单', ns ? `${A.fmtPx(ns.price)} <span class="neg">${A.fmtPct((ns.price / px - 1) * 100)}</span>` : '—'],
          ['近 1 小时成交', d.nt + ' 笔'], ['近 1 小时已实现', d.ncl ? `<span class="${A.signCls(d.rp)}">${A.fmtSigned(d.rp)}</span>` : '—']];
        if (p) rows.push(['持仓', `${p.side === 'short' ? '空' : '多'} ${A.fmtAmt(p.size)} @ ${A.fmtPx(p.entry)}`], ['未实现盈亏', `<span class="${A.signCls(p.upnl)}">${A.fmtSigned(p.upnl)}</span>`]);
        return rows;
      }
      case 'order': {
        const o = d.o; const px = s.focus.symbol === o.symbol && s.ticker ? s.ticker.last : null;
        return [['方向', o.side === 'buy' ? '<span class="pos">买入</span>' : '<span class="neg">卖出</span>'], ['类型', o.type], ['价格', A.fmtPx(o.price)],
          ['数量', A.fmtAmt(o.amount)], ['距现价', px ? A.fmtPct((o.price / px - 1) * 100) : '—'], ['来源', o.source], ['创建时间', o.time ? A.mdhm(o.time) : '—']];
      }
      case 'position': {
        const p = d.p;
        return [['方向', p.side === 'short' ? '空' : '多'], ['数量', A.fmtAmt(p.size)], ['开仓均价', A.fmtPx(p.entry)], ['标记价', A.fmtPx(p.mark)],
          ['未实现盈亏', `<span class="${A.signCls(p.upnl)}">${A.fmtSigned(p.upnl)}</span>`], ['强平价', p.liq ? A.fmtPx(p.liq) : '—'],
          ['保证金', A.fmt(p.margin)], ['杠杆', p.leverage ? p.leverage + 'x' : '—']];
      }
      case 'fill': {
        const t = d.t;
        return [['方向', t.side === 'buy' ? '买入' : '卖出'], ['价格', A.fmtPx(t.price)], ['数量', A.fmtAmt(t.amount)], ['手续费', A.fmt(t.fee, 4)], ['时间', t.time ? A.mdhm(t.time) : '—'], ['来源', t.source]];
      }
    }
    return [];
  }

  function renderInspector() {
    const n = G.nodes.get(G.sel);
    if (!n) return;
    A.$('inspTitle').textContent = n.type === 'result' ? n.data.symbol + ' · 结果' : n.title;
    A.$('inspType').innerHTML = `<span class="tag" style="color:${TYPES[n.type].color};border-color:${A.alpha(TYPES[n.type].color, 0.45)}">${TYPES[n.type].name}</span>` +
      (n.data.kind === 'symbol' && G.s.focus.symbol !== n.data.symbol ? `<button class="tag" type="button" id="inspFocus" style="cursor:pointer;background:none">设为关注</button>` : '');
    const btn = A.$('inspFocus');
    if (btn) btn.onclick = () => A.feed.setFocus(n.data.symbol);
    A.$('inspKv').innerHTML = rowsFor(n).map(([k, v]) => `<dt>${A.esc(k)}</dt><dd>${typeof v === 'number' ? v : v}</dd>`).join('');
    const nb = neighbors(n.id);
    A.$('inspN').innerHTML = nb.slice(0, 30).map((x) => {
      const m = G.nodes.get(x.id);
      const lab = (q) => (q.type === 'result' ? '结果' : q.title); const left = x.dir > 0 ? lab(n) : lab(m), right = x.dir > 0 ? lab(m) : lab(n);
      return `<div class="nrow"><span>${A.esc(left)}</span><em>→ ${A.esc(x.rel)} →</em><span>${A.esc(right)}</span></div>`;
    }).join('') || '<div class="empty">没有相邻节点</div>';
    G.radial = { id: n.id };
  }

  // neighbour ring in the inspector: turns slowly clockwise
  function drawRadial(n, nb) {
    const { ctx, w, h } = A.prep(A.$('inspRadial'));
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, R = Math.min(w, h) / 2 - 12;
    ctx.strokeStyle = C.line2; ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    nb.forEach((x, i) => {
      const m = G.nodes.get(x.id); if (!m) return;
      const a = (i / Math.max(1, nb.length)) * Math.PI * 2 - Math.PI / 2 + (A.reduceMotion ? 0 : G.t * 0.25);
      const px = cx + Math.cos(a) * R, py = cy + Math.sin(a) * R;
      ctx.strokeStyle = A.alpha(C.ink, 0.15); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(px, py); ctx.stroke();
      ctx.fillStyle = TYPES[m.type].color; ctx.beginPath(); ctx.arc(px, py, 5, 0, Math.PI * 2); ctx.fill();
    });
    const T = TYPES[n.type];
    const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, 22);
    grd.addColorStop(0, A.alpha(T.color, 0.45)); grd.addColorStop(1, A.alpha(T.color, 0));
    ctx.fillStyle = grd; ctx.beginPath(); ctx.arc(cx, cy, 22, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = T.color; ctx.beginPath(); ctx.arc(cx, cy, 6, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = C.ink3; ctx.font = `9.5px ${C.mono}`; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(`${nb.length} 个相邻节点`, 2, 2);
  }
})();
